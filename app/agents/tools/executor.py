"""Execute registered tools through shared parameter, identity and resource guards.

Structural budgets, unknown fields and resource constraints always reject unsafe
arguments. Ordinary missing/type errors retain the declared strict-validation
compatibility switch. Resource guards share the tool timeout and cancellation
boundary; rejected arguments never retry. One value-free audit records each
logical call, including its retries.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from app.agents.tools.base import ToolCall, ToolRegistry, format_tool_observation
from app.agents.tools.outcome import ToolOutput
from app.agents.tools.parameter_policy import ParameterRejectedError, check_resource, prepare_arguments
from app.core import log_context

# 瞬时错误类型：仅这类错误在幂等契约下才允许重试。
_TRANSIENT_ERRORS: tuple[type[BaseException], ...] = (TimeoutError, ConnectionError)
# 观测已收敛为友好文案、不暴露具体异常的工具（与 legacy run() 保持一致）。
_GENERIC_ERROR_TOOLS = {"web_search", "web_fetch", "file_ops", "file_ops_write"}


@dataclass
class ExecutorResult:
    """一次工具调用的结构化执行结果。"""

    observation: str  # 给模型的观测文本（与 legacy 拼装格式一致）
    status: str  # ok / denied / invalid / timeout / error
    attempts: int  # 底层实际执行次数（含重试）
    latency_ms: float
    redacted_args: dict = field(default_factory=dict)
    # 参数校验结论（供 Trace）：{"passed": bool, "errors": [...], "strict": bool}
    audit_metadata: dict = field(default_factory=dict)
    error_code: str | None = None


class ToolExecutor:
    """无状态工具执行器，组合复用 ToolRegistry 的 RBAC/审计。"""

    def __init__(
        self, registry: ToolRegistry, *, redact_errors: bool = False,
        before_attempt: Callable[[], None] | None = None,
    ) -> None:
        self._registry = registry
        self._redact_errors = redact_errors
        self._before_attempt = before_attempt

    @staticmethod
    def _validate(tool, arguments: dict) -> dict:
        """Validate bounded arguments against the declared parameter policy."""
        # Identity arguments must reach the existing permission refusal/audit,
        # rather than becoming business-schema fields at plan admission.
        business = ({key: value for key, value in arguments.items()
                     if key not in {"tenant_id", "user_id"}}
                    if isinstance(arguments, dict) else arguments)
        return dict(prepare_arguments(tool, business)[1])

    async def execute(self, call: ToolCall) -> ExecutorResult:
        """执行一次工具调用并返回结构化结果。"""
        arguments = call.arguments if call.arguments is not None else {}
        tool = self._registry.get(call.name)
        audit_call = ToolCall(call.name, {})
        user_id = log_context.get_user_id() or None
        tenant_id = log_context.get_tenant_id() or None
        risk = tool.contract.risk.value if (tool is not None and tool.contract) else "unlisted"

        # 未找到工具：与 legacy 一致，直接返回友好文案、不写审计。
        if tool is None:
            return ExecutorResult(
                observation=f"[工具调用失败] 未找到名为「{call.name}」的工具。",
                status="error",
                attempts=0,
                latency_ms=0.0,
                redacted_args={},
                audit_metadata={},
                error_code="tool_not_found",
            )

        arguments, validation = prepare_arguments(tool, arguments)
        strict = validation["strict"]

        # RBAC：组合委托既有拦截逻辑。
        reason = self._registry._denied_reason(
            ToolCall(call.name, call.arguments if isinstance(call.arguments, dict) else {}),
            tool, user_id, tenant_id)
        started = time.monotonic()
        if reason is not None:
            latency_ms = (time.monotonic() - started) * 1000
            await self._registry._record_audit(audit_call, user_id, tenant_id, risk, False, reason, latency_ms)
            return ExecutorResult(
                observation=f"[工具调用被拒绝] {call.name}：调用未授权。",
                status="denied",
                attempts=0,
                latency_ms=latency_ms,
                redacted_args={},
                audit_metadata=dict(validation),
                error_code=reason,
            )

        if validation["security_errors"] or (strict and not validation["passed"]):
            await self._registry._record_audit(
                audit_call, user_id, tenant_id, risk, False, "invalid_arguments", 0.0)
            return ExecutorResult(
                observation=f"[工具参数校验失败] {call.name}：参数不符合安全策略。",
                status="invalid", attempts=0, latency_ms=0.0,
                audit_metadata=dict(validation), error_code="invalid_arguments")

        if tool.contract is not None and tool.contract.risk.value in {"L2", "L3"}:
            try:
                await check_resource(tool, arguments)
            except ParameterRejectedError:
                await self._registry._record_audit(
                    audit_call, user_id, tenant_id, risk, False, "invalid_arguments", 0.0)
                return ExecutorResult(
                    observation=f"[工具参数校验失败] {call.name}：参数不符合安全策略。",
                    status="invalid", attempts=0, latency_ms=0.0,
                    audit_metadata=dict(validation), error_code="invalid_arguments")
            approval_id = None
            if user_id and tenant_id:
                from app.services.approvals import hold_tool_call

                approval_id = hold_tool_call(
                    user_id,
                    tenant_id,
                    resource=tool.contract.permission.resource,
                    action=call.name,
                    risk=risk,
                    arguments=arguments,
                )
            latency_ms = (time.monotonic() - started) * 1000
            await self._registry._record_audit(
                audit_call, user_id, tenant_id, risk, False, "approval_required", latency_ms)
            waiting = f"[工具等待审批] {call.name}：审批 {approval_id} 尚未执行。" if approval_id else (
                f"[工具调用被拒绝] {call.name}：需要审批后才能执行。"
            )
            return ExecutorResult(
                observation=waiting,
                status="denied",
                attempts=0,
                latency_ms=latency_ms,
                audit_metadata=dict(validation),
                error_code="approval_required",
            )

        timeout = tool.contract.timeout_seconds if tool.contract else 30.0
        max_retries = tool.contract.max_retries if tool.contract else 0
        idempotent = tool.contract.idempotent if tool.contract else True

        observation = ""
        attempts = 0
        status = "ok"
        error_code: str | None = None
        last_exc: BaseException | None = None
        try:
            while True:
                if self._before_attempt is not None:
                    try:
                        self._before_attempt()
                    except Exception:
                        await self._registry._record_audit(
                            audit_call, user_id, tenant_id, risk, False, "authority_changed",
                            (time.monotonic() - started) * 1000)
                        raise
                attempts += 1
                last_exc = None
                try:
                    async with asyncio.timeout(timeout):
                        await check_resource(tool, arguments)
                        observation = await tool.execute(arguments)
                    status = observation.status if isinstance(observation, ToolOutput) else "ok"
                    error_code = observation.error_code if isinstance(observation, ToolOutput) else None
                    break
                except ParameterRejectedError:
                    await self._registry._record_audit(
                        audit_call, user_id, tenant_id, risk, False, "invalid_arguments",
                        (time.monotonic() - started) * 1000)
                    return ExecutorResult(
                        observation=f"[工具参数校验失败] {call.name}：参数不符合安全策略。",
                        status="invalid", attempts=attempts,
                        latency_ms=(time.monotonic() - started) * 1000,
                        audit_metadata=dict(validation), error_code="invalid_arguments")
                except TimeoutError:
                    last_exc = TimeoutError()
                    observation = f"[工具执行错误] {call.name}：执行超时。"
                    status = "timeout"
                    error_code = "tool_timeout"
                except Exception as exc:  # noqa: BLE001 — 工具异常不中断管线
                    last_exc = exc
                    if self._redact_errors or call.name in _GENERIC_ERROR_TOOLS:
                        observation = f"[工具执行错误] {call.name}：工具暂时不可用。"
                    else:
                        observation = f"[工具执行错误] {call.name}：{exc}"
                    status = "error"
                    error_code = "tool_error"
                # 重试决策：仅幂等 + 瞬时错误 + 未达上限。
                transient = isinstance(last_exc, _TRANSIENT_ERRORS)
                if idempotent and transient and attempts <= max_retries:
                    await asyncio.sleep(0.05)
                    continue
                break
        except (asyncio.CancelledError, GeneratorExit):
            await self._registry._record_audit(
                audit_call, user_id, tenant_id, risk, False, "cancelled",
                (time.monotonic() - started) * 1000)
            raise

        latency_ms = (time.monotonic() - started) * 1000
        # 一次逻辑调用聚合审计；安全拒绝不记作放行。
        rejected = status == "invalid"
        await self._registry._record_audit(
            audit_call, user_id, tenant_id, risk, not rejected,
            "invalid_arguments" if rejected else None, latency_ms)

        return ExecutorResult(
            observation=format_tool_observation(call.name, arguments, observation),
            status=status,
            attempts=attempts,
            latency_ms=latency_ms,
            redacted_args={"input": "[redacted]"},
            audit_metadata=dict(validation),
            error_code=error_code,
        )
