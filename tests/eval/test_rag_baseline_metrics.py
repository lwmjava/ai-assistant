"""RAG-005 基线指标的确定性单测。

指标函数必须先于运行器可验证：手算样例锁定 Recall/MRR/nDCG/分位数/引用命中的语义，
避免基线报告里的分数来自未经检验的实现。
"""

from __future__ import annotations

import pytest

from tests.eval.metrics import (
    dedupe_preserving_order,
    ndcg_at_k,
    percentile,
    quote_hit,
    recall_at_k,
    reciprocal_rank,
)

RANKED = ["d1", "d2", "d3", "d4", "d5"]
EXPECTED = {"d2", "d5"}


def test_dedupe_keeps_first_occurrence_order():
    assert dedupe_preserving_order(["d3", "d1", "d3", "d2", "d1"]) == ["d3", "d1", "d2"]


@pytest.mark.parametrize(
    ("k", "expected_value"),
    [(1, 0.0), (2, 0.5), (3, 0.5), (5, 1.0), (10, 1.0)],
)
def test_recall_at_k_is_share_of_expected_documents_found(k, expected_value):
    assert recall_at_k(RANKED, EXPECTED, k) == pytest.approx(expected_value)


def test_recall_without_expected_documents_is_undefined():
    """无期望文档的案例（no_answer/private_resource）不得计入排序指标。"""
    assert recall_at_k(RANKED, set(), 5) is None


def test_reciprocal_rank_uses_first_relevant_position():
    assert reciprocal_rank(RANKED, EXPECTED) == pytest.approx(0.5)
    assert reciprocal_rank(RANKED, {"d1"}) == pytest.approx(1.0)
    assert reciprocal_rank(RANKED, {"d9"}) == pytest.approx(0.0)
    assert reciprocal_rank(RANKED, set()) is None


def test_ndcg_binary_gain_exact_cases():
    # 命中排在第 2 位：DCG = 1/log2(3)，IDCG = 1/log2(2) = 1。
    assert ndcg_at_k(["d1", "d2"], {"d2"}, 2) == pytest.approx(0.6309297535714574)
    # 命中排在首位即理想排序。
    assert ndcg_at_k(["d2", "d1"], {"d2"}, 2) == pytest.approx(1.0)
    # 截断到 k 之外的命中不计分。
    assert ndcg_at_k(RANKED, {"d5"}, 3) == pytest.approx(0.0)
    assert ndcg_at_k(RANKED, set(), 5) is None


def test_ndcg_multi_relevant_matches_hand_computation():
    dcg = 1 / 1.5849625007211562 + 1 / 2.584962500721156
    idcg = 1.0 + 1 / 1.5849625007211562
    assert ndcg_at_k(RANKED, EXPECTED, 5) == pytest.approx(dcg / idcg)


def test_percentile_uses_nearest_rank():
    values = [10.0, 20.0, 30.0, 40.0]
    assert percentile(values, 50) == pytest.approx(20.0)
    assert percentile(values, 95) == pytest.approx(40.0)
    assert percentile([], 50) is None


def test_quote_hit_ignores_whitespace_and_requires_verbatim_text():
    chunks = ["## 计费\n\n标准套餐月费 199 元，含 5 个坐席。"]
    assert quote_hit(chunks, "标准套餐月费 199 元") is True
    # 换行与空白差异不应导致漏判。
    assert quote_hit(chunks, "标准套餐月费\n199 元") is True
    # 语义相近但非原文不得算命中。
    assert quote_hit(chunks, "标准套餐每月 199 元") is False
    assert quote_hit([], "标准套餐月费 199 元") is False
