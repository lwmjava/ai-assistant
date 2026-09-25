"""功能概述
锁定融合常数的采纳规则，以及基线运行器不会覆盖冻结报告。

功能涵盖
- 只提高一项主指标时保持对照。
- 越权案例增加时拒绝候选。
- 两项主指标都提高时采纳 Recall@1 更高者。
- 运行器拒绝覆盖冻结基线，并且扫描列表必须包含对照常数。

已完成
- 上述规则均有确定性样例，不调用真实嵌入。

待完善
- 未覆盖缺失主指标、主指标回退这两条原因码。
- 未在本文件里跑完整语料扫描。
"""

from __future__ import annotations

import importlib.util

import pytest

from app.core.config import settings
from app.rag.vectorstore.local import _rrf
from tests.eval.rrf_decision import choose_k
from tests.eval.validation import REPO_ROOT


def _summary(recall_at_1: float, mrr: float, violations: int = 0) -> dict:
    """构造一份最小检索汇总，供决策函数使用。

    作用：避免测试依赖真实评测报告。
    入参：recall_at_1、mrr 为两项主指标；violations 为越权案例数。
    出参：与评测夹具 summarize 兼容的字典。
    """
    return {
        "recall": {"@1": recall_at_1, "@5": recall_at_1, "@10": recall_at_1},
        "mrr": mrr,
        "ndcg@10": recall_at_1,
        "citation": {"quote_coverage": 1.0},
        "safety": {"violation_cases": violations, "cases_with_forbidden_docs": 1},
    }


def test_keep_when_only_one_primary_metric_improves():
    """只提高 Recall@1 或只提高 MRR 时，必须保持对照。"""
    decision = choose_k(
        60,
        _summary(0.5, 0.5),
        [(40, _summary(0.8, 0.5)), (80, _summary(0.5, 0.8))],
    )
    assert decision["decision"] == "keep"
    assert decision["adopted_k"] == 60
    assert {item["reason"] for item in decision["candidates"]} == {"not_both_primary_improved"}


def test_reject_safety_regression_even_if_ranking_improves():
    """两项主指标都提高但越权案例增加时，必须拒绝。"""
    decision = choose_k(
        60,
        _summary(0.4, 0.4, violations=0),
        [(40, _summary(0.9, 0.9, violations=1))],
    )
    assert decision["decision"] == "keep"
    assert decision["candidates"][0]["reason"] == "safety_regression"


def test_adopt_candidate_that_improves_both_primary_metrics():
    """两个候选都合格时，采纳 Recall@1 更高的常数。"""
    decision = choose_k(
        60,
        _summary(0.4, 0.4),
        [(40, _summary(0.6, 0.55)), (80, _summary(0.7, 0.5))],
    )
    assert decision["decision"] == "adopt"
    assert decision["adopted_k"] == 80


def test_rrf_score_changes_with_k():
    """融合常数变化时，同一排序的融合分必须变化。不代表检索质量变化。"""
    rankings = [[0, 1, 2], [2, 0, 1]]
    low = dict(_rrf(rankings, k=40))
    high = dict(_rrf(rankings, k=80))
    assert low[0] != pytest.approx(high[0])


def _load_baseline_runner():
    """按文件路径加载基线运行器，避免把它当成已安装包。

    作用：让测试能调用运行器里的护栏函数。
    入参：无。
    出参：已执行的模块对象。
    """
    path = REPO_ROOT / "scripts" / "run_rag_baseline.py"
    spec = importlib.util.spec_from_file_location("run_rag_baseline", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runner_refuses_frozen_baseline(tmp_path):
    """输出路径指向冻结基线文件时必须退出。"""
    runner = _load_baseline_runner()
    with pytest.raises(SystemExit, match="冻结基线"):
        runner.refuse_frozen(runner.FROZEN_BASELINE)


def test_apply_rrf_k_can_be_restored():
    """进程内覆盖融合常数后，测试负责把配置改回原值。"""
    runner = _load_baseline_runner()
    original = settings.RAG_HYBRID_RRF_K
    try:
        assert runner.apply_rrf_k(40) == 40
        assert settings.RAG_HYBRID_RRF_K == 40
    finally:
        settings.RAG_HYBRID_RRF_K = original
    assert settings.RAG_HYBRID_RRF_K == original


def test_sweep_must_include_control_k():
    """扫描列表不含对照常数 60 时必须退出。"""
    runner = _load_baseline_runner()
    with pytest.raises(SystemExit, match="60"):
        runner.parse_sweep("40,80")
