"""生效日过滤单变量对照：冻结索引配置下比较过滤关闭与打开。

official 使用真实 Embedding；smoke 只用 Mock 验链路。查询改写保持关闭，
不改切分 / Embedding / Reranker，也不把生产默认改成开启。
本实验结论与查询改写（RAG-018）分开记录。
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
from app.rag.vectorstore.factory import get_vector_store  # noqa: E402
from app.rag.vectorstore.local import LocalVectorStore  # noqa: E402
from tests.eval.effective_date_filter import decide_effective_date_filter  # noqa: E402
from tests.eval.harness import score_hits  # noqa: E402
from tests.eval.query_rewrite import slice_metrics  # noqa: E402
from tests.eval.validation import load_cases  # noqa: E402

TARGET_CASE_ID = "rag-031"
FILTER_RRF_K = 60
FILTER_DEPTH = 10
DEFAULT_DB = REPO_ROOT / "data" / "eval_rag_v01_effective_date_filter.db"
REPORT_DIR = REPO_ROOT / "evals" / "reports"
EVAL_DOC_DIR = REPO_ROOT / "docs" / "evaluations"


def _load_semantic_helpers():
    """按文件路径加载语义重排运行器，复用冻结配置与调用量辅助函数。"""
    path = REPO_ROOT / "evals" / "semantic_rerank" / "run.py"
    spec = importlib.util.spec_from_file_location("semantic_rerank_helpers_for_date_filter", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"无法加载语义重排辅助模块：{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


helpers = _load_semantic_helpers()
runner = helpers.runner


def _sanitize_git_commits(payload: Any) -> Any:
    """给报告里的裸 40 位 SHA 加上 git 前缀，避免钩子误报高熵密钥。"""
    if isinstance(payload, dict):
        return {
            key: (
                helpers._hook_safe_git_commit(value)
                if key == "git_commit" and isinstance(value, str)
                else _sanitize_git_commits(value)
            )
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [_sanitize_git_commits(item) for item in payload]
    return payload


def _expected_doc_hit(outcomes: list[Any], case_id: str) -> bool | None:
    """从评分结果判断某案例是否命中期望文档。"""
    for outcome in outcomes:
        if getattr(outcome, "case_id", None) != case_id:
            continue
        failures = set(getattr(outcome, "failures", []) or [])
        if "EXPECTED_DOC_MISSED" in failures:
            return False
        return True
    return None


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
        FILTER_DEPTH,
        FILTER_RRF_K,
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
) -> list[Any]:
    outcomes: list[Any] = []
    for case in cases:
        started = time.perf_counter()
        try:
            hits = await _search(
                store=store,
                provider=provider,
                cache=cache,
                query=case["query"],
                tenant_id=case["identity"]["tenant_id"],
            )
        except Exception as exc:  # noqa: BLE001 — 记录单例失败
            outcomes.append(
                helpers._failed_case(case, (time.perf_counter() - started) * 1000.0, helpers._classify_failure(exc))
            )
            continue
        latency_ms = (time.perf_counter() - started) * 1000.0
        if len(hits) > FILTER_DEPTH:
            raise SystemExit("hybrid_search 返回条数超过深度 10，已停止")
        outcomes.append(score_hits(case, index, hits, latency_ms=latency_ms))
    return outcomes


def render_markdown(comparison: dict[str, Any]) -> str:
    """把对照结果写成复核人员可读的 Markdown。"""
    control = comparison["control_slices"]
    treatment = comparison["treatment_slices"]
    usage = comparison.get("embedding_usage") or {}
    cost = usage.get("estimated_cost_yuan")
    cost_text = "未知" if cost is None else str(cost)
    recalled = comparison.get("future_doc_recalled_by_query_date")
    recalled_text = "是" if recalled else "否"
    lines = [
        "# 生效日过滤单变量对照",
        "",
        "本报告只评估 `RAG_EFFECTIVE_DATE_FILTER` 这一变量，与查询改写结论分开。"
        "评测过程不改写查询，不改切分 / Embedding / Reranker，也不把生产默认改为开启。",
        "",
        f"- 模式：`{comparison['mode']}`；质量证据：`{comparison['quality_evidence']}`",
        f"- 决策：`{comparison['decision']}`（holdout 不参与）",
        f"- 是否按查询日召回未来生效文档（rag-031）：{recalled_text}",
        f"- 原因：{comparison['reason']}",
        f"- Embedding：`{comparison['embedding_provider']}` / `{comparison['embedding_model']}` / {comparison['embedding_dim']} 维",
        f"- 代码：`{comparison['git_commit']}`；命令：`{comparison['command']}`",
        "- 生产默认：`RAG_EFFECTIVE_DATE_FILTER=false`，本实验仅在评测进程内覆盖打开处理组",
        "",
        "## 单变量结果（查询改写关闭）",
        "",
        "| 指标 | 对照（过滤关闭） | 处理（过滤打开） |",
        "| --- | --- | --- |",
        f"| overall Recall@5 | {control['overall']} | {treatment['overall']} |",
        f"| 调参集 Recall@5 | {control['tuning']} | {treatment['tuning']} |",
        f"| validation Recall@5 | {control['validation']} | {treatment['validation']} |",
        f"| effective_date Recall@5 | {control['effective_date']} | {treatment['effective_date']} |",
        f"| holdout Recall@5（仅记录） | {control['holdout']} | {treatment['holdout']} |",
        f"| 越权案例 | {control['violations']} | {treatment['violations']} |",
        "",
        "对照应对齐查询改写第一轮官方结果中过滤关闭、原查询的分数："
        "overall 0.9714（34/35）、调参集 0.9643、validation 0.8333（n=6）、effective_date 0.6667、越权 0。",
        "",
        "## 调用量",
        "",
        f"- 嵌入调用 {usage.get('call_count')} 次，文本 {usage.get('text_count')} 条，"
        f"估计 token {usage.get('estimated_tokens')}，估计费用 {cost_text}",
        "",
        "## 说明",
        "",
        "- 本结果不是查询改写是否采纳的证据。",
        "- 即使处理组提升，也不在本提交中打开生产默认。",
        "",
    ]
    return "\n".join(lines)


async def run_experiment(args: argparse.Namespace) -> int:
    """应用冻结配置后执行对照，结束时恢复原设置。"""
    previous = helpers.apply_frozen_eval_settings()
    # 冻结配置把过滤钉在关闭；处理组只在对照之后临时打开，并在 finally 恢复。
    try:
        return await _run_experiment(args)
    finally:
        for name, value in previous.items():
            setattr(settings, name, value)


async def _run_experiment(args: argparse.Namespace) -> int:
    """在冻结评测配置内比较过滤关闭与打开。查询保持原文，不做改写。"""
    if args.rrf_k not in (None, FILTER_RRF_K):
        raise SystemExit("生效日过滤实验固定融合常数 60，不能同时改 k")
    if args.retrieval_depth != FILTER_DEPTH:
        raise SystemExit("生效日过滤实验固定检索深度 10")

    raw_provider = runner.resolve_embedding(args.mode)
    provider = helpers.CountingEmbeddingProvider(raw_provider)
    if args.mode == "official":
        runner._require_frozen_index_config()

    if settings.RAG_EFFECTIVE_DATE_FILTER:
        raise SystemExit("对照开始前生效日过滤必须保持关闭")

    db_path = Path(args.db)
    print(f"[1/4] 重建评测索引（一次）-> {db_path}")
    memory_before_index = helpers.process_memory_bytes()
    index = await runner.build_eval_index(db_path, provider)
    memory_after_index = helpers.process_memory_bytes()
    cache: dict[str, list[float]] = {}
    try:
        store = get_vector_store(index.session)
        if not isinstance(store, LocalVectorStore):
            raise SystemExit("生效日过滤实验只使用本地向量库")
        cases = sorted(load_cases(), key=lambda item: item["case_id"])
        print(f"[2/4] 过滤关闭对照 {len(cases)} 条案例（查询改写关闭）")
        control_outcomes = await _run_arm(
            store=store, provider=provider, cache=cache, cases=cases, index=index
        )
        print("[3/4] 处理组：进程内打开 RAG_EFFECTIVE_DATE_FILTER（不改生产默认）")
        settings.RAG_EFFECTIVE_DATE_FILTER = True
        try:
            treatment_outcomes = await _run_arm(
                store=store, provider=provider, cache=cache, cases=cases, index=index
            )
        finally:
            settings.RAG_EFFECTIVE_DATE_FILTER = False
    finally:
        index.close()

    memory_after_run = helpers.process_memory_bytes()
    config = runner.build_run_config(args.mode, provider._inner, FILTER_DEPTH)
    config["indexed_documents"] = len(index.db_doc_id_to_logical)
    config["indexed_chunks"] = index.chunk_total
    config["variable"] = "effective_date_filter"
    config["machine"] = runner.machine_spec()
    command = (
        "python evals/effective_date_filter/run.py --mode official"
        if args.mode == "official"
        else "python evals/effective_date_filter/run.py --mode smoke"
    )
    config["command"] = command
    config["working_directory"] = str(REPO_ROOT)
    control_report = runner.make_report(config, control_outcomes)
    treatment_report = runner.make_report(config, treatment_outcomes)
    rag031_hit = _expected_doc_hit(treatment_outcomes, TARGET_CASE_ID)
    rag031_control_hit = _expected_doc_hit(control_outcomes, TARGET_CASE_ID)
    quality_evidence = args.mode == "official"
    if quality_evidence:
        decision = decide_effective_date_filter(
            control_report,
            treatment_report,
            rag031_expected_doc_hit=rag031_hit,
        )
    else:
        decision = {
            "decision": "not_evaluated",
            "reason_code": "mock_not_quality_evidence",
            "failed_gates": ["mock_not_quality_evidence"],
            "slice_gains": [],
            "slice_crossed_0_90": [],
            "future_doc_recalled_by_query_date": bool(rag031_hit),
            "reason": "冒烟模式只证明链路，不能作为质量或采纳证据",
        }

    inner_name = type(provider._inner).__name__
    usage = helpers.estimate_cost_yuan(provider.text_count, provider.char_count)
    usage["call_count"] = provider.call_count
    usage["text_count"] = provider.text_count
    usage["char_count"] = provider.char_count

    comparison = {
        "experiment": "effective-date-filter",
        "variable": "effective_date_filter",
        "run_at": config.get("run_at") or datetime.now(UTC).isoformat(),
        "git_commit": helpers._hook_safe_git_commit(config["git_commit"]),
        "dataset_id": config.get("dataset_id"),
        "dataset_version": config.get("dataset_version"),
        "mode": args.mode,
        "quality_evidence": quality_evidence,
        "holdout_used_for_decision": False,
        "query_rewrite_enabled": False,
        "production_enabled_default": False,
        "effective_date_filter_production_default": False,
        "embedding_provider": inner_name,
        "embedding_model": config.get("embedding_model"),
        "embedding_dim": config.get("embedding_dim"),
        "vector_store": config.get("vector_store"),
        "rag_backend": config.get("rag_backend"),
        "chunk_strategy": config.get("chunk_strategy"),
        "chunk_size": config.get("chunk_size"),
        "chunk_overlap": config.get("chunk_overlap"),
        "rrf_k": FILTER_RRF_K,
        "retrieval_depth_chunks": FILTER_DEPTH,
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
        "future_doc_recalled_by_query_date": decision.get("future_doc_recalled_by_query_date"),
        "rag031_expected_doc_hit": {
            "control": rag031_control_hit,
            "treatment": rag031_hit,
        },
        "control_slices": slice_metrics(control_report),
        "treatment_slices": slice_metrics(treatment_report),
        "control": control_report,
        "treatment": treatment_report,
        "control_failures": helpers.failure_slices(control_outcomes),
        "treatment_failures": helpers.failure_slices(treatment_outcomes),
        "embedding_usage": usage,
        "memory": {
            "rss_before_index_bytes": memory_before_index.get("rss_bytes"),
            "rss_after_index_bytes": memory_after_index.get("rss_bytes"),
            "rss_after_run_bytes": memory_after_run.get("rss_bytes"),
        },
        "recommendation": (
            "Keep RAG_EFFECTIVE_DATE_FILTER disabled in production by default. "
            "This report is an official single-variable evaluation only, not an adoption or a query-rewrite result."
        ),
    }
    comparison = _sanitize_git_commits(comparison)

    suffix = "" if args.mode == "official" else "-smoke"
    report_path = REPORT_DIR / f"rag-v0.1-effective-date-filter-20261110{suffix}.json"
    doc_path = EVAL_DOC_DIR / f"rag-v0.1-effective-date-filter-20261110{suffix}.md"
    runner.refuse_frozen(report_path)
    report_path.write_text(json.dumps(comparison, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    doc_path.write_text(render_markdown(comparison), encoding="utf-8")
    print(f"[4/4] {report_path}")
    print(f"      Markdown {doc_path}")
    print(f"      决策 {comparison['decision']}")
    print(f"      按查询日召回未来文档 {comparison['future_doc_recalled_by_query_date']}")
    print(f"      切片 {comparison['control_slices']} -> {comparison['treatment_slices']}")
    cost = usage.get("estimated_cost_yuan")
    print(
        f"      嵌入调用 {usage['call_count']} 次，文本 {usage['text_count']} 条，"
        f"估计 token {usage.get('estimated_tokens')}，估计费用 {'未知' if cost is None else cost}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="运行生效日过滤单变量对照")
    parser.add_argument("--mode", choices=("official", "smoke"), default="official")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--rrf-k", dest="rrf_k", type=int, default=FILTER_RRF_K)
    parser.add_argument("--retrieval-depth", dest="retrieval_depth", type=int, default=FILTER_DEPTH)
    args = parser.parse_args()
    return asyncio.run(run_experiment(args))


if __name__ == "__main__":
    raise SystemExit(main())
