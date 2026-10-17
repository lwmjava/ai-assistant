"""RAG-040 真实保真评价预算护栏（BudgetEnvelope）纯逻辑测试。

不发起任何真实调用；只验证「余额不足以覆盖下一次最坏请求即停止」的护栏语义。
"""

from __future__ import annotations

import pytest

from evals.section_summary.run import (
    REAL_CALL_HARD_CAP,
    BudgetEnvelope,
    BudgetExhaustedError,
    run_skeleton,
)


def test_envelope_reserves_until_exhausted() -> None:
    env = BudgetEnvelope(ceiling_cny=1.0)
    # 初始付得起
    assert env.can_afford_next() is True
    # 一直预占直到信封耗尽；每次 reserve 都应成功，直到最后一次抛 BudgetExhaustedError。
    reserved = 0
    while env.can_afford_next():
        env.reserve()
        reserved += 1
    with pytest.raises(BudgetExhaustedError):
        env.reserve()
    assert reserved >= 1
    assert env.estimated_cost_cny <= 1.0 + env.worst_cost_per_call


def test_tiny_ceiling_refuses_first_call() -> None:
    env = BudgetEnvelope(ceiling_cny=0.0)
    assert env.can_afford_next() is False
    with pytest.raises(BudgetExhaustedError):
        env.reserve()


def test_hard_call_cap_is_enforced() -> None:
    env = BudgetEnvelope(ceiling_cny=100.0)  # 钱充足，但调用次数有硬上限
    for _ in range(REAL_CALL_HARD_CAP):
        env.reserve()
    assert env.can_afford_next() is False
    with pytest.raises(BudgetExhaustedError):
        env.reserve()


async def test_synthetic_skeleton_still_runs_without_real_call() -> None:
    env = BudgetEnvelope(ceiling_cny=1.0)
    report = await run_skeleton(envelope=env)
    assert report.real_calls_made is False
    assert report.human_review_status == "pending"
    assert report.calls_used >= 1
