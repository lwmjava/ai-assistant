"""RAG-035：真实生成评测的预算/计数护栏反例测试。

护栏是硬安全：预算、调用次数、token 上限、费用停止线、未知 usage 都必须
fail-closed。这里的每条反例都对应「失败边界清单」里的一类，随实现提交并纳入回归。
"""

from __future__ import annotations

import pytest

from tests.eval.generation_budget import (
    BudgetGuard,
    BudgetLimits,
    UnknownUsageError,
)


def _limits(**overrides) -> BudgetLimits:
    base = BudgetLimits()
    for key, value in overrides.items():
        object.__setattr__(base, key, value)
    return base


def test_default_limits_match_authorization() -> None:
    """授权书硬上限：200 次 HTTP、单请求输入 8000/输出 2048、累计 1.6M/0.4096M、10 元停止线。"""
    limits = BudgetLimits()
    assert limits.max_http_attempts == 200
    assert limits.per_request_input_cap == 8000
    assert limits.per_request_output_cap == 2048
    assert limits.max_input_tokens == 1_600_000
    assert limits.max_output_tokens == 409_600
    assert limits.cost_yuan_stop == pytest.approx(10.0)


def test_reserve_then_settle_reconciles_actual_usage() -> None:
    """成功调用后按真实 usage 结算，实际比最坏预占少则释放预算。"""
    guard = BudgetGuard(_limits())
    guard.reserve(estimated_input_tokens=2000, reserved_output_tokens=2048)
    assert guard.attempts == 1
    guard.settle(prompt_tokens=1800, completion_tokens=400)
    assert guard.used_input_tokens == 1800
    assert guard.used_output_tokens == 400
    # 实际费用 = 1800*2/1e6 + 400*8/1e6 = 0.0036 + 0.0032 = 0.0068
    assert guard.spent_yuan == pytest.approx(0.0068, abs=1e-9)


def test_unknown_usage_raises_and_counts_as_failure() -> None:
    """响应缺 usage / usage 为 None：不算通过，且不补造数字（授权书硬要求）。"""
    guard = BudgetGuard(_limits())
    guard.reserve(estimated_input_tokens=2000, reserved_output_tokens=2048)
    assert guard.attempts == 1
    with pytest.raises(UnknownUsageError):
        guard.settle(prompt_tokens=None, completion_tokens=200)
    # 失败后仍按最坏预占保留费用，余额更紧——不释放。
    assert guard.spent_yuan > 0


def test_per_request_input_over_cap_is_rejected_before_send() -> None:
    """单请求输入 >8000：发送前就拒绝，不发请求。"""
    guard = BudgetGuard(_limits())
    assert guard.per_request_input_allowed(8001) is False
    assert guard.per_request_input_allowed(8000) is True


def test_balance_insufficient_stops_before_next_request() -> None:
    """费用余额不能覆盖下一次最坏预占：直接停，不再 reserve。"""
    # 一次最坏预占 ≈ 输入 2000*2/1e6 + 输出 2048*8/1e6 = 0.004 + 0.016384 = 0.020384 元。
    # 停止线 0.03 元：第一次能发，花完后第二次最坏预占 0.020384 会让累计 0.040768 > 0.03。
    guard = BudgetGuard(_limits(cost_yuan_stop=0.03))
    assert guard.can_afford_next(estimated_input_tokens=2000, reserved_output_tokens=2048) is True
    guard.reserve(estimated_input_tokens=2000, reserved_output_tokens=2048)
    guard.settle(prompt_tokens=2000, completion_tokens=2048)
    assert guard.spent_yuan == pytest.approx(0.020384, abs=1e-9)
    assert guard.can_afford_next(estimated_input_tokens=2000, reserved_output_tokens=2048) is False


def test_http_attempt_cap_stops_after_200() -> None:
    """HTTP 尝试达上限即停；每次 reserve 都计数，无隐式重试。"""
    guard = BudgetGuard(_limits(max_http_attempts=2))
    guard.reserve(estimated_input_tokens=10, reserved_output_tokens=2048)
    guard.settle(prompt_tokens=10, completion_tokens=10)
    guard.reserve(estimated_input_tokens=10, reserved_output_tokens=2048)
    guard.settle(prompt_tokens=10, completion_tokens=10)
    assert guard.can_afford_next(estimated_input_tokens=10, reserved_output_tokens=2048) is False


def test_cumulative_token_caps_stop_further_reservations() -> None:
    """累计输入达 1.6M 后即使费用没超也停止。"""
    guard = BudgetGuard(_limits(max_input_tokens=10_000))
    guard.reserve(estimated_input_tokens=9_000, reserved_output_tokens=2048)
    guard.settle(prompt_tokens=9_000, completion_tokens=10)
    assert guard.can_afford_next(estimated_input_tokens=2_000, reserved_output_tokens=2048) is False


def test_resume_restores_usage_without_double_charging() -> None:
    """断点续跑：从已落盘 usage 恢复计数，不重复计费、不重复 reserve。"""
    guard = BudgetGuard(_limits())
    guard.restore_from_history(
        attempts=3,
        used_input_tokens=5000,
        used_output_tokens=600,
        spent_yuan=0.0148,
    )
    assert guard.attempts == 3
    assert guard.used_input_tokens == 5000
    # 恢复后仍能发下一次（余额充足）。
    assert guard.can_afford_next(estimated_input_tokens=1000, reserved_output_tokens=2048) is True
