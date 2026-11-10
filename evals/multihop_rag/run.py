"""MultiHop-RAG 补充检索对照。

这是补充对照，不替代官方 35 条质量门槛，不改官方评估配置、holdout
决策规则或生产默认开关。official 使用真实 Embedding；smoke 只用 Mock
验链路。查询改写与生效日过滤在本评测进程内保持关闭。
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.config import settings  # noqa: E402
from app.rag.ingestion import split_text_structured  # noqa: E402
from app.rag.service import RAGService  # noqa: E402
from app.rag.vectorstore.factory import get_vector_store  # noqa: E402
from app.rag.vectorstore.local import LocalVectorStore  # noqa: E402
from tests.eval.harness import CaseOutcome, EvalIndex, score_hits, summarize, group_summaries  # noqa: E402

CONVERT_PATH = Path(__file__).resolve().parent / "convert.py"
DEFAULT_OFFICIAL_DB = REPO_ROOT / "data" / "eval_multihop_rag.db"
DEFAULT_SMOKE_DB = REPO_ROOT / "data" / "eval_multihop_rag_smoke.db"
REPORT_DIR = REPO_ROOT / "evals" / "reports"
EVAL_DOC_DIR = REPO_ROOT / "docs" / "evaluations"
RETRIEVAL_DEPTH = 10
RETRIEVAL_RRF_K = 60
BUDGET_YUAN = 1.0
STOP_YUAN = 2.0
# 阿里云百炼 text-embedding-v3 北京公开标价，仅作预算闸门，不是账单。
PUBLIC_REF_YUAN_PER_MTOK = 0.5
USER_TZ = ZoneInfo("Asia/Shanghai")
PROTECTED_REPORTS = {
    (REPORT_DIR / "rag-v0.1-baseline-20260919.json").resolve(),
    (REPORT_DIR / "rag-v0.1-query-rewrite-20261110.json").resolve(),
    (REPORT_DIR / "rag-v0.1-query-rewrite-gate-20261110.json").resolve(),
    (REPORT_DIR / "rag-v0.1-effective-date-filter-20261110.json").resolve(),
}


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"无法加载模块：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


helpers = _load_module(REPO_ROOT / "evals" / "semantic_rerank" / "run.py", "semantic_rerank_helpers_for_multihop")
runner = helpers.runner
convert = _load_module(CONVERT_PATH, "multihop_rag_convert")


def _sanitize_git_commits(payload: Any) -> Any:
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


def _shanghai(ts: str | None) -> str:
    if not ts:
        return ""
    try:
        value = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts
    return value.astimezone(USER_TZ).strftime("%Y-%m-%d %H:%M:%S Asia/Shanghai")


def _force_eval_flags_off() -> None:
    """评测进程内关闭查询改写与生效日过滤，不改配置文件或生产默认。"""
    os.environ["RAG_QUERY_REWRITE_ENABLED"] = "false"
    settings.RAG_EFFECTIVE_DATE_FILTER = False
    settings.RAG_HYBRID_RRF_K = RETRIEVAL_RRF_K


def _refuse_protected(path: Path) -> None:
    resolved = path.resolve()
    runner.refuse_frozen(resolved)
    if resolved in PROTECTED_REPORTS:
        raise SystemExit(f"拒绝覆盖官方 35 条相关报告：{resolved}")


def estimate_texts(texts: Iterable[str]) -> dict[str, Any]:
    """按项目既有方法估计 token 与费用（UTF-8 字节数 / 4）。"""
    items = list(texts)
    char_count = sum(len(item) for item in items)
    usage = helpers.estimate_cost_yuan(len(items), char_count)
    usage["text_count"] = len(items)
    usage["char_count"] = char_count
    configured = usage.get("estimated_cost_yuan")
    budget_tokens = usage["estimated_tokens"]
    usage["budget_check_yuan_per_mtok"] = (
        settings.EVALUATION_EMBEDDING_YUAN_PER_MTOK
        if settings.EVALUATION_EMBEDDING_YUAN_PER_MTOK is not None
        else PUBLIC_REF_YUAN_PER_MTOK
    )
    usage["budget_check_price_source"] = (
        "configured EVALUATION_EMBEDDING_YUAN_PER_MTOK"
        if settings.EVALUATION_EMBEDDING_YUAN_PER_MTOK is not None
        else "public DashScope text-embedding-v3 Beijing list price 0.5 yuan/MTok (budget gate only, not a bill)"
    )
    usage["budget_check_cost_yuan"] = float(usage["budget_check_yuan_per_mtok"]) * budget_tokens / 1_000_000
    usage["budget_yuan"] = BUDGET_YUAN
    usage["stop_yuan"] = STOP_YUAN
    if configured is None:
        usage["estimated_cost_yuan"] = None
    return usage


def collect_chunk_texts(manifest_documents: list[dict[str, Any]], output_dir: Path) -> list[str]:
    """用冻结的 structured 切分估计将被嵌入的块文本，不调用 Embedding。"""
    texts: list[str] = []
    for entry in manifest_documents:
        text = (output_dir / entry["source"]).read_text(encoding="utf-8")
        texts.extend(split_text_structured(text, settings.RAG_CHUNK_SIZE, settings.RAG_CHUNK_OVERLAP))
    return texts


async def build_multihop_index(
    db_path: Path,
    embedding: Any,
    manifest_documents: list[dict[str, Any]],
    output_dir: Path,
) -> EvalIndex:
    """在隔离 SQLite 中重建 MultiHop 语料索引，不触碰官方 35 条索引。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    from sqlmodel import Session, SQLModel, create_engine, select

    from app.models.rag import Document
    from app.rag.effective_date import parse_manifest_datetime

    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    services: dict[str, RAGService] = {}
    mapping: dict[str, str] = {}
    documents_by_tenant: dict[str, int] = {}

    for entry in manifest_documents:
        tenant_id = entry["tenant_id"]
        service = services.get(tenant_id)
        if service is None:
            service = RAGService(session, tenant_id, embedding_provider=embedding)
            services[tenant_id] = service
        text = (output_dir / entry["source"]).read_text(encoding="utf-8")
        document = await service.ingest_text(
            text,
            title=entry["title"],
            source=entry["source"],
            user_id=f"eval-owner-{tenant_id}",
            is_current=bool(entry["is_current"]),
            effective_at=parse_manifest_datetime(entry.get("effective_at")),
            strategy="structured",
        )
        mapping[document.id] = entry["document_id"]
        documents_by_tenant[tenant_id] = documents_by_tenant.get(tenant_id, 0) + 1

    chunk_total = sum(doc.chunk_count for doc in session.exec(select(Document)).all())
    return EvalIndex(
        session=session,
        db_doc_id_to_logical=mapping,
        chunk_total=chunk_total,
        documents_by_tenant=documents_by_tenant,
    )


async def run_cases(
    cases: list[dict[str, Any]],
    index: EvalIndex,
    embedding: Any,
) -> list[CaseOutcome]:
    outcomes: list[CaseOutcome] = []
    services: dict[str, RAGService] = {}
    for case in cases:
        tenant_id = case["identity"]["tenant_id"]
        service = services.get(tenant_id)
        if service is None:
            service = RAGService(index.session, tenant_id, embedding_provider=embedding)
            services[tenant_id] = service
        started = time.perf_counter()
        try:
            hits = await service.search(case["query"], top_k=RETRIEVAL_DEPTH)
        except Exception as exc:  # noqa: BLE001 — 记录单例失败
            outcomes.append(
                helpers._failed_case(case, (time.perf_counter() - started) * 1000.0, helpers._classify_failure(exc))
            )
            continue
        latency_ms = (time.perf_counter() - started) * 1000.0
        if len(hits) > RETRIEVAL_DEPTH:
            raise SystemExit("search 返回条数超过深度 10，已停止")
        outcomes.append(score_hits(case, index, hits, latency_ms=latency_ms))
    return outcomes


def _fmt(value: float | None, digits: int = 4) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def render_markdown(report: dict[str, Any]) -> str:
    overall = report["overall"]
    usage = report.get("embedding_usage") or {}
    pre = report.get("pre_run_estimate") or {}
    cost = usage.get("estimated_cost_yuan")
    cost_text = "未知" if cost is None else str(cost)
    pre_cost = pre.get("estimated_cost_yuan")
    pre_budget = pre.get("budget_check_cost_yuan")
    types = report.get("by_question_type") or {}
    type_rows = [
        f"| {name} | {summary['cases']} | {summary['scorable_cases']} | "
        f"{_fmt(summary['recall']['@1'])} | {_fmt(summary['recall']['@5'])} | "
        f"{_fmt(summary['recall']['@10'])} | {_fmt(summary['mrr'])} | {_fmt(summary['ndcg@10'])} |"
        for name, summary in types.items()
    ]
    if not type_rows:
        type_rows = ["| （无） |  |  |  |  |  |  |  |"]
    lines = [
        "# MultiHop-RAG 补充检索对照",
        "",
        "本报告是 **补充对照**，不替代官方 35 条质量门槛，也不作为查询改写或生效日过滤的采纳证据。",
        "官方 35 条评估配置、holdout 决策规则和生产默认开关均未改动。",
        "",
        f"- 模式：`{report['mode']}`；质量证据（官方 35 条门槛）：`{report['quality_evidence']}`；补充对照：`{report['supplementary_comparison']}`",
        f"- 运行时间：{_shanghai(report.get('run_at'))}",
        f"- Embedding：`{report['embedding_provider']}` / `{report['embedding_model']}` / {report['embedding_dim']} 维",
        f"- 代码：`{report['git_commit']}`；命令：`{report['command']}`",
        f"- 语料：{report['document_count']} 篇；问题：{report['question_count']} 条；可计分（非 null_query）：{report['scorable_question_count']} 条",
        f"- 检索：本地向量库 / native / structured chunk {report['chunk_size']}/{report['chunk_overlap']}，RRF k={report['rrf_k']}，深度 {report['retrieval_depth_chunks']} 块",
        "- 生产默认：`RAG_QUERY_REWRITE_ENABLED` 与 `RAG_EFFECTIVE_DATE_FILTER` 保持关闭；本评测未打开这两项",
        "",
        "## 数据集与许可",
        "",
        "- 数据集：MultiHop-RAG（Yixuan Tang, Yi Yang；COLM 2024）",
        "- 论文：https://arxiv.org/abs/2401.15391",
        "- GitHub：https://github.com/yixuantt/MultiHop-RAG",
        "- Hugging Face：https://huggingface.co/datasets/yixuantt/MultiHopRAG",
        "- 许可证：ODC-BY（https://opendatacommons.org/licenses/by/1-0/）",
        "",
        "## 检索指标",
        "",
        "指标与官方 35 条夹具相同：在去重后的文档序上计算 Recall@1/5/10、MRR、nDCG@10；",
        f"检索深度为 {report['retrieval_depth_chunks']} 个块。null_query 没有期望文档，计入 not_scorable，不稀释召回。",
        "引用覆盖率按命中块是否包含证据事实句计算。本对照不是对原论文 Hits@k（事实句包含）设置的复现。",
        "",
        f"- 案例数：{overall['cases']}（可计分 {overall['scorable_cases']}，不可计分 {overall['not_scorable_cases']}）",
        f"- Recall@1：{_fmt(overall['recall']['@1'])}",
        f"- Recall@5：{_fmt(overall['recall']['@5'])}",
        f"- Recall@10：{_fmt(overall['recall']['@10'])}",
        f"- MRR：{_fmt(overall['mrr'])}",
        f"- nDCG@10：{_fmt(overall['ndcg@10'])}",
        f"- 证据引用覆盖：{overall['citation']['evidence_hit']}/{overall['citation']['evidence_total']}"
        f"（{_fmt(overall['citation']['quote_coverage'])}）",
        f"- 越权案例：{overall['safety']['violation_cases']}（本数据集无 forbidden 文档）",
        f"- 延迟 p50/p95/max：{_fmt(overall['latency_ms']['p50'], 1)} / {_fmt(overall['latency_ms']['p95'], 1)} / {_fmt(overall['latency_ms']['max'], 1)} ms",
        "",
        "## 按 question_type",
        "",
        "| type | cases | scorable | R@1 | R@5 | R@10 | MRR | nDCG@10 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        *type_rows,
        "",
        "## 调用量与费用",
        "",
        f"- 跑前估计：文本 {pre.get('text_count')} 条，估计 token {pre.get('estimated_tokens')}，",
        f"预算闸门费用 {pre_budget if pre_budget is not None else '未知'} 元（{pre.get('budget_check_price_source')}）",
        f"- 实际嵌入调用 {usage.get('call_count')} 次，文本 {usage.get('text_count')} 条，",
        f"估计 token {usage.get('estimated_tokens')}，估计费用 {cost_text}",
        "" if pre_cost is None else f"- 配置单价下的跑前估计费用：{pre_cost}",
        "",
        "## 说明",
        "",
        "- 未改官方 35 条门槛、holdout 决策规则、`evals/datasets/rag-v0.1/` 或生产默认。",
        "- 冒烟模式只证明链路，不能作为质量证据。",
        "- 切分与检索深度沿用冻结官方设置，与原论文默认 256 token 分块、Hits@k 不可直接横向对比。",
        "",
    ]
    return "\n".join(line for line in lines if line is not None)


def _prepare_dataset(mode: str, args: argparse.Namespace):
    if mode == "smoke":
        output_dir = Path(args.output) if args.output else REPO_ROOT / "data" / "multihop_rag" / "smoke"
        return convert.convert_smoke(output_dir)
    output_dir = Path(args.output) if args.output else convert.CONVERTED_DIR
    raw_dir = Path(args.raw_dir) if args.raw_dir else convert.RAW_DIR
    return convert.convert_official(output_dir, raw_dir=raw_dir)


def _preflight_cost(chunk_texts: list[str], queries: list[str], *, official: bool) -> dict[str, Any]:
    usage = estimate_texts(list(chunk_texts) + list(queries))
    budget_cost = usage["budget_check_cost_yuan"]
    print(
        f"[estimate] texts={usage['text_count']} chunks={len(chunk_texts)} queries={len(queries)} "
        f"tokens={usage['estimated_tokens']} budget_check={budget_cost:.4f} yuan "
        f"({usage['budget_check_price_source']})"
    )
    if official and budget_cost > STOP_YUAN:
        raise SystemExit(
            f"跑前估计约 {budget_cost:.4f} 元，明显超过约 1 元预算（停止线 {STOP_YUAN} 元）。已停止，未调用正式 Embedding。"
        )
    return usage


async def run_experiment(args: argparse.Namespace) -> int:
    previous = helpers.apply_frozen_eval_settings()
    _force_eval_flags_off()
    try:
        return await _run_experiment(args)
    finally:
        for name, value in previous.items():
            setattr(settings, name, value)


async def _run_experiment(args: argparse.Namespace) -> int:
    if args.rrf_k not in (None, RETRIEVAL_RRF_K):
        raise SystemExit("MultiHop 对照固定融合常数 60，不能同时改 k")
    if args.retrieval_depth != RETRIEVAL_DEPTH:
        raise SystemExit("MultiHop 对照固定检索深度 10")
    _force_eval_flags_off()
    if settings.RAG_EFFECTIVE_DATE_FILTER:
        raise SystemExit("本对照要求 RAG_EFFECTIVE_DATE_FILTER 保持关闭")
    if os.getenv("RAG_QUERY_REWRITE_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}:
        raise SystemExit("本对照要求 RAG_QUERY_REWRITE_ENABLED 保持关闭")

    print("[1/4] 准备数据集")
    conversion = _prepare_dataset(args.mode, args)
    manifest = convert.load_json(conversion.manifest_path)
    cases = convert.load_json(conversion.cases_path)
    dataset_index = convert.load_json(conversion.index_path)
    cases = sorted(cases, key=lambda item: item["case_id"])
    chunk_texts = collect_chunk_texts(manifest["documents"], conversion.output_dir)
    pre_estimate = _preflight_cost(
        chunk_texts,
        [case["query"] for case in cases],
        official=args.mode == "official",
    )
    if args.estimate_only:
        estimate_path = REPORT_DIR / (
            "rag-v0.1-multihop-rag-20261110-estimate.json"
            if args.mode == "official"
            else "rag-v0.1-multihop-rag-20261110-smoke-estimate.json"
        )
        _refuse_protected(estimate_path)
        estimate_path.write_text(
            json.dumps(
                {
                    "mode": args.mode,
                    "document_count": conversion.document_count,
                    "question_count": conversion.question_count,
                    "indexed_chunk_estimate": len(chunk_texts),
                    "pre_run_estimate": pre_estimate,
                    "stopped_before_embedding": True,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(f"estimate-only -> {estimate_path}")
        return 0

    raw_provider = runner.resolve_embedding(args.mode)
    provider = helpers.CountingEmbeddingProvider(raw_provider)
    if args.mode == "official":
        runner._require_frozen_index_config()
        store_name = settings.RAG_VECTOR_STORE.strip().lower()
        if store_name != "local":
            raise SystemExit(f"正式对照只使用本地向量库，当前为 {settings.RAG_VECTOR_STORE!r}")

    db_path = Path(args.db)
    print(f"[2/4] 重建评测索引 -> {db_path}（documents={conversion.document_count}）")
    memory_before_index = helpers.process_memory_bytes()
    index = await build_multihop_index(db_path, provider, manifest["documents"], conversion.output_dir)
    memory_after_index = helpers.process_memory_bytes()
    try:
        store = get_vector_store(index.session)
        if not isinstance(store, LocalVectorStore):
            raise SystemExit("MultiHop 对照只使用本地向量库")
        print(f"[3/4] 检索 {len(cases)} 条问题（深度 {RETRIEVAL_DEPTH}，k={RETRIEVAL_RRF_K}）")
        outcomes = await run_cases(cases, index, provider)
        indexed_documents = len(index.db_doc_id_to_logical)
        indexed_chunks = index.chunk_total
    finally:
        index.close()
    memory_after_run = helpers.process_memory_bytes()

    usage = helpers.estimate_cost_yuan(provider.text_count, provider.char_count)
    usage["call_count"] = provider.call_count
    usage["text_count"] = provider.text_count
    usage["char_count"] = provider.char_count

    inner = provider._inner
    command = (
        "python evals/multihop_rag/run.py --mode official"
        if args.mode == "official"
        else "python evals/multihop_rag/run.py --mode smoke"
    )
    quality_evidence = False
    report = {
        "experiment": "multihop-rag-retrieval-comparison",
        "variable": "none-supplementary-corpus",
        "run_at": datetime.now(UTC).isoformat(),
        "git_commit": helpers._hook_safe_git_commit(runner.git_commit()),
        "dataset_id": dataset_index["dataset_id"],
        "dataset_version": dataset_index["dataset_version"],
        "corpus_version": dataset_index["corpus_version"],
        "gold_status": dataset_index["gold_status"],
        "attribution": dataset_index["attribution"],
        "mode": args.mode,
        "quality_evidence": quality_evidence,
        "supplementary_comparison": True,
        "official_35_case_thresholds_unchanged": True,
        "holdout_used_for_decision": False,
        "query_rewrite_enabled": False,
        "effective_date_filter_enabled": False,
        "production_query_rewrite_default": False,
        "production_effective_date_filter_default": False,
        "embedding_provider": type(inner).__name__,
        "embedding_model": getattr(inner, "model", "unknown"),
        "embedding_dim": getattr(inner, "dim", None),
        "vector_store": settings.RAG_VECTOR_STORE,
        "rag_backend": settings.RAG_BACKEND,
        "chunk_strategy": settings.RAG_CHUNK_STRATEGY,
        "chunk_size": settings.RAG_CHUNK_SIZE,
        "chunk_overlap": settings.RAG_CHUNK_OVERLAP,
        "rrf_k": RETRIEVAL_RRF_K,
        "retrieval_depth_chunks": RETRIEVAL_DEPTH,
        "document_count": conversion.document_count,
        "question_count": conversion.question_count,
        "scorable_question_count": conversion.scorable_question_count,
        "null_query_count": conversion.null_query_count,
        "question_types": conversion.question_types,
        "indexed_documents": indexed_documents,
        "indexed_chunks": indexed_chunks,
        "machine": runner.machine_spec(),
        "package_versions": helpers.package_versions(),
        "command": command,
        "working_directory": str(REPO_ROOT),
        "pre_run_estimate": pre_estimate,
        "embedding_usage": usage,
        "memory": {
            "rss_before_index_bytes": memory_before_index.get("rss_bytes"),
            "rss_after_index_bytes": memory_after_index.get("rss_bytes"),
            "rss_after_run_bytes": memory_after_run.get("rss_bytes"),
        },
        "overall": summarize(outcomes),
        "by_question_type": group_summaries(outcomes, "category"),
        "failing_cases": [item.to_dict() for item in outcomes if item.failures],
        "failure_slices": helpers.failure_slices(outcomes),
        "cases": [item.to_dict() for item in outcomes],
        "recommendation": (
            "Treat this report as a supplementary retrieval comparison only. "
            "Do not change official 35-case thresholds or enable query rewrite / effective-date filter from this result."
        ),
    }
    report = _sanitize_git_commits(report)

    suffix = "" if args.mode == "official" else "-smoke"
    report_path = REPORT_DIR / f"rag-v0.1-multihop-rag-20261110{suffix}.json"
    doc_path = EVAL_DOC_DIR / f"rag-v0.1-multihop-rag-20261110{suffix}.md"
    _refuse_protected(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    doc_path.write_text(render_markdown(report), encoding="utf-8", newline="\n")
    print(f"[4/4] {report_path}")
    print(f"      Markdown {doc_path}")
    overall = report["overall"]
    print(
        f"      documents={report['document_count']} questions={report['question_count']} "
        f"scorable={report['scorable_question_count']}"
    )
    print(
        f"      Recall@1={_fmt(overall['recall']['@1'])} "
        f"Recall@5={_fmt(overall['recall']['@5'])} "
        f"Recall@10={_fmt(overall['recall']['@10'])} "
        f"MRR={_fmt(overall['mrr'])} "
        f"nDCG@10={_fmt(overall['ndcg@10'])}"
    )
    cost = usage.get("estimated_cost_yuan")
    print(
        f"      嵌入调用 {usage['call_count']} 次，文本 {usage['text_count']} 条，"
        f"估计 token {usage.get('estimated_tokens')}，估计费用 {'未知' if cost is None else cost}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 MultiHop-RAG 补充检索对照")
    parser.add_argument("--mode", choices=("official", "smoke"), default="official")
    parser.add_argument("--db", default=None, help="评测索引 SQLite 路径（每次重建）")
    parser.add_argument("--output", default=None, help="转换输出目录")
    parser.add_argument("--raw-dir", default=None, help="官方原始数据目录")
    parser.add_argument("--rrf-k", dest="rrf_k", type=int, default=RETRIEVAL_RRF_K)
    parser.add_argument("--retrieval-depth", dest="retrieval_depth", type=int, default=RETRIEVAL_DEPTH)
    parser.add_argument("--estimate-only", action="store_true", help="只估计 token/费用，不调用正式 Embedding")
    args = parser.parse_args()
    if args.db is None:
        args.db = str(DEFAULT_OFFICIAL_DB if args.mode == "official" else DEFAULT_SMOKE_DB)
    return asyncio.run(run_experiment(args))


if __name__ == "__main__":
    raise SystemExit(main())
