"""词面覆盖重排的公式、次序和采纳规则。"""

from types import SimpleNamespace

from tests.eval.lexical_rerank import (
    decide_lexical_coverage,
    lexical_coverage,
    rerank_by_lexical_coverage,
)


def _chunk(content: str) -> SimpleNamespace:
    return SimpleNamespace(content=content)


def _summary(recall_at_1: float | None, mrr: float | None, violations: int) -> dict:
    return {
        "recall": {"@1": recall_at_1},
        "mrr": mrr,
        "safety": {"violation_cases": violations},
    }


def test_coverage_is_query_set_overlap() -> None:
    """覆盖率等于查询词集合与分块词集合的交集比例。"""
    tokens = ["退", "票", "费"]
    assert lexical_coverage(tokens, "退票手续费") == 1.0
    assert lexical_coverage(tokens, "退票") == 2 / 3
    assert lexical_coverage([], "退票") == 0.0


def test_rerank_keeps_original_order_on_ties() -> None:
    """覆盖率相同的条目保持截断后的原次序，并且不增删。"""
    chunks = [_chunk("甲"), _chunk("乙乙"), _chunk("丙")]
    ordered = rerank_by_lexical_coverage(["甲"], chunks)
    assert [item.content for item in ordered] == ["甲", "乙乙", "丙"]
    assert chunks[0].content == "甲"


def test_rerank_puts_higher_coverage_first() -> None:
    """覆盖率更高的分块排到前面。"""
    chunks = [_chunk("无关内容"), _chunk("退票手续费")]
    ordered = rerank_by_lexical_coverage(["退", "票"], chunks)
    assert [item.content for item in ordered] == ["退票手续费", "无关内容"]


def test_decide_adopts_only_when_both_metrics_rise_without_more_violations() -> None:
    """两项都提高且越权不增加才采纳。只提高一项、指标下降或越权增加都保持对照。"""
    control = _summary(0.5, 0.5, 0)
    adopted = decide_lexical_coverage(control, _summary(0.6, 0.7, 0))
    assert adopted["decision"] == "adopt"
    assert adopted["reason_code"] == "adopt"

    held = decide_lexical_coverage(control, _summary(0.6, 0.5, 0))
    assert held["decision"] == "keep"
    assert held["reason_code"] == "not_both_primary_improved"

    regressed = decide_lexical_coverage(control, _summary(0.4, 0.6, 0))
    assert regressed["reason_code"] == "primary_regression"

    unsafe = decide_lexical_coverage(control, _summary(0.9, 0.9, 1))
    assert unsafe["reason_code"] == "safety_regression"

    missing = decide_lexical_coverage(control, _summary(None, 0.9, 0))
    assert missing["reason_code"] == "missing_primary_metric"
