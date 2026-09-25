"""功能概述
锁定融合常数的采纳规则，以及基线运行器不会覆盖冻结报告。

功能涵盖
- 只提高一项主指标时保持对照。
- 越权案例增加时拒绝候选。
- 两项主指标都提高时采纳 Recall@1 更高者。
- 运行器拒绝覆盖冻结基线，并且扫描列表必须包含对照常数。
- 单次评测与扫描结束后，进程内 RAG_HYBRID_RRF_K 写回进入前的值。

已完成
- 上述规则均有确定性样例，不调用真实嵌入。
- 单次成功、单次失败与扫描路径的融合常数恢复均有用例。

待完善
- 未覆盖缺失主指标、主指标回退这两条原因码。
- 未在本文件里跑完整语料扫描。
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from types import SimpleNamespace

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


def _fake_overall() -> dict:
    return {
        "recall": {"@1": 0.5, "@5": 0.5, "@10": 0.5},
        "mrr": 0.5,
        "ndcg@10": 0.5,
        "citation": {"quote_coverage": 1.0},
        "safety": {"violation_cases": 0, "cases_with_forbidden_docs": 1},
        "latency_ms": {"p50": 1.0, "p95": 2.0},
    }


def _stub_eval_io(monkeypatch: pytest.MonkeyPatch, runner, *, fail_cases: bool = False):
    """把索引构建与案例运行换成空操作，避免真实语料与嵌入。"""

    class _FakeIndex:
        db_doc_id_to_logical: dict = {}
        chunk_total = 0

        def close(self) -> None:
            return None

    async def fake_build_eval_index(db_path, provider):
        return _FakeIndex()

    async def fake_run_all_cases(index, provider, retrieval_depth=10):
        if fail_cases:
            raise RuntimeError("simulated case failure")
        return []

    def fake_make_report(config, outcomes):
        summary = _summary(0.5, 0.5)
        return {
            "overall": _fake_overall(),
            "decision_inputs": {
                "summary": summary,
                "holdout_summary": summary,
            },
            "failing_cases": [],
        }

    def fake_write_json(path: Path, payload: dict) -> Path:
        return Path(path)

    monkeypatch.setattr(runner, "build_eval_index", fake_build_eval_index)
    monkeypatch.setattr(runner, "run_all_cases", fake_run_all_cases)
    monkeypatch.setattr(runner, "make_report", fake_make_report)
    monkeypatch.setattr(runner, "write_json", fake_write_json)


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


@pytest.mark.asyncio
async def test_main_async_restores_rrf_k_after_single_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """单次路径返回后，进程内融合常数等于进入前。"""
    runner = _load_baseline_runner()
    _stub_eval_io(monkeypatch, runner)
    original = settings.RAG_HYBRID_RRF_K
    override = 40 if original != 40 else 80
    args = argparse.Namespace(
        mode="smoke",
        db=str(REPO_ROOT / "data" / "eval_rrf_k_restore_test.db"),
        retrieval_depth=10,
        out=None,
        rrf_k=override,
        rrf_sweep=None,
    )
    try:
        code = await runner.main_async(args)
        assert code == 0
        assert settings.RAG_HYBRID_RRF_K == original
    finally:
        settings.RAG_HYBRID_RRF_K = original


@pytest.mark.asyncio
async def test_main_async_restores_rrf_k_when_cases_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """单次路径在案例运行失败后仍写回融合常数。"""
    runner = _load_baseline_runner()
    _stub_eval_io(monkeypatch, runner, fail_cases=True)
    original = settings.RAG_HYBRID_RRF_K
    override = 40 if original != 40 else 80
    args = argparse.Namespace(
        mode="smoke",
        db=str(REPO_ROOT / "data" / "eval_rrf_k_restore_fail.db"),
        retrieval_depth=10,
        out=None,
        rrf_k=override,
        rrf_sweep=None,
    )
    try:
        with pytest.raises(RuntimeError, match="simulated case failure"):
            await runner.main_async(args)
        assert settings.RAG_HYBRID_RRF_K == original
    finally:
        settings.RAG_HYBRID_RRF_K = original


@pytest.mark.asyncio
async def test_run_sweep_still_restores_rrf_k(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """扫描路径结束时仍恢复进入函数前的融合常数。"""
    runner = _load_baseline_runner()
    _stub_eval_io(monkeypatch, runner)
    original = settings.RAG_HYBRID_RRF_K
    settings.RAG_HYBRID_RRF_K = 55
    entered = settings.RAG_HYBRID_RRF_K
    args = argparse.Namespace(
        mode="smoke",
        db=str(REPO_ROOT / "data" / "eval_rrf_k_restore_sweep.db"),
        retrieval_depth=10,
        out=None,
        rrf_k=None,
        rrf_sweep="60,40",
    )
    try:
        code = await runner.run_sweep(args, SimpleNamespace())
        assert code == 0
        assert settings.RAG_HYBRID_RRF_K == entered
    finally:
        settings.RAG_HYBRID_RRF_K = original
