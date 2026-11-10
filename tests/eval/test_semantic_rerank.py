"""稠密语义相似度重排的次序和预注册采纳门槛。"""

from types import SimpleNamespace

from tests.eval.semantic_rerank import (
    MEMORY_INCREMENT_LIMIT_BYTES,
    decide_semantic_rerank,
    rerank_by_semantic_similarity,
)


def _chunk(content: str, similarity: float, chunk_id: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        id=chunk_id or content,
        content=content,
        similarity=similarity,
    )


def _summary(
    recall_at_1: float | None,
    mrr: float | None,
    *,
    recall_at_5: float | None = None,
    violations: int = 0,
    p95: float | None = 100.0,
) -> dict:
    if recall_at_5 is None:
        recall_at_5 = recall_at_1
    return {
        "recall": {"@1": recall_at_1, "@5": recall_at_5},
        "mrr": mrr,
        "safety": {"violation_cases": violations},
        "latency_ms": {"p95": p95},
    }


def test_rerank_puts_higher_similarity_first() -> None:
    """相似度更高的分块排到前面。"""
    chunks = [_chunk("低分", 0.1), _chunk("高分", 0.9)]
    ordered = rerank_by_semantic_similarity(chunks)
    assert [item.content for item in ordered] == ["高分", "低分"]
    assert chunks[0].content == "低分"


def test_rerank_keeps_original_order_on_ties() -> None:
    """相似度相同的条目保持截断后的原次序，并且不增删。"""
    chunks = [_chunk("甲", 0.5), _chunk("乙", 0.5), _chunk("丙", 0.4)]
    ordered = rerank_by_semantic_similarity(chunks)
    assert [item.content for item in ordered] == ["甲", "乙", "丙"]
    assert [item.id for item in ordered] == [item.id for item in chunks]


def test_decide_adopts_only_when_all_preregistered_gates_pass() -> None:
    """质量、安全、延迟同时达标且内存未超限才采纳。"""
    control = _summary(0.50, 0.50, recall_at_5=0.90, p95=100.0)
    adopted = decide_semantic_rerank(
        control,
        _summary(0.54, 0.53, recall_at_5=0.90, p95=110.0),
        memory_increment_bytes=1024,
    )
    assert adopted["decision"] == "adopt"
    assert adopted["reason_code"] == "adopt"
    assert adopted["failed_gates"] == []


def test_decide_keeps_when_quality_or_safety_misses() -> None:
    """主指标不足、Recall@5 下降过多或越权出现时保持对照。"""
    control = _summary(0.50, 0.50, recall_at_5=0.90, p95=100.0)

    recall_short = decide_semantic_rerank(
        control, _summary(0.52, 0.53, recall_at_5=0.90, p95=100.0)
    )
    assert recall_short["decision"] == "keep"
    assert "recall_at_1_shortfall" in recall_short["failed_gates"]

    mrr_short = decide_semantic_rerank(
        control, _summary(0.54, 0.51, recall_at_5=0.90, p95=100.0)
    )
    assert "mrr_shortfall" in mrr_short["failed_gates"]

    recall5 = decide_semantic_rerank(
        control, _summary(0.54, 0.53, recall_at_5=0.88, p95=100.0)
    )
    assert "recall_at_5_regression" in recall5["failed_gates"]

    unsafe = decide_semantic_rerank(
        control, _summary(0.80, 0.80, recall_at_5=0.95, violations=1, p95=100.0)
    )
    assert unsafe["reason_code"] == "safety_regression"
    assert unsafe["failed_gates"][0] == "safety_regression"


def test_decide_keeps_when_latency_or_memory_exceeds_limit() -> None:
    """p95 增量超过 20% 或内存增量超过 256 MiB 时不采纳。"""
    control = _summary(0.50, 0.50, recall_at_5=0.90, p95=100.0)
    slow = decide_semantic_rerank(
        control, _summary(0.54, 0.53, recall_at_5=0.90, p95=121.0)
    )
    assert "latency_p95_regression" in slow["failed_gates"]
    assert slow["decision"] == "keep"

    heavy = decide_semantic_rerank(
        control,
        _summary(0.54, 0.53, recall_at_5=0.90, p95=100.0),
        memory_increment_bytes=MEMORY_INCREMENT_LIMIT_BYTES + 1,
    )
    assert "memory_limit" in heavy["failed_gates"]


def test_decide_keeps_when_primary_metric_missing() -> None:
    """缺主指标时不能写成采纳。"""
    control = _summary(0.50, 0.50, p95=100.0)
    missing = decide_semantic_rerank(control, _summary(None, 0.90, p95=100.0))
    assert missing["decision"] == "keep"
    assert missing["reason_code"] == "missing_primary_metric"
