"""功能概述
在隔离索引上比较已截断混合检索与稠密语义相似度重排。

功能涵盖
- 正式模式使用真实嵌入；冒烟模式使用确定性伪嵌入，只验证链路。
- 同一次建库、同一次检索：对照保留融合顺序，处理只重排这同一批。
- 决策只用 development 与 validation；holdout 仅记录。
- 报告写入新文件，拒绝覆盖 2026-09-19 的冻结基线。
- 不修改检索默认配置，不把候选接到生产读路径。

已完成
- 记录嵌入身份、机器规格、调用量、可选费用估算、延迟、内存、失败切片和采纳结论。
- 冒烟结果不得作为质量或采纳证据。

待完善
- 未把生成层答案正确性纳入本脚本。
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import importlib.util
import os
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.config import settings  # noqa: E402
from app.rag.effective_date import retrieval_window  # noqa: E402
from app.rag.embeddings.base import EmbeddingProvider  # noqa: E402
from app.rag.embeddings.mock import tokenize  # noqa: E402
from app.rag.vectorstore.factory import get_vector_store  # noqa: E402
from app.rag.vectorstore.local import LocalVectorStore  # noqa: E402
from tests.eval.harness import CaseOutcome, score_hits  # noqa: E402
from tests.eval.semantic_rerank import decide_semantic_rerank, rerank_by_semantic_similarity  # noqa: E402
from tests.eval.validation import INDEX_PATH, load_cases, load_json  # noqa: E402

SEMANTIC_DEPTH = 10
SEMANTIC_RRF_K = 60
FROZEN_EVAL_SETTINGS = {
    "RAG_CHUNK_STRATEGY": "structured",
    "RAG_CHUNK_SIZE": 500,
    "RAG_CHUNK_OVERLAP": 64,
    "RAG_VECTOR_STORE": "local",
    "RAG_BACKEND": "native",
    "RAG_EFFECTIVE_DATE_FILTER": False,
}
DEFAULT_DB = REPO_ROOT / "data" / "eval_rag_v01_semantic.db"
REPORT_DIR = REPO_ROOT / "evals" / "reports"
EVAL_DOC_DIR = REPO_ROOT / "docs" / "evaluations"
TRACKED_PACKAGES = ("numpy", "sqlmodel", "pydantic", "httpx", "openai")



def _hook_safe_git_commit(value: str) -> str:
    sha = (value or "").strip()
    if len(sha) == 40 and all(ch in "0123456789abcdefABCDEF" for ch in sha):
        return f"git {sha}"
    return value


def _load_baseline_runner():
    """按文件路径加载已有基线运行器，复用护栏与报告辅助函数。"""
    path = REPO_ROOT / "scripts" / "run_rag_baseline.py"
    spec = importlib.util.spec_from_file_location("run_rag_baseline_for_semantic", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"无法加载基线运行器：{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = _load_baseline_runner()


class CountingEmbeddingProvider(EmbeddingProvider):
    """给已有嵌入提供商计数，不改变向量结果。"""

    def __init__(self, inner: EmbeddingProvider) -> None:
        self._inner = inner
        self.model = getattr(inner, "model", "unknown")
        self.dim = int(getattr(inner, "dim", 0) or 0)
        self.input_policy = getattr(inner, "input_policy", None)
        self.call_count = 0
        self.text_count = 0
        self.char_count = 0

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        batch = list(texts)
        self.call_count += 1
        self.text_count += len(batch)
        self.char_count += sum(len(item) for item in batch)
        return await self._inner.embed(batch)



def apply_frozen_eval_settings() -> dict:
    """在本进程临时改用冻结基线的索引期配置，结束时由调用方写回。

    作用：评测必须与 2026-09-19 基线同一切分和本地向量库，但不能改配置文件。
    入参：无。
    出参：进入前的配置快照。
    """
    previous = {name: getattr(settings, name) for name in FROZEN_EVAL_SETTINGS}
    for name, value in FROZEN_EVAL_SETTINGS.items():
        setattr(settings, name, value)
    return previous


def process_memory_bytes() -> dict[str, int | None]:
    """读取当前进程的常驻内存和峰值，失败时对应字段为 None。"""
    rss: int | None = None
    peak: int | None = None
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCountersEx(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCountersEx()
        counters.cb = ctypes.sizeof(counters)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        # 必须声明参数类型，否则当前进程伪句柄 -1 会被截断，调用失败。
        psapi = ctypes.WinDLL("psapi")
        get_info = psapi.GetProcessMemoryInfo
        get_info.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCountersEx),
            wintypes.DWORD,
        ]
        get_info.restype = wintypes.BOOL
        if get_info(handle, ctypes.byref(counters), counters.cb):
            rss = int(counters.WorkingSetSize)
            peak = int(counters.PeakWorkingSetSize)
    else:
        try:
            page_size = os.sysconf("SC_PAGE_SIZE")
            with open("/proc/self/statm", encoding="utf-8") as handle:
                parts = handle.read().split()
            rss = int(parts[1]) * page_size
        except (OSError, IndexError, ValueError):
            rss = None
        try:
            import resource

            usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            # Linux 为 KB，macOS 为字节。
            peak = int(usage * 1024) if sys.platform.startswith("linux") else int(usage)
        except (OSError, ValueError):
            peak = None
    return {"rss_bytes": rss, "peak_bytes": peak}


def package_versions() -> dict[str, str]:
    """记录与检索评测相关的已安装包版本。缺失的包记为 missing。"""
    versions: dict[str, str] = {}
    for name in TRACKED_PACKAGES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "missing"
    return versions


def estimate_cost_yuan(text_count: int, char_count: int) -> dict[str, Any]:
    """在已配置单价时估算嵌入费用，未配置则只保留调用量。

    作用：不编造官方账单。字符按 UTF-8 字节数的四分之一作保守 token 估计。
    """
    estimated_tokens = max(char_count // 4, 0)
    unit_price = settings.EVALUATION_EMBEDDING_YUAN_PER_MTOK
    cost: float | None = None
    if unit_price is not None:
        cost = float(unit_price) * estimated_tokens / 1_000_000
    return {
        "unit_price_yuan_per_mtok": unit_price,
        "price_model": settings.EVALUATION_EMBEDDING_PRICE_MODEL or None,
        "price_reference": settings.EVALUATION_EMBEDDING_PRICE_REFERENCE or None,
        "token_estimate_method": "utf8_bytes_divided_by_4",
        "estimated_tokens": estimated_tokens,
        "estimated_cost_yuan": cost,
        "cost_is_estimate": True,
    }


def _fmt(value: float | None, digits: int = 4) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _classify_failure(exc: BaseException) -> str:
    name = type(exc).__name__
    lowered = name.lower()
    message = str(exc).lower()
    if "timeout" in lowered or "timeout" in message:
        return "TIMEOUT"
    if "memory" in lowered or "memory" in message:
        return "RESOURCE_LIMIT"
    return f"MODEL_OR_RUNTIME_FAILURE:{name}"


def _failed_case(case: dict[str, Any], latency_ms: float, reason: str) -> CaseOutcome:
    """检索失败时记一条可汇总的失败结果，不中断整轮。"""
    expected = list(case["expected_document_ids"])
    return CaseOutcome(
        case_id=case["case_id"],
        split=case["split"],
        tier=case["tier"],
        category=case["category"],
        review_status=case["provenance"]["review_status"],
        tenant_id=case["identity"]["tenant_id"],
        should_answer=case["should_answer"],
        expected_document_ids=sorted(expected),
        forbidden_document_ids=sorted(case["forbidden_document_ids"]),
        retrieved_document_ids=[],
        retrieved_chunk_count=0,
        recall={1: 0.0 if expected else None, 5: 0.0 if expected else None, 10: 0.0 if expected else None},
        reciprocal_rank=0.0 if expected else None,
        ndcg_at_10=0.0 if expected else None,
        evidence_total=len(case["expected_evidence"]),
        evidence_hit=0,
        forbidden_hits=[],
        latency_ms=latency_ms,
        failures=[reason],
    )


def failure_slices(outcomes: list[CaseOutcome]) -> dict[str, Any]:
    """按失败原因和类目汇总切片，便于查看未命中或越权。"""
    by_reason: dict[str, list[str]] = {}
    by_category: dict[str, list[str]] = {}
    for item in outcomes:
        if not item.failures:
            continue
        by_category.setdefault(item.category, []).append(item.case_id)
        for reason in item.failures:
            key = reason.split(":", 1)[0]
            by_reason.setdefault(key, []).append(item.case_id)
    return {
        "by_reason": {key: {"count": len(ids), "case_ids": ids} for key, ids in sorted(by_reason.items())},
        "by_category": {
            key: {"count": len(ids), "case_ids": ids} for key, ids in sorted(by_category.items())
        },
        "timeout_or_model_or_resource": [
            item.to_dict()
            for item in outcomes
            if any(
                failure.startswith("TIMEOUT")
                or failure.startswith("RESOURCE_LIMIT")
                or failure.startswith("MODEL_OR_RUNTIME_FAILURE")
                for failure in item.failures
            )
        ],
    }


def render_markdown(comparison: dict[str, Any]) -> str:
    """把语义重排对照写成 Markdown。冒烟模式会标明不能作为质量证据。"""
    control = comparison["control"]["tuning"]
    treatment = comparison["treatment"]["tuning"]
    holdout_control = comparison["control"]["holdout"]
    holdout_treatment = comparison["treatment"]["holdout"]
    machine = comparison["machine"]
    usage = comparison["embedding_usage"]
    memory = comparison["memory"]
    quality_note = (
        "本轮是冒烟链路验证，使用 Mock 嵌入，不能作为质量或采纳证据。"
        if not comparison["quality_evidence"]
        else "决策只使用 development 与 validation。holdout 仅记录，不参与判断，也不改公式。"
    )
    lines = [
        "# 稠密语义相似度重排对照",
        "",
        "> 变量：`semantic_similarity`",
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
        f"> {quality_note}",
        "> 重排发生在 hybrid_search 已经截断之后，深度 10，融合常数 60。没有第二次检索，也没有改 Embedding 或切分。",
        "> 未把该候选接到生产检索路径，默认行为保持融合顺序。",
        "> 本文件不改写 `evals/reports/rag-v0.1-baseline-20260919.json`。",
        "",
        "## 1. 结论",
        "",
        comparison["reason"],
        "",
        f"决策：`{comparison['decision']}`。原因码：`{comparison['reason_code']}`。",
        (
            f"索引文档 {comparison['indexed_documents']} 篇，"
            f"分块 {comparison['indexed_chunks']} 个。"
        ),
        "对照顺序是已截断的倒数排名融合顺序。处理顺序只按稠密余弦相似度重排这同一批。",
        "",
        "## 2. 调参 split（development + validation）",
        "",
        "| 顺序 | Recall@1 | Recall@5 | MRR | 越权案例 | p95(ms) |",
        "|---|---|---|---|---|---|",
        (
            f"| 对照 | {_fmt(control['recall']['@1'])} | {_fmt(control['recall']['@5'])} | "
            f"{_fmt(control['mrr'])} | {control['safety']['violation_cases']} | "
            f"{_fmt(control['latency_ms']['p95'], 1)} |"
        ),
        (
            f"| 语义相似度 | {_fmt(treatment['recall']['@1'])} | {_fmt(treatment['recall']['@5'])} | "
            f"{_fmt(treatment['mrr'])} | {treatment['safety']['violation_cases']} | "
            f"{_fmt(treatment['latency_ms']['p95'], 1)} |"
        ),
        "",
        "## 3. holdout（不参与决策）",
        "",
        "| 顺序 | Recall@1 | Recall@5 | MRR | 越权案例 | p95(ms) |",
        "|---|---|---|---|---|---|",
        (
            f"| 对照 | {_fmt(holdout_control['recall']['@1'])} | {_fmt(holdout_control['recall']['@5'])} | "
            f"{_fmt(holdout_control['mrr'])} | {holdout_control['safety']['violation_cases']} | "
            f"{_fmt(holdout_control['latency_ms']['p95'], 1)} |"
        ),
        (
            f"| 语义相似度 | {_fmt(holdout_treatment['recall']['@1'])} | {_fmt(holdout_treatment['recall']['@5'])} | "
            f"{_fmt(holdout_treatment['mrr'])} | {holdout_treatment['safety']['violation_cases']} | "
            f"{_fmt(holdout_treatment['latency_ms']['p95'], 1)} |"
        ),
        "",
        "## 4. 调用量、费用、内存与失败切片",
        "",
        (
            f"嵌入调用 {usage['call_count']} 次，文本 {usage['text_count']} 条，"
            f"字符 {usage['char_count']}。token 估计 {usage['estimated_tokens']}，"
            f"方法为 {usage['token_estimate_method']}。"
        ),
        (
            "已配置单价，估计费用 "
            f"{_fmt(usage['estimated_cost_yuan'])} 元。"
            if usage["estimated_cost_yuan"] is not None
            else "未配置嵌入单价，费用未知，不编造账单。"
        ),
        (
            f"重排内存增量 {memory['rerank_increment_bytes']} 字节；"
            f"运行后常驻 {memory['rss_after_run_bytes']} 字节；"
            f"峰值 {memory['peak_bytes']} 字节。"
        ),
        "",
    ]
    slices = comparison["failure_slices"]
    if slices["by_reason"]:
        lines.append("处理组失败原因：")
        for reason, payload in slices["by_reason"].items():
            lines.append(f"- `{reason}`：{payload['count']} 条，编号 {', '.join(payload['case_ids'])}")
        lines.append("")
    else:
        lines.extend(["处理组没有记录到失败原因。", ""])
    return "\n".join(lines)


async def run_experiment(args: argparse.Namespace) -> int:
    """重建一次索引，检索一次，比较融合顺序与语义重排。"""
    previous_settings = apply_frozen_eval_settings()
    try:
        return await _run_experiment(args)
    finally:
        for name, value in previous_settings.items():
            setattr(settings, name, value)


async def _run_experiment(args: argparse.Namespace) -> int:
    """在已切换到冻结评测配置的进程内执行对照。"""
    if args.rrf_k not in (None, SEMANTIC_RRF_K):
        raise SystemExit("语义重排实验固定融合常数 60，不能同时改 k")
    if args.retrieval_depth != SEMANTIC_DEPTH:
        raise SystemExit("语义重排实验固定检索深度 10")
    if int(settings.RAG_HYBRID_RRF_K) != SEMANTIC_RRF_K:
        raise SystemExit(
            f"语义重排实验固定 k=60，当前 RAG_HYBRID_RRF_K={settings.RAG_HYBRID_RRF_K!r}"
        )

    raw_provider = runner.resolve_embedding(args.mode)
    provider = CountingEmbeddingProvider(raw_provider)
    if args.mode == "official":
        runner._require_frozen_index_config()

    db_path = Path(args.db)
    print(f"[1/3] 重建评测索引（一次）-> {db_path}")
    memory_before_index = process_memory_bytes()
    index = await runner.build_eval_index(db_path, provider)
    memory_after_index = process_memory_bytes()
    control_outcomes: list[CaseOutcome] = []
    treatment_outcomes: list[CaseOutcome] = []
    rerank_increment = 0
    try:
        store = get_vector_store(index.session)
        if not isinstance(store, LocalVectorStore):
            raise SystemExit("语义重排实验只使用本地向量库返回的已截断结果")
        cases = sorted(load_cases(), key=lambda item: item["case_id"])
        print(f"[2/3] 检索并重排 {len(cases)} 条案例（深度 {SEMANTIC_DEPTH}，k={SEMANTIC_RRF_K}）")
        for case in cases:
            query = case["query"]
            tokens = tokenize(query)
            as_of, schedule_at = retrieval_window(query)
            started = time.perf_counter()
            try:
                vector = (await provider.embed([query]))[0]
                hits = await store.hybrid_search(
                    vector,
                    tokens,
                    case["identity"]["tenant_id"],
                    SEMANTIC_DEPTH,
                    SEMANTIC_RRF_K,
                    as_of=as_of,
                    schedule_at=schedule_at,
                )
            except Exception as exc:  # noqa: BLE001 — 记录单例失败，不中断整轮
                latency_ms = (time.perf_counter() - started) * 1000.0
                failed = _failed_case(case, latency_ms, _classify_failure(exc))
                control_outcomes.append(failed)
                treatment_outcomes.append(failed)
                continue
            retrieve_ms = (time.perf_counter() - started) * 1000.0
            if len(hits) > SEMANTIC_DEPTH:
                raise SystemExit("hybrid_search 返回条数超过深度 10，已停止")
            before_rerank = process_memory_bytes()["rss_bytes"]
            rerank_started = time.perf_counter()
            reranked = rerank_by_semantic_similarity(hits)
            rerank_ms = (time.perf_counter() - rerank_started) * 1000.0
            after_rerank = process_memory_bytes()["rss_bytes"]
            if before_rerank is not None and after_rerank is not None:
                rerank_increment = max(rerank_increment, after_rerank - before_rerank)
            if sorted(hit.id for hit in reranked) != sorted(hit.id for hit in hits):
                raise SystemExit("重排改变了候选集合，已停止")
            control_outcomes.append(score_hits(case, index, hits, latency_ms=retrieve_ms))
            treatment_outcomes.append(
                score_hits(case, index, reranked, latency_ms=retrieve_ms + rerank_ms)
            )
    finally:
        index.close()

    memory_after_run = process_memory_bytes()
    config = runner.build_run_config(args.mode, provider._inner, SEMANTIC_DEPTH)
    config["indexed_documents"] = len(index.db_doc_id_to_logical)
    config["indexed_chunks"] = index.chunk_total
    config["variable"] = "semantic_similarity"
    config["rerank_after_truncation"] = True
    config["candidate_pool_expanded"] = False
    config["machine"] = runner.machine_spec()
    command = (
        "python evals/semantic_rerank/run.py --mode official"
        if args.mode == "official"
        else "python evals/semantic_rerank/run.py --mode smoke"
    )
    config["command"] = command
    config["working_directory"] = str(REPO_ROOT)
    control_report = runner.make_report(config, control_outcomes)
    treatment_report = runner.make_report(config, treatment_outcomes)
    quality_evidence = args.mode == "official"
    if quality_evidence:
        decision = decide_semantic_rerank(
            control_report["decision_inputs"]["summary"],
            treatment_report["decision_inputs"]["summary"],
            memory_increment_bytes=rerank_increment,
        )
    else:
        decision = {
            "decision": "not_evaluated",
            "reason_code": "mock_not_quality_evidence",
            "failed_gates": ["mock_not_quality_evidence"],
            "reason": "冒烟模式只证明链路，不能作为质量或采纳证据",
        }

    usage_cost = estimate_cost_yuan(provider.text_count, provider.char_count)
    usage_cost["call_count"] = provider.call_count
    usage_cost["text_count"] = provider.text_count
    usage_cost["char_count"] = provider.char_count

    dataset_index = load_json(INDEX_PATH)
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    suffix = "" if args.mode == "official" else "-smoke"
    comparison = {
        "experiment": "semantic-similarity-rerank",
        "variable": "semantic_similarity",
        "run_at": datetime.now(UTC).isoformat(),
        # 完整 SHA 前加 git 前缀，避免 staged-secrets 把 40 位十六进制误报为密钥。
        "git_commit": _hook_safe_git_commit(config["git_commit"]),
        "dataset_id": dataset_index["dataset_id"],
        "dataset_version": dataset_index["dataset_version"],
        "corpus_version": dataset_index["corpus_version"],
        "gold_status": dataset_index["gold_status"],
        "mode": args.mode,
        "quality_evidence": quality_evidence,
        "embedding_provider": config["embedding_provider"],
        "embedding_model": config["embedding_model"],
        "embedding_dim": config["embedding_dim"],
        "embedding_index_version": settings.EMBEDDING_INDEX_VERSION,
        "embedding_normalization": settings.EMBEDDING_NORMALIZATION,
        "embedding_metric": settings.EMBEDDING_METRIC,
        "vector_store": config["vector_store"],
        "rag_backend": config["rag_backend"],
        "chunk_strategy": config["chunk_strategy"],
        "chunk_size": config["chunk_size"],
        "chunk_overlap": config["chunk_overlap"],
        "rrf_k": SEMANTIC_RRF_K,
        "retrieval_depth_chunks": SEMANTIC_DEPTH,
        "indexed_documents": config["indexed_documents"],
        "indexed_chunks": config["indexed_chunks"],
        "machine": config["machine"],
        "package_versions": package_versions(),
        "command": command,
        "working_directory": config["working_directory"],
        "decision_splits": sorted(runner.TUNING_SPLITS),
        "holdout_used_for_decision": False,
        "production_enabled": False,
        "decision": decision["decision"],
        "reason_code": decision["reason_code"],
        "failed_gates": decision.get("failed_gates", []),
        "reason": decision["reason"],
        "adoption_rule": (
            "调参集 Recall@1 提高至少 0.03，MRR 提高至少 0.02，Recall@5 下降不超过 0.01，"
            "处理组越权案例为 0 且不高于对照，p95 增量不超过 20%，重排内存增量不超过 256 MiB"
        ),
        "thresholds": decision.get("thresholds"),
        "embedding_usage": usage_cost,
        "memory": {
            "rss_before_index_bytes": memory_before_index["rss_bytes"],
            "rss_after_index_bytes": memory_after_index["rss_bytes"],
            "rss_after_run_bytes": memory_after_run["rss_bytes"],
            "peak_bytes": memory_after_run["peak_bytes"],
            "rerank_increment_bytes": rerank_increment,
            "increment_limit_bytes": 256 * 1024 * 1024,
        },
        "failure_slices": failure_slices(treatment_outcomes),
        "control": {
            "order": "rrf_truncated",
            "tuning": control_report["decision_inputs"]["summary"],
            "holdout": control_report["decision_inputs"]["holdout_summary"],
            "overall": control_report["overall"],
            "failing_cases": control_report["failing_cases"],
        },
        "treatment": {
            "order": "semantic_similarity",
            "tuning": treatment_report["decision_inputs"]["summary"],
            "holdout": treatment_report["decision_inputs"]["holdout_summary"],
            "overall": treatment_report["overall"],
            "failing_cases": treatment_report["failing_cases"],
        },
        "cases": {
            "control": [item.to_dict() for item in control_outcomes],
            "treatment": [item.to_dict() for item in treatment_outcomes],
        },
    }
    json_path = runner.write_json(
        REPORT_DIR / f"rag-v0.1-semantic-rerank-{stamp}{suffix}.json",
        comparison,
    )
    md_path = EVAL_DOC_DIR / f"rag-v0.1-semantic-rerank-{stamp}{suffix}.md"
    md_path.write_text(render_markdown(comparison), encoding="utf-8")
    print(f"[3/3] {json_path}")
    print(f"      Markdown {md_path}")
    print(f"      决策 {decision['decision']}")
    print(f"      {decision['reason']}")
    tuning_control = comparison["control"]["tuning"]
    tuning_treatment = comparison["treatment"]["tuning"]
    print(
        "      调参集对照 "
        f"Recall@1={_fmt(tuning_control['recall']['@1'])} "
        f"Recall@5={_fmt(tuning_control['recall']['@5'])} "
        f"MRR={_fmt(tuning_control['mrr'])} "
        f"越权={tuning_control['safety']['violation_cases']} "
        f"p95={_fmt(tuning_control['latency_ms']['p95'], 1)}ms"
    )
    print(
        "      调参集处理 "
        f"Recall@1={_fmt(tuning_treatment['recall']['@1'])} "
        f"Recall@5={_fmt(tuning_treatment['recall']['@5'])} "
        f"MRR={_fmt(tuning_treatment['mrr'])} "
        f"越权={tuning_treatment['safety']['violation_cases']} "
        f"p95={_fmt(tuning_treatment['latency_ms']['p95'], 1)}ms"
    )
    print(
        f"      嵌入调用 {provider.call_count} 次，文本 {provider.text_count} 条，"
        f"估计 token {usage_cost['estimated_tokens']}，"
        f"估计费用 {usage_cost['estimated_cost_yuan'] if usage_cost['estimated_cost_yuan'] is not None else '未知'}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="运行稠密语义相似度重排单变量对照")
    parser.add_argument(
        "--mode",
        choices=("official", "smoke"),
        default="official",
        help="official=真实嵌入并给出采纳结论；smoke=Mock 嵌入仅验链路",
    )
    parser.add_argument("--db", default=str(DEFAULT_DB), help="评测索引 SQLite 路径（每次重建）")
    parser.add_argument("--retrieval-depth", type=int, default=SEMANTIC_DEPTH, help="固定为 10")
    parser.add_argument(
        "--rrf-k",
        type=int,
        default=None,
        help="必须省略或等于 60；本实验不改融合常数",
    )
    args = parser.parse_args()
    return asyncio.run(run_experiment(args))


if __name__ == "__main__":
    raise SystemExit(main())
