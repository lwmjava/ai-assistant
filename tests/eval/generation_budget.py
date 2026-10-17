"""RAG-035：真实生成评测的预算、调用次数与费用护栏（fail-closed）。

护栏只认授权书的硬上限，且在**发出请求之前**按最坏价格预占：

    最坏单次费用 ≈ 估算输入 tokens × 输入单价 + 预留输出 2048 × 输出单价

余额不能覆盖下一次最坏预占就停止。成功响应后按真实 usage 结算（释放比最坏预占
少用的部分）；**缺 usage / usage 为 None 按失败处理，不补造数字**，并保留最坏预占
费用（更保守）。每次 HTTP 尝试都计数，不使用任何 SDK 隐式重试。

本模块不发网络请求、不读密钥，纯计数，便于单测与变异验证。
"""

from __future__ import annotations

from dataclasses import dataclass, field


class UnknownUsageError(RuntimeError):
    """响应缺少可核算的 usage：不得算评测通过，也不补造 token 数。"""


@dataclass(frozen=True)
class BudgetLimits:
    """授权书硬上限（2026-10-08 用户批准）。改这里等于改授权，必须先经用户批准。"""

    max_http_attempts: int = 200
    per_request_input_cap: int = 8000
    per_request_output_cap: int = 2048
    max_input_tokens: int = 1_600_000
    max_output_tokens: int = 409_600
    cost_yuan_stop: float = 10.0
    # 高峰价预占（元 / 百万 tokens）：输入 2、输出 8。
    price_input_per_mtok: float = 2.0
    price_output_per_mtok: float = 8.0


@dataclass
class BudgetGuard:
    limits: BudgetLimits = field(default_factory=BudgetLimits)
    attempts: int = 0
    used_input_tokens: int = 0
    used_output_tokens: int = 0
    spent_yuan: float = 0.0
    # 在途（已 reserve 未 settle）请求的最坏预占费用栈，按 reserve 顺序 LIFO 结算。
    _inflight_worst: list[float] = field(default_factory=list)

    # ── 价格 ──────────────────────────────────────────────────
    def _cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * self.limits.price_input_per_mtok / 1_000_000
            + output_tokens * self.limits.price_output_per_mtok / 1_000_000
        )

    def worst_case_cost(self, estimated_input_tokens: int) -> float:
        """一次请求的最坏费用：按估算输入 + 输出硬上限预占。"""
        return self._cost(estimated_input_tokens, self.limits.per_request_output_cap)

    # ── 发送前校验 ─────────────────────────────────────────────
    def per_request_input_allowed(self, estimated_input_tokens: int) -> bool:
        """单请求输入不得超过授权上限 8000 tokens。"""
        return estimated_input_tokens <= self.limits.per_request_input_cap

    def can_afford_next(self, estimated_input_tokens: int, reserved_output_tokens: int) -> bool:
        """余额能否覆盖下一次请求的最坏预占；不能即停（不发请求）。"""
        if self.attempts >= self.limits.max_http_attempts:
            return False
        worst = self._cost(estimated_input_tokens, reserved_output_tokens)
        committed = self.spent_yuan + sum(self._inflight_worst)
        if committed + worst > self.limits.cost_yuan_stop:
            return False
        if self.used_input_tokens + estimated_input_tokens > self.limits.max_input_tokens:
            return False
        if self.used_output_tokens + reserved_output_tokens > self.limits.max_output_tokens:
            return False
        return True

    # ── 计费 ──────────────────────────────────────────────────
    def reserve(self, estimated_input_tokens: int, reserved_output_tokens: int) -> float:
        """登记一次 HTTP 尝试并预占最坏费用；返回本次最坏预占额。

        调用方必须在确认 ``can_afford_next`` 之后才 reserve；这里仍做一次硬校验，
        双重保险（fail-closed）。
        """
        if not self.can_afford_next(estimated_input_tokens, reserved_output_tokens):
            raise RuntimeError("预算/计数护栏拒绝预占：余额或上限不足，未发送请求。")
        worst = self._cost(estimated_input_tokens, reserved_output_tokens)
        self.attempts += 1
        self._inflight_worst.append(worst)
        return worst

    def settle(self, prompt_tokens: int | None, completion_tokens: int | None) -> None:
        """成功响应后按真实 usage 结算；缺 usage 抛 ``UnknownUsageError``。

        缺 usage 时**不**释放最坏预占（保留更保守的占用），并由调用方把该例记为失败。
        """
        if not isinstance(prompt_tokens, int) or not isinstance(completion_tokens, int):
            worst = self._inflight_worst.pop()
            # 缺 usage：保留最坏预占（更保守），并由调用方把该例记为失败。
            self.spent_yuan += worst
            raise UnknownUsageError(
                "响应缺少可核算的 usage（prompt/completion 为 None 或非整数），按失败处理。"
            )
        worst = self._inflight_worst.pop()
        actual = self._cost(prompt_tokens, completion_tokens)
        self.used_input_tokens += prompt_tokens
        self.used_output_tokens += completion_tokens
        self.spent_yuan += actual

    def fail_settle(self) -> None:
        """失败（超时/5xx/未知 usage）时关闭在途请求：保留最坏预占，不释放。"""
        if self._inflight_worst:
            self.spent_yuan += self._inflight_worst.pop()

    # ── 断点续跑 ───────────────────────────────────────────────
    def restore_from_history(
        self,
        *,
        attempts: int,
        used_input_tokens: int,
        used_output_tokens: int,
        spent_yuan: float,
    ) -> None:
        """从已落盘的逐例结果恢复计数与费用，续跑不重复计费。"""
        self.attempts = attempts
        self.used_input_tokens = used_input_tokens
        self.used_output_tokens = used_output_tokens
        self.spent_yuan = spent_yuan
        self._inflight_worst.clear()
