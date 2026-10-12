"""独立 Tool Executor（PAGT-006，ADR-0010 决定1）。

把工具执行咽喉从 ``ToolRegistry.run`` 外移为独立、无状态的执行器：

- 输入 ``ToolCall`` + 执行上下文（user_id/tenant_id 从 ``log_context`` 读，沿用现状）；
- 输出结构化 ``ExecutorResult``：观测文本、status、attempts、耗时、脱敏参数快照、
  校验元数据（供 Trace 消费，**不进审计 details**）；
- 参数校验按工具 JSON Schema：v1 对既有 4 个内置工具不提前拦截（其自身已返回
  友好文案），校验结论只记入 ``audit_metadata``；硬拒绝由 ``contract.strict_validation``
  开关（默认关）。
- 超时沿用 ``contract.timeout_seconds``；重试仅当 ``contract.idempotent`` 且错误为瞬时类
  （TimeoutError/ConnectionError），次数上限 ``max_retries``（默认 0=不重试，与现状逐字节一致）；
  非幂等 / L1 以上写工具不自动重试。
- 权限拦截与审计通过**组合委托**复用 ``ToolRegistry`` 既有 ``_denied_reason`` /
  ``_record_audit``，不复制 RBAC 逻辑；一次逻辑调用（含全部重试）只写一条聚合审计，
  details 恒 risk/allowed/reason/latency_ms 四键。
- 观测拼装统一调用 ``base.format_tool_observation``，与 legacy ``Registry.run`` 同源，
  保证 bound/legacy 两条路径 golden 逐字节一致。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from app.agents.tools.base import ToolCall, ToolRegistry, format_tool_observation
from app.core import log_context

# 瞬时错误类型：仅这类错误在幂等契约下才允许重试。
_TRANSIENT_ERRORS: tuple[type[BaseException], ...] = (TimeoutError, ConnectionError)
# 观测已收敛为友好文案、不暴露具体异常的工具（与 legacy run() 保持一致）。
_GENERIC_ERROR_TOOLS = {"web_search", "file_ops", "file_ops_write"}


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


class ToolExecutor:
    """无状态工具执行器，组合复用 ToolRegistry 的 RBAC/审计。"""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    @staticmethod
    def _validate(tool, arguments: dict) -> dict:
        """按工具 parameters（JSON Schema）做必填项与顶层类型校验。

        v1 只做 required 存在性与基础类型（string/number/integer/boolean/object/array）
        检查，不做完整 JSON Schema 校验器；结论写入返回 dict，由调用方决定是否拦截。
        """
        schema = getattr(tool, "parameters", None) or {}
        required = schema.get("required") or []
        properties = schema.get("properties") or {}
        errors: list[str] = []
        for key in required:
            if key not in arguments:
                errors.append(f"缺少必填参数: {key}")
        for key, value in arguments.items():
            spec = properties.get(key) or {}
            expected = spec.get("type")
            if expected is None:
                continue
            if expected == "string" and not isinstance(value, str):
                errors.append(f"参数 {key} 应为 string，实际 {type(value).__name__}")
            elif expected == "integer" and not isinstance(value, int):
                errors.append(f"参数 {key} 应为 integer，实际 {type(value).__name__}")
            elif expected == "number" and not isinstance(value, int | float):
                errors.append(f"参数 {key} 应为 number，实际 {type(value).__name__}")
            elif expected == "boolean" and not isinstance(value, bool):
                errors.append(f"参数 {key} 应为 boolean，实际 {type(value).__name__}")
        return {"passed": not errors, "errors": errors}

    async def execute(self, call: ToolCall) -> ExecutorResult:
        """执行一次工具调用并返回结构化结果。"""
        arguments = call.arguments or {}
        tool = self._registry.get(call.name)
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
            )

        validation = self._validate(tool, arguments)
        strict = bool(tool.contract and tool.contract.strict_validation)
        validation["strict"] = strict

        # 硬校验：仅 strict 契约工具在执行前拒绝，func 零调用。
        if strict and not validation["passed"]:
            errors_text = "；".join(validation["errors"])
            latency_ms = 0.0
            await self._registry._record_audit(call, user_id, tenant_id, risk, False, "invalid_arguments", latency_ms)
            return ExecutorResult(
                observation=f"[工具参数校验失败] {call.name}：{errors_text}",
                status="invalid",
                attempts=0,
                latency_ms=latency_ms,
                redacted_args={},
                audit_metadata=validation,
            )

        # RBAC：组合委托既有拦截逻辑。
        reason = self._registry._denied_reason(call, tool, user_id, tenant_id)
        started = time.monotonic()
        if reason is not None:
            latency_ms = (time.monotonic() - started) * 1000
            await self._registry._record_audit(call, user_id, tenant_id, risk, False, reason, latency_ms)
            return ExecutorResult(
                observation=f"[工具调用被拒绝] {call.name}：调用未授权。",
                status="denied",
                attempts=0,
                latency_ms=latency_ms,
                redacted_args={},
                audit_metadata=validation,
            )

        timeout = tool.contract.timeout_seconds if tool.contract else 30.0
        max_retries = tool.contract.max_retries if tool.contract else 0
        idempotent = tool.contract.idempotent if tool.contract else True

        observation = ""
        attempts = 0
        status = "ok"
        last_exc: BaseException | None = None
        while True:
            attempts += 1
            last_exc = None
            try:
                async with asyncio.timeout(timeout):
                    observation = await tool.execute(arguments)
                status = "ok"
                break
            except TimeoutError:
                last_exc = TimeoutError()
                observation = f"[工具执行错误] {call.name}：执行超时。"
                status = "timeout"
            except Exception as exc:  # noqa: BLE001 — 工具异常不中断管线
                last_exc = exc
                if call.name in _GENERIC_ERROR_TOOLS:
                    observation = f"[工具执行错误] {call.name}：工具暂时不可用。"
                else:
                    observation = f"[工具执行错误] {call.name}：{exc}"
                status = "error"
            # 重试决策：仅幂等 + 瞬时错误 + 未达上限。
            transient = isinstance(last_exc, _TRANSIENT_ERRORS)
            if idempotent and transient and attempts <= max_retries:
                await asyncio.sleep(0.05)
                continue
            break

        latency_ms = (time.monotonic() - started) * 1000
        # 一次逻辑调用聚合为一条审计（reason 为 None 即视为已执行/放行）。
        await self._registry._record_audit(call, user_id, tenant_id, risk, True, None, latency_ms)

        return ExecutorResult(
            observation=format_tool_observation(call.name, arguments, observation),
            status=status,
            attempts=attempts,
            latency_ms=latency_ms,
            redacted_args=arguments,
            audit_metadata=validation,
        )
