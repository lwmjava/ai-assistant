"""RAG-005 基线运行器。

用法（仓库根目录）::

    python scripts/run_rag_baseline.py --mode official
    python scripts/run_rag_baseline.py --mode smoke        # Mock 嵌入，仅验链路

正式模式的硬性前置（不满足直接退出，不产出报告）：

1. 嵌入必须是真实提供商。Mock 只能证明链路，不能证明检索质量。
2. ``RAG_VECTOR_STORE`` 必须是 ``local``——ADR-0002 规定评测固定 Local。

报告会冻结代码、数据集、语料、嵌入模型、索引与检索参数版本，便于后续单变量对比。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.config import settings  # noqa: E402
from app.rag.embeddings.base import EmbeddingProvider  # noqa: E402
from app.rag.embeddings.factory import get_embedding_provider  # noqa: E402
from app.rag.embeddings.mock import MockEmbeddingProvider  # noqa: E402
from tests.eval.harness import (  # noqa: E402
    build_eval_index,
    group_summaries,
    run_all_cases,
    summarize,
)
from tests.eval.validation import INDEX_PATH, load_json  # noqa: E402

DEFAULT_DB = REPO_ROOT / "data" / "eval_rag_v01.db"
REPORT_DIR = REPO_ROOT / "evals" / "reports"


def git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def resolve_embedding(mode: str) -> EmbeddingProvider:
    """按模式选择嵌入；正式模式拒绝任何 Mock 降级。"""
    if mode == "smoke":
        return MockEmbeddingProvider(dim=64)

    provider = get_embedding_provider()
    if isinstance(provider, MockEmbeddingProvider):
        raise SystemExit(
            "正式基线拒绝运行：当前解析到 Mock 嵌入（很可能是 EMBEDDING_API_KEY 为空导致的开发环境降级）。\n"
            "Mock 只能证明链路，不能证明检索质量。请配置真实 EMBEDDING_API_KEY，"
            "或用 --mode smoke 明确跑链路验证。"
        )
    if settings.RAG_VECTOR_STORE.strip().lower() != "local":
        raise SystemExit(
            f"正式基线拒绝运行：ADR-0002 规定评测固定 Local，当前 RAG_VECTOR_STORE="
            f"{settings.RAG_VECTOR_STORE!r}。"
        )
    return provider


def build_run_config(mode: str, provider: EmbeddingProvider, retrieval_depth: int) -> dict:
    """冻结本次运行的全部版本与参数，供复现和单变量对比。"""
    dataset_index = load_json(INDEX_PATH)
    return {
        "mode": mode,
        "run_at": datetime.now(UTC).isoformat(),
        "git_commit": git_commit(),
        "dataset_id": dataset_index["dataset_id"],
        "dataset_version": dataset_index["dataset_version"],
        "corpus_version": dataset_index["corpus_version"],
        "gold_status": dataset_index["gold_status"],
        "embedding_provider": type(provider).__name__,
        "embedding_model": getattr(provider, "model", "unknown"),
        "embedding_dim": getattr(provider, "dim", None),
        "vector_store": settings.RAG_VECTOR_STORE,
        "rag_backend": settings.RAG_BACKEND,
        "chunk_strategy": settings.RAG_CHUNK_STRATEGY,
        "chunk_size": settings.RAG_CHUNK_SIZE,
        "chunk_overlap": settings.RAG_CHUNK_OVERLAP,
        "rrf_k": settings.RAG_HYBRID_RRF_K,
        "retrieval_depth_chunks": retrieval_depth,
        "production_default_top_k": settings.RAG_TOP_K,
    }


async def main_async(args: argparse.Namespace) -> int:
    provider = resolve_embedding(args.mode)
    config = build_run_config(args.mode, provider, args.retrieval_depth)

    print(f"[1/4] 重建评测索引 -> {args.db}")
    index = await build_eval_index(Path(args.db), provider)
    config["indexed_documents"] = len(index.db_doc_id_to_logical)
    config["indexed_chunks"] = index.chunk_total
    print(f"      文档 {config['indexed_documents']} 篇，分块 {config['indexed_chunks']} 个")

    try:
        print(f"[2/4] 运行案例（检索深度 {args.retrieval_depth} 个分块）")
        outcomes = await run_all_cases(
            index, provider, retrieval_depth=args.retrieval_depth
        )
    finally:
        index.close()

    print("[3/4] 聚合指标")
    report = {
        "run_config": config,
        "overall": summarize(outcomes),
        "by_split": group_summaries(outcomes, "split"),
        "by_category": group_summaries(outcomes, "category"),
        "by_tier": group_summaries(outcomes, "tier"),
        "by_review_status": group_summaries(outcomes, "review_status"),
        "failing_cases": [
            item.to_dict() for item in outcomes if item.failures
        ],
        "cases": [item.to_dict() for item in outcomes],
    }

    stamp = datetime.now(UTC).strftime("%Y%m%d")
    suffix = "" if args.mode == "official" else f"-{args.mode}"
    default_name = f"rag-v0.1-baseline-{stamp}{suffix}.json"
    out_path = (Path(args.out) if args.out else REPORT_DIR / default_name).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    overall = report["overall"]
    print(f"[4/4] 已写出 {out_path}")
    print(
        f"      Recall@1={_fmt(overall['recall']['@1'])} "
        f"@5={_fmt(overall['recall']['@5'])} @10={_fmt(overall['recall']['@10'])} "
        f"MRR={_fmt(overall['mrr'])} nDCG@10={_fmt(overall['ndcg@10'])}"
    )
    print(
        f"      引用原文覆盖={_fmt(overall['citation']['quote_coverage'])} "
        f"越权命中案例={overall['safety']['violation_cases']}/"
        f"{overall['safety']['cases_with_forbidden_docs']} "
        f"P50={_fmt(overall['latency_ms']['p50'], 1)}ms P95={_fmt(overall['latency_ms']['p95'], 1)}ms"
    )
    print(f"      失败案例 {len(report['failing_cases'])} 条")
    return 0


def _fmt(value: float | None, digits: int = 4) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 RAG v0.1 检索基线")
    parser.add_argument(
        "--mode",
        choices=("official", "smoke"),
        default="official",
        help="official=真实嵌入正式基线；smoke=Mock 嵌入仅验链路",
    )
    parser.add_argument("--db", default=str(DEFAULT_DB), help="评测索引 SQLite 路径（每次重建）")
    parser.add_argument("--retrieval-depth", type=int, default=10, help="每次检索取回的分块数")
    parser.add_argument("--out", default=None, help="报告 JSON 输出路径")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
