"""功能概述
对已经截断的混合检索结果按稠密语义相似度重排，并按预注册门槛决定是否采纳。

功能涵盖
- 重排只改变已截断候选的顺序，不增删条目，也不扩大候选池。
- 排序键是命中上的稠密余弦相似度；同分保持原次序。
- 决策只使用 development 与 validation 的汇总，本模块不读取 holdout。
- 质量门槛预注册为：Recall@1 至少提高 0.03，MRR 至少提高 0.02，Recall@5 下降不超过 0.01，
  处理组越权案例数为 0 且不高于对照，端到端 p95 延迟增量不超过 20%，
  重排引入的常驻内存增量不超过 256 MiB。

已完成
- 重排与采纳规则均为纯函数，不依赖真实网络调用。
- 未达门槛时返回不采纳及可检查的原因码，不把部分达标写成采纳。

待完善
- 不在这里改检索实现，也不默认打开生产重排。
- 本候选使用检索已计算的稠密相似度，不再次调用嵌入或独立交叉编码器。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, TypeVar

T = TypeVar("T")

RECALL_AT_1_GAIN = 0.03
MRR_GAIN = 0.02
RECALL_AT_5_MAX_DROP = 0.01
LATENCY_P95_MAX_RATIO = 1.20
MEMORY_INCREMENT_LIMIT_BYTES = 256 * 1024 * 1024


def rerank_by_semantic_similarity(chunks: Sequence[T]) -> list[T]:
    """按稠密语义相似度重排已经截断的命中。

    作用：不增删条目。相似度高的在前；相似度相同则保持原来的次序。
    入参：chunks 为已截断列表，元素需有 similarity 属性。
    出参：新的列表。原列表不被就地修改。
    """
    indexed = list(enumerate(chunks))
    indexed.sort(key=lambda item: (-float(item[1].similarity), item[0]))
    return [item[1] for item in indexed]


def decide_semantic_rerank(
    control_summary: dict[str, Any],
    treatment_summary: dict[str, Any],
    *,
    memory_increment_bytes: int | None = None,
) -> dict[str, Any]:
    """决定语义相似度重排是否采纳。

    作用：只看调参集汇总和成对的延迟、内存增量。全部门槛同时满足才采纳。
    入参：两份汇总都需含 recall['@1']、recall['@5']、mrr、safety.violation_cases、
    latency_ms.p95。不要传入 holdout。memory_increment_bytes 为处理相对对照的常驻内存增量。
    出参：字典，含 decision（adopt 或 keep）、reason_code、failed_gates、reason。
    """
    failed = _failed_gates(control_summary, treatment_summary, memory_increment_bytes)
    control_recall_1 = control_summary["recall"]["@1"]
    treatment_recall_1 = treatment_summary["recall"]["@1"]
    control_recall_5 = control_summary["recall"]["@5"]
    treatment_recall_5 = treatment_summary["recall"]["@5"]
    control_mrr = control_summary["mrr"]
    treatment_mrr = treatment_summary["mrr"]
    control_p95 = control_summary["latency_ms"]["p95"]
    treatment_p95 = treatment_summary["latency_ms"]["p95"]
    if failed:
        reason_code = failed[0]
        reason = "不采纳 semantic_similarity：调参集未同时满足预注册质量、安全、延迟和内存门槛"
        decision = "keep"
    else:
        reason_code = "adopt"
        reason = (
            "采纳 semantic_similarity：调参集 Recall@1 至少提高 0.03，MRR 至少提高 0.02，"
            "Recall@5 下降不超过 0.01，越权案例为 0，p95 增量不超过 20%，内存增量未超上限"
        )
        decision = "adopt"
    return {
        "decision": decision,
        "reason_code": reason_code,
        "failed_gates": failed,
        "reason": reason,
        "control_recall@1": control_recall_1,
        "treatment_recall@1": treatment_recall_1,
        "control_recall@5": control_recall_5,
        "treatment_recall@5": treatment_recall_5,
        "control_mrr": control_mrr,
        "treatment_mrr": treatment_mrr,
        "control_p95_ms": control_p95,
        "treatment_p95_ms": treatment_p95,
        "memory_increment_bytes": memory_increment_bytes,
        "thresholds": {
            "recall@1_gain": RECALL_AT_1_GAIN,
            "mrr_gain": MRR_GAIN,
            "recall@5_max_drop": RECALL_AT_5_MAX_DROP,
            "latency_p95_max_ratio": LATENCY_P95_MAX_RATIO,
            "memory_increment_limit_bytes": MEMORY_INCREMENT_LIMIT_BYTES,
            "treatment_violation_cases": 0,
        },
    }


def _failed_gates(
    control: dict[str, Any],
    treatment: dict[str, Any],
    memory_increment_bytes: int | None,
) -> list[str]:
    """按固定优先级检查未满足的门槛。

    作用：安全、完整性、质量、延迟、内存依次检查，不因后面达标而忽略前面失败。
    入参：对照与处理汇总，以及可选的内存增量。
    出参：失败原因码列表，空列表表示全部通过。
    """
    failed: list[str] = []
    treatment_violations = treatment["safety"]["violation_cases"]
    control_violations = control["safety"]["violation_cases"]
    if treatment_violations > control_violations or treatment_violations > 0:
        failed.append("safety_regression")

    control_recall_1 = control["recall"]["@1"]
    treatment_recall_1 = treatment["recall"]["@1"]
    control_recall_5 = control["recall"]["@5"]
    treatment_recall_5 = treatment["recall"]["@5"]
    control_mrr = control["mrr"]
    treatment_mrr = treatment["mrr"]
    control_p95 = control["latency_ms"]["p95"]
    treatment_p95 = treatment["latency_ms"]["p95"]
    metrics = (
        control_recall_1,
        treatment_recall_1,
        control_recall_5,
        treatment_recall_5,
        control_mrr,
        treatment_mrr,
        control_p95,
        treatment_p95,
    )
    if any(value is None for value in metrics):
        failed.append("missing_primary_metric")
        return failed

    if treatment_recall_1 < control_recall_1 + RECALL_AT_1_GAIN:
        failed.append("recall_at_1_shortfall")
    if treatment_mrr < control_mrr + MRR_GAIN:
        failed.append("mrr_shortfall")
    if treatment_recall_5 < control_recall_5 - RECALL_AT_5_MAX_DROP:
        failed.append("recall_at_5_regression")
    if control_p95 <= 0:
        if treatment_p95 > 0:
            failed.append("latency_p95_regression")
    elif treatment_p95 > control_p95 * LATENCY_P95_MAX_RATIO:
        failed.append("latency_p95_regression")
    if (
        memory_increment_bytes is not None
        and memory_increment_bytes > MEMORY_INCREMENT_LIMIT_BYTES
    ):
        failed.append("memory_limit")
    return failed
