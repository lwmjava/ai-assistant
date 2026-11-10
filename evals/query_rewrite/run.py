"""查询改写单变量对照：冻结索引配置下比较原查询与改写查询。

official 使用真实 Embedding；smoke 只用 Mock 验链路。可选诊断会在同一索引上
临时打开生效日过滤，该结果单独标注，不作为单变量改写结论。
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.config import settings  # noqa: E402
from app.rag.effective_date import retrieval_window  # noqa: E402
from app.rag.embeddings.mock import tokenize  # noqa: E402
from app.rag.query_rewrite import rewrite_query  # noqa: E402
from app.rag.vectorstore.factory import get_vector_store  # noqa: E402
from app.rag.vectorstore.local import LocalVectorStore  # noqa: E402
from tests.eval.harness import score_hits  # noqa: E402
from tests.eval.query_rewrite import decide_query_rewrite, slice_metrics  # noqa: E402
from tests.eval.validation import load_cases  # noqa: E402


def _load_semantic_helpers():
    """按文件路径加载语义重排运行器，复用冻结配置与调用量辅助函数。"""
    path = REPO_ROOT / "evals" / "semantic_rerank" / "run.py"
    spec = importlib.util.spec_from_file_location("semantic_rerank_helpers_for_query_rewrite", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"无法加载语义重排辅助模块：{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


helpers = _load_semantic_helpers()
runner = helpers.runner

REWRITE_RRF_K = 60
REWRITE_DEPTH = 10
DEFAULT_DB = REPO_ROOT / "data" / "eval_rag_v01_query_rewrite.db"
REPORT_DIR = REPO_ROOT / "evals" / "reports"
EVAL_DOC_DIR = REPO_ROOT / "docs" / "evaluations"


async def _search(
    *,
    store: LocalVectorStore,
    provider: Any,
    cache: dict[str, list[float]],
    query: str,
    tenant_id: str,
) -> list[Any]:
    if query not in cache:
        cache[query] = (await provider.embed([query]))[0]
    as_of, schedule_at = retrieval_window(query)
    return await store.hybrid_search(
        cache[query],
        tokenize(query),
        tenant_id,
        REWRITE_DEPTH,
        REWRITE_RRF_K,
        as_of=as_of,
        schedule_at=schedule_at,
    )


async def _run_arm(
    *,
    store: LocalVectorStore,
    provider: Any,
    cache: dict[str, list[float]],
    cases: list[dict[str, Any]],
    index: Any,
    rewrite: bool,
) -> list[Any]:
    outcomes: list[Any] = []
    for case in cases:
        original = case["query"]
        query = rewrite_query(original).text if rewrite else original
        started = time.perf_counter()
        try:
            hits = await _search(
                store=store,
                provider=provider,
                cache=cache,
                query=query,
                tenant_id=case["identity"]["tenant_id"],
            )
        except Exception as exc:  # noqa: BLE001 — 记录单例失败
            outcomes.append(
                helpers._failed_case(case, (time.perf_counter() - started) * 1000.0, helpers._classify_failure(exc))
            )
            continue
        latency_ms = (time.perf_counter() - started) * 1000.0
        if len(hits) > REWRITE_DEPTH:
            raise SystemExit("hybrid_search 返回条数超过深度 10，已停止")
        outcomes.append(score_hits(case, index, hits, latency_ms=latency_ms))
    return outcomes


def render_markdown(comparison: dict[str, Any]) -> str:
    """把对照结果写成复核人员可读的 Markdown。"""
    control = comparison["control_slices"]
    treatment = comparison["treatment_slices"]
    diagnostic = comparison.get("diagnostic_effective_date_filter")
    usage = comparison.get("embedding_usage") or {}
    cost = usage.get("estimated_cost_yuan")
    cost_text = "未知" if cost is None else str(cost)
    lines = [
        "# 查询改写单变量对照",
        "",
        f"- 模式：`{comparison['mode']}`；质量证据：`{comparison['quality_evidence']}`",
        f"- 决策：`{comparison['decision']}`（holdout 不参与）",
        f"- 原因：{comparison['reason']}",
        "- 改写：确定性扩展，不调用聊天模型；生产路径默认关闭，可用 `RAG_QUERY_REWRITE_ENABLED=true` 开启",
        f"- Embedding：`{comparison['embedding_provider']}` / `{comparison['embedding_model']}` / {comparison['embedding_dim']} 维",
        f"- 代码：`{comparison['git_commit']}`；命令：`{comparison['command']}`",
        "- 进入条件（已落盘）：任一切片 Recall@5 跌破 0.90 即实施。AGENTS.md §14 仍写 overall 口径，本卡未改该文件。",
        "",
        "## 冻结配置下的单变量结果（RAG_EFFECTIVE_DATE_FILTER=false）",
        "",
        "| 指标 | 对照（原查询） | 处理（改写） |",
        "| --- | --- | --- |",
        f"| overall Recall@5 | {control['overall']} | {treatment['overall']} |",
        f"| 调参集 Recall@5 | {control['tuning']} | {treatment['tuning']} |",
        f"| validation Recall@5 | {control['validation']} | {treatment['validation']} |",
        f"| effective_date Recall@5 | {control['effective_date']} | {treatment['effective_date']} |",
        f"| holdout Recall@5（仅记录） | {control['holdout']} | {treatment['holdout']} |",
        f"| 越权案例 | {control['violations']} | {treatment['violations']} |",
        "",
        "官方冻结配置只检索 `is_current` 文档。`doc-billing-future` 属于 scheduled 预告，"
        "在此配置下不会进入候选集；改写单独无法召回 rag-031，这是已知限制，不是把过滤开关偷偷打开。",
        "",
    ]
    if diagnostic:
        d_control = diagnostic["control_slices"]
        d_treatment = diagnostic["treatment_slices"]
        lines.extend(
            [
                "## 诊断：临时打开生效日过滤（不作为单变量改写结论，不改生产默认）",
                "",
                "| 指标 | 过滤开 + 原查询 | 过滤开 + 改写 |",
                "| --- | --- | --- |",
                f"| overall Recall@5 | {d_control['overall']} | {d_treatment['overall']} |",
                f"| 调参集 Recall@5 | {d_control['tuning']} | {d_treatment['tuning']} |",
                f"| validation Recall@5 | {d_control['validation']} | {d_treatment['validation']} |",
                f"| effective_date Recall@5 | {d_control['effective_date']} | {d_treatment['effective_date']} |",
                f"| holdout Recall@5（仅记录） | {d_control['holdout']} | {d_treatment['holdout']} |",
                f"| 越权案例 | {d_control['violations']} | {d_treatment['violations']} |",
                "",
            ]
        )
    lines.extend(
        [
            "## 调用量",
            "",
            f"- 嵌入调用 {usage.get('call_count')} 次，文本 {usage.get('text_count')} 条，"
            f"估计 token {usage.get('estimated_tokens')}，估计费用 {cost_text}",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


async def run_experiment(args: argparse.Namespace) -> int:
    """应用冻结配置后执行对照，结束时恢复原设置。"""
    previous = helpers.apply_frozen_eval_settings()
    try:
        return await _run_experiment(args)
    finally:
        for name, value in previous.items():
            setattr(settings, name, value)


async def _run_experiment(args: argparse.Namespace) -> int:
    """在冻结评测配置内比较原查询与改写查询。"""
    if args.rrf_k not in (None, REWRITE_RRF_K):
        raise SystemExit("查询改写实验固定融合常数 60，不能同时改 k")
    if args.retrieval_depth != REWRITE_DEPTH:
        raise SystemExit("查询改写实验固定检索深度 10")

    raw_provider = runner.resolve_embedding(args.mode)
    provider = helpers.CountingEmbeddingProvider(raw_provider)
    if args.mode == "official":
        runner._require_frozen_index_config()

    db_path = Path(args.db)
    print(f"[1/4] 重建评测索引（一次）-> {db_path}")
    memory_before_index = helpers.process_memory_bytes()
    index = await runner.build_eval_index(db_path, provider)
    memory_after_index = helpers.process_memory_bytes()
    cache: dict[str, list[float]] = {}
    diag_control = None
    diag_treatment = None
    try:
        store = get_vector_store(index.session)
        if not isinstance(store, LocalVectorStore):
            raise SystemExit("查询改写实验只使用本地向量库")
        cases = sorted(load_cases(), key=lambda item: item["case_id"])
        print(f"[2/4] 冻结配置下对照 {len(cases)} 条案例")
        control_outcomes = await _run_arm(
            store=store, provider=provider, cache=cache, cases=cases, index=index, rewrite=False
        )
        treatment_outcomes = await _run_arm(
            store=store, provider=provider, cache=cache, cases=cases, index=index, rewrite=True
        )

        print("[3/4] 诊断：临时打开生效日过滤（不改生产默认，不作为单变量结论）")
        settings.RAG_EFFECTIVE_DATE_FILTER = True
        try:
            diag_control = await _run_arm(
                store=store, provider=provider, cache=cache, cases=cases, index=index, rewrite=False
            )
            diag_treatment = await _run_arm(
                store=store, provider=provider, cache=cache, cases=cases, index=index, rewrite=True
            )
        finally:
            settings.RAG_EFFECTIVE_DATE_FILTER = False
    finally:
        index.close()

    memory_after_run = helpers.process_memory_bytes()
    config = runner.build_run_config(args.mode, provider._inner, REWRITE_DEPTH)
    config["indexed_documents"] = len(index.db_doc_id_to_logical)
    config["indexed_chunks"] = index.chunk_total
    config["variable"] = "query_rewrite"
    config["machine"] = runner.machine_spec()
    command = (
        "python evals/query_rewrite/run.py --mode official"
        if args.mode == "official"
        else "python evals/query_rewrite/run.py --mode smoke"
    )
    config["command"] = command
    config["working_directory"] = str(REPO_ROOT)
    control_report = runner.make_report(config, control_outcomes)
    treatment_report = runner.make_report(config, treatment_outcomes)
    quality_evidence = args.mode == "official"
    if quality_evidence:
        decision = decide_query_rewrite(control_report, treatment_report)
    else:
        decision = {
            "decision": "not_evaluated",
            "reason_code": "mock_not_quality_evidence",
            "failed_gates": ["mock_not_quality_evidence"],
            "slice_gains": [],
            "slice_crossed_0_90": [],
            "reason": "冒烟模式只证明链路，不能作为质量或采纳证据",
        }

    diag_payload = None
    if diag_control is not None and diag_treatment is not None:
        d_control = runner.make_report(config, diag_control)
        d_treatment = runner.make_report(config, diag_treatment)
        diag_payload = {
            "note": "in-process RAG_EFFECTIVE_DATE_FILTER=true diagnostic only",
            "used_for_decision": False,
            "control": d_control,
            "treatment": d_treatment,
            "control_slices": slice_metrics(d_control),
            "treatment_slices": slice_metrics(d_treatment),
        }

    inner_name = type(provider._inner).__name__
    usage = helpers.estimate_cost_yuan(provider.text_count, provider.char_count)
    usage["call_count"] = provider.call_count
    usage["text_count"] = provider.text_count
    usage["char_count"] = provider.char_count

    comparison = {
        "experiment": "query-rewrite",
        "variable": "query_rewrite",
        "run_at": config.get("run_at") or datetime.now(UTC).isoformat(),
        "git_commit": helpers._hook_safe_git_commit(config["git_commit"]),
        "dataset_id": config.get("dataset_id"),
        "dataset_version": config.get("dataset_version"),
        "mode": args.mode,
        "quality_evidence": quality_evidence,
        "holdout_used_for_decision": False,
        "production_enabled_default": False,
        "effective_date_filter_for_decision": False,
        "embedding_provider": inner_name,
        "embedding_model": config.get("embedding_model"),
        "embedding_dim": config.get("embedding_dim"),
        "vector_store": config.get("vector_store"),
        "rag_backend": config.get("rag_backend"),
        "chunk_strategy": config.get("chunk_strategy"),
        "chunk_size": config.get("chunk_size"),
        "chunk_overlap": config.get("chunk_overlap"),
        "rrf_k": REWRITE_RRF_K,
        "retrieval_depth_chunks": REWRITE_DEPTH,
        "indexed_documents": config["indexed_documents"],
        "indexed_chunks": config["indexed_chunks"],
        "machine": config["machine"],
        "package_versions": helpers.package_versions(),
        "command": command,
        "working_directory": str(REPO_ROOT),
        "decision": decision["decision"],
        "reason_code": decision["reason_code"],
        "reason": decision["reason"],
        "failed_gates": decision.get("failed_gates") or [],
        "slice_gains": decision.get("slice_gains") or [],
        "slice_crossed_0_90": decision.get("slice_crossed_0_90") or [],
        "control_slices": slice_metrics(control_report),
        "treatment_slices": slice_metrics(treatment_report),
        "control": control_report,
        "treatment": treatment_report,
        "control_failures": helpers.failure_slices(control_outcomes),
        "treatment_failures": helpers.failure_slices(treatment_outcomes),
        "diagnostic_effective_date_filter": diag_payload,
        "embedding_usage": usage,
        "memory": {
            "rss_before_index_bytes": memory_before_index.get("rss_bytes"),
            "rss_after_index_bytes": memory_after_index.get("rss_bytes"),
            "rss_after_run_bytes": memory_after_run.get("rss_bytes"),
        },
    }

    suffix = "" if args.mode == "official" else "-smoke"
    report_path = REPORT_DIR / f"rag-v0.1-query-rewrite-20261110{suffix}.json"
    doc_path = EVAL_DOC_DIR / f"rag-v0.1-query-rewrite-20261110{suffix}.md"
    runner.refuse_frozen(report_path)
    report_path.write_text(json.dumps(comparison, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    doc_path.write_text(render_markdown(comparison), encoding="utf-8")
    print(f"[4/4] {report_path}")
    print(f"      Markdown {doc_path}")
    print(f"      决策 {comparison['decision']}")
    print(f"      切片 {comparison['control_slices']} -> {comparison['treatment_slices']}")
    cost = usage.get("estimated_cost_yuan")
    print(
        f"      嵌入调用 {usage['call_count']} 次，文本 {usage['text_count']} 条，"
        f"估计 token {usage.get('estimated_tokens')}，估计费用 {'未知' if cost is None else cost}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="运行查询改写单变量对照")
    parser.add_argument("--mode", choices=("official", "smoke"), default="official")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--rrf-k", dest="rrf_k", type=int, default=REWRITE_RRF_K)
    parser.add_argument("--retrieval-depth", dest="retrieval_depth", type=int, default=REWRITE_DEPTH)
    args = parser.parse_args()
    return asyncio.run(run_experiment(args))


if __name__ == "__main__":
    raise SystemExit(main())
