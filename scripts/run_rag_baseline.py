"""功能概述
在隔离索引上运行检索评测，并可选地对融合常数做一次对照扫描。

功能涵盖
- 正式模式使用真实嵌入；冒烟模式使用确定性伪嵌入，只验证链路。
- 单次运行可临时覆盖融合常数，不写回配置文件。
- 扫描模式只重建一次索引，再按多个常数检索，并用调参集决定是否采纳。
- 写入报告前拒绝覆盖 2026-09-19 的冻结基线文件。

已完成
- 报告记录数据集、嵌入、切分、融合常数和检索深度。
- 正式扫描要求切分策略、块大小、重叠、向量库、后端和生效日期开关与冻结基线一致。
- 采纳规则：Recall@1 与 MRR 都严格提高，且越权案例数不增加。
- 单次覆盖融合常数后，进程在返回前写回进入前的值。
- 显式传入词面重排时，同一次已截断结果先记对照再重排，默认不重排。

待完善
- 冒烟扫描会写 JSON，但不写质量结论 Markdown。
- 尚未把生成层答案正确性纳入本脚本。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.config import settings  # noqa: E402
from app.rag.effective_date import retrieval_window  # noqa: E402
from app.rag.embeddings.base import EmbeddingProvider  # noqa: E402
from app.rag.embeddings.factory import get_embedding_provider  # noqa: E402
from app.rag.embeddings.mock import MockEmbeddingProvider, tokenize  # noqa: E402
from app.rag.vectorstore.factory import get_vector_store  # noqa: E402
from app.rag.vectorstore.local import LocalVectorStore  # noqa: E402
from tests.eval.harness import (  # noqa: E402
    CaseOutcome,
    build_eval_index,
    group_summaries,
    run_all_cases,
    score_hits,
    summarize,
)
from tests.eval.lexical_rerank import (  # noqa: E402
    decide_lexical_coverage,
    rerank_by_lexical_coverage,
)
from tests.eval.rrf_decision import TUNING_SPLITS, choose_k  # noqa: E402
from tests.eval.validation import INDEX_PATH, load_cases, load_json  # noqa: E402

LEXICAL_DEPTH = 10
LEXICAL_RRF_K = 60

DEFAULT_DB = REPO_ROOT / "data" / "eval_rag_v01.db"
SWEEP_DB = REPO_ROOT / "data" / "eval_rrf_k_sweep.db"
REPORT_DIR = REPO_ROOT / "evals" / "reports"
FROZEN_BASELINE = (REPORT_DIR / "rag-v0.1-baseline-20260919.json").resolve()
COMPARISON_MD = REPO_ROOT / "docs" / "evaluations" / "rag-v0.1-rrf-k-comparison.md"


def git_commit() -> str:
    """读取当前 HEAD，供报告记录代码版本。

    作用：失败时返回 unknown，不中断评测。
    入参：无。
    出参：40 位提交哈希，或 unknown。
    """
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
    """按运行模式选择嵌入提供商。

    作用：正式模式拒绝伪嵌入和非 local 向量库，避免写出不能代表质量的报告。
    入参：mode 为 official 或 smoke。
    出参：嵌入提供商。不满足正式模式前置时直接退出进程。
    """
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


def refuse_frozen(path: Path) -> None:
    """拒绝覆盖已冻结的基线报告。

    作用：防止新实验改写 2026-09-19 那份报告。
    入参：即将写入的路径。
    出参：无。路径指向冻结文件时退出进程。
    """
    if path.resolve() == FROZEN_BASELINE:
        raise SystemExit(f"拒绝覆盖冻结基线：{FROZEN_BASELINE}")


def apply_rrf_k(value: int | None) -> int:
    """在当前进程内临时改融合常数。

    作用：只影响此后新建的检索服务，不修改配置文件，也不重建索引。
    入参：value 为正整数；None 表示继续使用当前配置。
    出参：实际生效的常数。非法值时退出进程。
    """
    if value is None:
        return int(settings.RAG_HYBRID_RRF_K)
    if value < 1:
        raise SystemExit("--rrf-k 必须是正整数")
    settings.RAG_HYBRID_RRF_K = value
    return value


def parse_sweep(raw: str | None) -> list[int]:
    """把逗号分隔的融合常数解析成列表。

    作用：扫描必须包含对照值 60，否则无法比较。
    入参：raw 如 "60,40,80"；None 或空串表示不扫描。
    出参：常数列表。缺 60 或含非正整数时退出进程。
    """
    if not raw:
        return []
    values = [int(part.strip()) for part in raw.split(",") if part.strip()]
    if 60 not in values:
        raise SystemExit("--rrf-sweep 必须包含对照 k=60")
    if any(item < 1 for item in values):
        raise SystemExit("--rrf-sweep 中的 k 必须是正整数")
    return values


def build_run_config(mode: str, provider: EmbeddingProvider, retrieval_depth: int) -> dict:
    """收集本次运行的版本与检索参数。

    作用：让报告能说明除融合常数外还有哪些配置。
    入参：mode 为 official 或 smoke；provider 为嵌入提供商；retrieval_depth 为取回块数。
    出参：可写入 JSON 的配置字典。融合常数取调用当时的进程配置。
    """
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


def make_report(config: dict, outcomes: list[CaseOutcome]) -> dict:
    """把案例结果收成报告，并单独留出调参集与留出集汇总。

    作用：全量指标供查阅；调参集汇总才交给常数决策。留出集不参与决策。
    入参：config 为运行配置；outcomes 为按案例排列的检索结果。
    出参：含 overall、分组、decision_inputs、失败案例和全部案例的字典。
    """
    tuning = [item for item in outcomes if item.split in TUNING_SPLITS]
    holdout = [item for item in outcomes if item.split == "holdout"]
    return {
        "run_config": config,
        "overall": summarize(outcomes),
        "by_split": group_summaries(outcomes, "split"),
        "by_category": group_summaries(outcomes, "category"),
        "by_tier": group_summaries(outcomes, "tier"),
        "by_review_status": group_summaries(outcomes, "review_status"),
        "decision_inputs": {
            "splits": sorted(TUNING_SPLITS),
            "summary": summarize(tuning),
            "holdout_summary": summarize(holdout),
            "holdout_used_for_decision": False,
        },
        "failing_cases": [item.to_dict() for item in outcomes if item.failures],
        "cases": [item.to_dict() for item in outcomes],
    }


def write_json(path: Path, payload: dict) -> Path:
    """把报告写成 JSON，并先检查是否会覆盖冻结基线。

    作用：创建父目录后覆盖写入调用方给出的路径。
    入参：path 为目标文件；payload 为可序列化字典。
    出参：解析后的绝对路径。指向冻结基线时不写文件并退出。
    """
    resolved = path.resolve()
    refuse_frozen(resolved)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return resolved


def print_overall(overall: dict) -> None:
    """在终端打印总体检索指标。

    作用：便于扫描每个常数后立即看到分数。
    入参：overall 为 summarize 返回的总体字典。
    出参：无。
    """
    print(
        f"      Recall@1={_fmt(overall['recall']['@1'])} "
        f"@5={_fmt(overall['recall']['@5'])} @10={_fmt(overall['recall']['@10'])} "
        f"MRR={_fmt(overall['mrr'])} nDCG@10={_fmt(overall['ndcg@10'])}"
    )
    print(
        f"      引用原文覆盖={_fmt(overall['citation']['quote_coverage'])} "
        f"越权命中案例={overall['safety']['violation_cases']}/"
        f"{overall['safety']['cases_with_forbidden_docs']} "
        f"P50={_fmt(overall['latency_ms']['p50'], 1)}ms "
        f"P95={_fmt(overall['latency_ms']['p95'], 1)}ms"
    )


def render_comparison_md(comparison: dict) -> str:
    """把常数对照结果写成 Markdown。

    作用：人类可读地列出调参集、留出集和全量指标，以及采纳或保持的原因。
    入参：comparison 含 run_at、数据集、代码版本、adopted_k、decision、reason、runs。
    出参：完整 Markdown 文本。
    """
    lines = [
        "# 融合常数对照",
        "",
        "> 实验：倒数排名融合常数",
        f"> 运行时间：{comparison['run_at']}",
        f"> 数据集：`{comparison['dataset_id']}@{comparison['dataset_version']}`",
        f"> 代码：`{comparison['git_commit']}`",
        "> 决策只使用 development 与 validation。holdout 仅记录，不参与改 k。",
        "> 本文件不改写 `evals/reports/rag-v0.1-baseline-20260919.json`。",
        "",
        "## 1. 结论",
        "",
        comparison["reason"],
        "",
        f"采纳 k = **{comparison['adopted_k']}**（决策：`{comparison['decision']}`）。",
        "默认 `RAG_HYBRID_RRF_K` 仅在 `decision=adopt` 时修改；`keep` 时保持 60。",
        "",
        "索引只构建一次。三次检索只改变 `RAG_HYBRID_RRF_K`。",
        f"文档 {comparison['indexed_documents']} 篇，分块 {comparison['indexed_chunks']} 个。",
        "",
        "## 2. 调参 split（development + validation）",
        "",
        "| k | Recall@1 | MRR | nDCG@10 | 引用覆盖 | 越权案例 | 资格 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in comparison["runs"]:
        tuning = row["tuning"]
        lines.append(
            "| {k} | {r1} | {mrr} | {ndcg} | {cite} | {viol}/{den} | {mark} |".format(
                k=row["rrf_k"],
                r1=_fmt(tuning["recall"]["@1"]),
                mrr=_fmt(tuning["mrr"]),
                ndcg=_fmt(tuning["ndcg@10"]),
                cite=_fmt(tuning["citation"]["quote_coverage"]),
                viol=tuning["safety"]["violation_cases"],
                den=tuning["safety"]["cases_with_forbidden_docs"],
                mark=row["eligibility"],
            )
        )
    lines.extend(
        [
            "",
            "## 3. holdout（不参与决策）",
            "",
            "| k | Recall@1 | MRR | 越权案例 |",
            "|---|---|---|---|",
        ]
    )
    for row in comparison["runs"]:
        holdout = row["holdout"]
        lines.append(
            f"| {row['rrf_k']} | {_fmt(holdout['recall']['@1'])} | {_fmt(holdout['mrr'])} | "
            f"{holdout['safety']['violation_cases']}/{holdout['safety']['cases_with_forbidden_docs']} |"
        )
    lines.extend(
        [
            "",
            "## 4. 全量（含 holdout，仅供查阅）",
            "",
            "| k | Recall@1 | MRR | P50 ms | P95 ms | 失败案例 |",
            "|---|---|---|---|---|---|",
        ]
    )
    for row in comparison["runs"]:
        overall = row["overall"]
        lines.append(
            f"| {row['rrf_k']} | {_fmt(overall['recall']['@1'])} | {_fmt(overall['mrr'])} | "
            f"{_fmt(overall['latency_ms']['p50'], 1)} | {_fmt(overall['latency_ms']['p95'], 1)} | "
            f"{row['failing_cases']} |"
        )
    lines.extend(
        [
            "",
            "## 5. 未验证",
            "",
            "- 未用真实 LLM 测生成层、拒答和答案点。",
            "- 语料规模小，Recall@5/@10 可能饱和，不代表生产检索质量。",
            "- 前端知识库页不是本任务的通过条件。",
            "- 未改 Chunking、Embedding、BM25 k1/b、Top-K、Reranker 或 Query Rewrite。",
            "",
        ]
    )
    return "\n".join(lines)


def _require_frozen_index_config() -> None:
    """正式扫描前核对索引期配置没有被同时改掉。

    作用：保证本次对照只可能来自融合常数，而不是切分、向量库或生效日期开关。
    入参：无。读取当前进程配置。
    出参：无。任一期望项不一致时退出进程。
    """
    expected = {
        "RAG_CHUNK_STRATEGY": "structured",
        "RAG_CHUNK_SIZE": 500,
        "RAG_CHUNK_OVERLAP": 64,
        "RAG_VECTOR_STORE": "local",
        "RAG_BACKEND": "native",
        "RAG_EFFECTIVE_DATE_FILTER": False,
    }
    mismatches = [
        f"{name}={getattr(settings, name)!r}，期望 {value!r}"
        for name, value in expected.items()
        if getattr(settings, name) != value
    ]
    if mismatches:
        raise SystemExit(
            "正式 RRF 对照拒绝运行：索引期配置与 2026-09-19 基线不一致。\n"
            + "\n".join(mismatches)
        )


async def run_sweep(args: argparse.Namespace, provider: EmbeddingProvider) -> int:
    """同一索引上扫描多个融合常数并写出对照。

    作用：索引只建一次；每个常数单独检索。正式模式才写 Markdown 结论。
    入参：args 含 mode、rrf_sweep、db、retrieval_depth；provider 为嵌入提供商。
    出参：进程退出码。成功为 0。结束时恢复进入函数前的融合常数。
    """
    ks = parse_sweep(args.rrf_sweep)
    if args.mode == "official":
        _require_frozen_index_config()
    db_path = Path(args.db) if args.db != str(DEFAULT_DB) else SWEEP_DB
    print(f"[1/3] 重建评测索引（一次）-> {db_path}")
    index = await build_eval_index(db_path, provider)
    indexed_documents = len(index.db_doc_id_to_logical)
    indexed_chunks = index.chunk_total
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    runs: list[dict] = []
    previous_k = settings.RAG_HYBRID_RRF_K
    try:
        for rrf_k in ks:
            apply_rrf_k(rrf_k)
            print(f"[2/3] 检索 k={rrf_k}（深度 {args.retrieval_depth}）")
            config = build_run_config(args.mode, provider, args.retrieval_depth)
            config["indexed_documents"] = len(index.db_doc_id_to_logical)
            config["indexed_chunks"] = index.chunk_total
            config["rrf_sweep"] = ks
            outcomes = await run_all_cases(
                index, provider, retrieval_depth=args.retrieval_depth
            )
            report = make_report(config, outcomes)
            out_path = write_json(REPORT_DIR / f"rag-v0.1-rrf-k{rrf_k}-{stamp}.json", report)
            print_overall(report["overall"])
            print(f"      已写出 {out_path}")
            runs.append(
                {
                    "rrf_k": rrf_k,
                    "report": str(out_path.relative_to(REPO_ROOT)).replace("\\", "/"),
                    "tuning": report["decision_inputs"]["summary"],
                    "holdout": report["decision_inputs"]["holdout_summary"],
                    "overall": report["overall"],
                    "failing_cases": len(report["failing_cases"]),
                }
            )
    finally:
        settings.RAG_HYBRID_RRF_K = previous_k
        index.close()

    control = next(item for item in runs if item["rrf_k"] == 60)
    decision = choose_k(
        60,
        control["tuning"],
        [(item["rrf_k"], item["tuning"]) for item in runs if item["rrf_k"] != 60],
    )
    by_k = {note["rrf_k"]: note for note in decision["candidates"]}
    for item in runs:
        if item["rrf_k"] == 60:
            item["eligibility"] = "对照"
        else:
            note = by_k[item["rrf_k"]]
            item["eligibility"] = "采纳候选" if note["eligible"] else note["reason"]

    dataset_index = load_json(INDEX_PATH)
    comparison = {
        "experiment": "rrf-k-sweep",
        "run_at": datetime.now(UTC).isoformat(),
        "git_commit": git_commit(),
        "dataset_id": dataset_index["dataset_id"],
        "dataset_version": dataset_index["dataset_version"],
        "mode": args.mode,
        "indexed_documents": indexed_documents,
        "indexed_chunks": indexed_chunks,
        "control_k": 60,
        "sweep": ks,
        "decision_splits": sorted(TUNING_SPLITS),
        "holdout_used_for_decision": False,
        "adopted_k": decision["adopted_k"],
        "decision": decision["decision"],
        "reason": decision["reason"],
        "runs": runs,
    }
    comparison_path = write_json(
        REPORT_DIR / f"rag-v0.1-rrf-k-comparison-{stamp}.json", comparison
    )
    if args.mode == "official":
        COMPARISON_MD.write_text(render_comparison_md(comparison), encoding="utf-8")
        print(f"[3/3] 对照 {comparison_path}")
        print(f"      Markdown {COMPARISON_MD}")
    else:
        print(f"[3/3] smoke 对照只写 JSON：{comparison_path}")
        print("      smoke 不写质量结论 Markdown")
    print(f"      决策 {decision['decision']} adopted_k={decision['adopted_k']}")
    print(f"      {decision['reason']}")
    return 0


def machine_spec() -> dict:
    """记录运行机器的公开规格。

    作用：报告能说明数字是在哪类机器上得到的。
    入参：无。
    出参：不含主机名、密钥和连接串的字典。
    """
    return {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
    }


async def run_lexical(args: argparse.Namespace, provider: EmbeddingProvider) -> int:
    """同一次建库、同一次检索上比较词面覆盖重排。

    作用：对照是已截断的融合顺序，处理只重排这同一批。决策只用调参集。
    入参：args 含 mode、db、retrieval_depth、rrf_k、rrf_sweep；provider 为真实嵌入。
    出参：进程退出码。成功为 0。不修改检索配置。
    """
    if args.mode != "official":
        raise SystemExit("词面覆盖结论只接受 --mode official。smoke 不能作为采纳证据。")
    if args.rrf_sweep:
        raise SystemExit("--rerank lexical 不能与 --rrf-sweep 同时使用")
    if args.rrf_k not in (None, LEXICAL_RRF_K):
        raise SystemExit("--rerank lexical 固定融合常数 60，不能同时改 k")
    if args.retrieval_depth != LEXICAL_DEPTH:
        raise SystemExit("--rerank lexical 固定检索深度 10")
    if int(settings.RAG_HYBRID_RRF_K) != LEXICAL_RRF_K:
        raise SystemExit(
            f"词面覆盖实验固定 k=60，当前 RAG_HYBRID_RRF_K={settings.RAG_HYBRID_RRF_K!r}"
        )
    _require_frozen_index_config()

    db_path = Path(args.db)
    print(f"[1/3] 重建评测索引（一次）-> {db_path}")
    index = await build_eval_index(db_path, provider)
    control_outcomes: list[CaseOutcome] = []
    treatment_outcomes: list[CaseOutcome] = []
    try:
        store = get_vector_store(index.session)
        if not isinstance(store, LocalVectorStore):
            raise SystemExit("词面覆盖实验只使用本地向量库返回的已截断结果")
        cases = sorted(load_cases(), key=lambda item: item["case_id"])
        print(f"[2/3] 检索并重排 {len(cases)} 条案例（深度 {LEXICAL_DEPTH}，k={LEXICAL_RRF_K}）")
        for case in cases:
            query = case["query"]
            tokens = tokenize(query)
            as_of, schedule_at = retrieval_window(query)
            started = time.perf_counter()
            vector = (await provider.embed([query]))[0]
            hits = await store.hybrid_search(
                vector,
                tokens,
                case["identity"]["tenant_id"],
                LEXICAL_DEPTH,
                LEXICAL_RRF_K,
                as_of=as_of,
                schedule_at=schedule_at,
            )
            latency_ms = (time.perf_counter() - started) * 1000.0
            if len(hits) > LEXICAL_DEPTH:
                raise SystemExit("hybrid_search 返回条数超过深度 10，已停止")
            reranked = rerank_by_lexical_coverage(tokens, hits)
            if sorted(hit.id for hit in reranked) != sorted(hit.id for hit in hits):
                raise SystemExit("重排改变了候选集合，已停止")
            control_outcomes.append(score_hits(case, index, hits, latency_ms=latency_ms))
            treatment_outcomes.append(
                score_hits(case, index, reranked, latency_ms=0.0)
            )
    finally:
        index.close()

    config = build_run_config(args.mode, provider, LEXICAL_DEPTH)
    config["indexed_documents"] = len(index.db_doc_id_to_logical)
    config["indexed_chunks"] = index.chunk_total
    config["variable"] = "lexical_coverage"
    config["rerank_after_truncation"] = True
    config["candidate_pool_expanded"] = False
    config["machine"] = machine_spec()
    config["command"] = "python scripts/run_rag_baseline.py --mode official --rerank lexical"
    config["working_directory"] = str(REPO_ROOT)
    control_report = make_report(config, control_outcomes)
    treatment_report = make_report(config, treatment_outcomes)
    decision = decide_lexical_coverage(
        control_report["decision_inputs"]["summary"],
        treatment_report["decision_inputs"]["summary"],
    )
    dataset_index = load_json(INDEX_PATH)
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    comparison = {
        "experiment": "lexical-coverage",
        "variable": "lexical_coverage",
        "run_at": datetime.now(UTC).isoformat(),
        "git_commit": git_commit(),
        "command": config["command"],
        "working_directory": config["working_directory"],
        "dataset_id": dataset_index["dataset_id"],
        "dataset_version": dataset_index["dataset_version"],
        "corpus_version": dataset_index["corpus_version"],
        "mode": args.mode,
        "embedding_provider": config["embedding_provider"],
        "embedding_model": config["embedding_model"],
        "embedding_dim": config["embedding_dim"],
        "vector_store": config["vector_store"],
        "rrf_k": LEXICAL_RRF_K,
        "retrieval_depth_chunks": LEXICAL_DEPTH,
        "indexed_documents": config["indexed_documents"],
        "indexed_chunks": config["indexed_chunks"],
        "machine": config["machine"],
        "decision_splits": sorted(TUNING_SPLITS),
        "holdout_used_for_decision": False,
        "decision": decision["decision"],
        "reason_code": decision["reason_code"],
        "reason": decision["reason"],
        "adoption_rule": "Recall@1 高于对照，且 MRR 高于对照，且越权案例数不高于对照",
        "not_production_quality_claim": True,
        "signal_same_family_as_bm25": True,
        "independent_reranker": "Planned",
        "control": {
            "order": "rrf_truncated",
            "tuning": control_report["decision_inputs"]["summary"],
            "holdout": control_report["decision_inputs"]["holdout_summary"],
            "overall": control_report["overall"],
        },
        "treatment": {
            "order": "lexical_coverage",
            "tuning": treatment_report["decision_inputs"]["summary"],
            "holdout": treatment_report["decision_inputs"]["holdout_summary"],
            "overall": treatment_report["overall"],
        },
    }
    json_path = write_json(
        REPORT_DIR / f"rag-v0.1-lexical-coverage-{stamp}.json",
        comparison,
    )
    md_path = REPO_ROOT / "docs" / "evaluations" / f"rag-v0.1-lexical-coverage-{stamp}.md"
    md_path.write_text(render_lexical_md(comparison), encoding="utf-8")
    print(f"[3/3] {json_path}")
    print(f"      Markdown {md_path}")
    print(f"      决策 {decision['decision']}")
    print(f"      {decision['reason']}")
    tuning_control = comparison["control"]["tuning"]
    tuning_treatment = comparison["treatment"]["tuning"]
    print(
        "      调参集对照 "
        f"Recall@1={_fmt(tuning_control['recall']['@1'])} "
        f"MRR={_fmt(tuning_control['mrr'])} "
        f"越权={tuning_control['safety']['violation_cases']}"
    )
    print(
        "      调参集处理 "
        f"Recall@1={_fmt(tuning_treatment['recall']['@1'])} "
        f"MRR={_fmt(tuning_treatment['mrr'])} "
        f"越权={tuning_treatment['safety']['violation_cases']}"
    )
    return 0


def render_lexical_md(comparison: dict) -> str:
    """把词面覆盖对照写成 Markdown。

    作用：列出调参集、留出集和采纳结论，并写明这不是生产检索质量结论。
    入参：comparison 为 run_lexical 组装的对照字典。
    出参：完整 Markdown 文本。不含密钥和连接串。
    """
    control = comparison["control"]["tuning"]
    treatment = comparison["treatment"]["tuning"]
    holdout_control = comparison["control"]["holdout"]
    holdout_treatment = comparison["treatment"]["holdout"]
    machine = comparison["machine"]
    lines = [
        "# 词面覆盖重排对照",
        "",
        "> 变量：`lexical_coverage`",
        f"> 运行时间：{comparison['run_at']}",
        f"> 数据集：`{comparison['dataset_id']}@{comparison['dataset_version']}`",
        f"> 语料版本：`{comparison['corpus_version']}`",
        f"> 代码：`{comparison['git_commit']}`",
        f"> 命令：`{comparison['command']}`",
        f"> 工作目录：`{comparison['working_directory']}`",
        (
            f"> 嵌入：{comparison['embedding_provider']} / {comparison['embedding_model']} / "
            f"{comparison['embedding_dim']} 维"
        ),
        f"> 向量库：{comparison['vector_store']}",
        (
            f"> 机器：{machine['system']} {machine['release']} {machine['machine']}，"
            f"Python {machine['python']}，CPU {machine['cpu_count']}"
        ),
        "> 决策只使用 development 与 validation。holdout 仅记录，不参与判断，也不改公式。",
        "> 重排发生在 hybrid_search 已经截断之后，深度 10，融合常数 60。没有第二次检索。",
        "> 本轮不能作为生产检索质量结论。信号与 BM25 同源。独立 Reranker 仍是 Planned。",
        "> 本文件不改写 `evals/reports/rag-v0.1-baseline-20260919.json`。",
        "",
        "## 1. 结论",
        "",
        comparison["reason"],
        "",
        f"决策：`{comparison['decision']}`。",
        (
            f"索引文档 {comparison['indexed_documents']} 篇，"
            f"分块 {comparison['indexed_chunks']} 个。"
        ),
        "对照顺序是已截断的倒数排名融合顺序。处理顺序只按词面覆盖率重排这同一批。",
        "",
        "## 2. 调参 split（development + validation）",
        "",
        "| 顺序 | Recall@1 | MRR | 越权案例 |",
        "|---|---|---|---|",
        (
            f"| 对照 | {_fmt(control['recall']['@1'])} | {_fmt(control['mrr'])} | "
            f"{control['safety']['violation_cases']} |"
        ),
        (
            f"| 词面覆盖 | {_fmt(treatment['recall']['@1'])} | {_fmt(treatment['mrr'])} | "
            f"{treatment['safety']['violation_cases']} |"
        ),
        "",
        "## 3. holdout（不参与决策）",
        "",
        "| 顺序 | Recall@1 | MRR | 越权案例 |",
        "|---|---|---|---|",
        (
            f"| 对照 | {_fmt(holdout_control['recall']['@1'])} | {_fmt(holdout_control['mrr'])} | "
            f"{holdout_control['safety']['violation_cases']} |"
        ),
        (
            f"| 词面覆盖 | {_fmt(holdout_treatment['recall']['@1'])} | {_fmt(holdout_treatment['mrr'])} | "
            f"{holdout_treatment['safety']['violation_cases']} |"
        ),
        "",
    ]
    return "\n".join(lines)


async def main_async(args: argparse.Namespace) -> int:
    """按命令行选择单次评测或融合常数扫描。

    作用：解析嵌入后分发。扫描与单次运行互斥，有 rrf_sweep 时走扫描。
    入参：args 为 argparse 结果。
    出参：进程退出码。单次路径结束时写回进入前的融合常数。
    """
    provider = resolve_embedding(args.mode)
    if args.rerank == "lexical":
        return await run_lexical(args, provider)
    if args.rrf_sweep:
        if args.mode != "official" and args.mode != "smoke":
            raise SystemExit("不支持的 mode")
        return await run_sweep(args, provider)

    previous_k = settings.RAG_HYBRID_RRF_K
    try:
        apply_rrf_k(args.rrf_k)
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
        report = make_report(config, outcomes)

        stamp = datetime.now(UTC).strftime("%Y%m%d")
        suffix = "" if args.mode == "official" else f"-{args.mode}"
        if args.rrf_k is not None:
            default_name = f"rag-v0.1-rrf-k{args.rrf_k}-{stamp}{suffix}.json"
        else:
            default_name = f"rag-v0.1-baseline-{stamp}{suffix}.json"
        out_path = write_json(Path(args.out) if args.out else REPORT_DIR / default_name, report)

        overall = report["overall"]
        print(f"[4/4] 已写出 {out_path}")
        print_overall(overall)
        print(f"      失败案例 {len(report['failing_cases'])} 条")
        return 0
    finally:
        settings.RAG_HYBRID_RRF_K = previous_k


def _fmt(value: float | None, digits: int = 4) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def main() -> int:
    """解析命令行并启动评测。

    作用：提供正式运行、冒烟运行、单次常数覆盖和常数扫描四个入口。
    入参：无，读取 sys.argv。
    出参：进程退出码。
    """
    parser = argparse.ArgumentParser(description="运行检索评测或融合常数对照")
    parser.add_argument(
        "--mode",
        choices=("official", "smoke"),
        default="official",
        help="official=真实嵌入正式基线；smoke=Mock 嵌入仅验链路",
    )
    parser.add_argument("--db", default=str(DEFAULT_DB), help="评测索引 SQLite 路径（每次重建）")
    parser.add_argument("--retrieval-depth", type=int, default=10, help="每次检索取回的分块数")
    parser.add_argument("--out", default=None, help="单次运行的报告 JSON 输出路径")
    parser.add_argument(
        "--rrf-k",
        type=int,
        default=None,
        help="本次进程的 RRF k；不写回配置文件，结束时改回进入前的值；省略则用 RAG_HYBRID_RRF_K",
    )
    parser.add_argument(
        "--rrf-sweep",
        default=None,
        help="逗号分隔的 k 列表，必须含 60。索引只建一次，例如 60,40,80",
    )
    parser.add_argument(
        "--rerank",
        choices=("lexical",),
        default=None,
        help="lexical=只重排已经截断的深度 10；省略则保持现有融合顺序",
    )
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
