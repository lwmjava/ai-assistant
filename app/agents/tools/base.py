"""工具（Function Calling）抽象。

让 Agent 在「行动」阶段能够声明并调用外部工具 / API：
- ``Tool``：单个工具的元数据与执行入口（名称、描述、参数 JSON Schema、执行函数）；
- ``ToolRegistry``：按名称管理的工具注册表，并提供供提示词使用的文本描述；
- ``parse_tool_call``：解析模型在「行动」阶段产出的工具调用指令（统一采用
  ``<tool_call>`` 包裹的 JSON 信封，避免与普通文本混淆）。

工具执行与具体大模型实现无关，因此本模块可在任意 LLM 提供商下工作。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.agents.tools.contract import ToolContract
from app.agents.tools.outcome import ToolOutput
from app.core import log_context

if TYPE_CHECKING:
    from app.agents.tools.executor import ToolExecutor

logger = logging.getLogger(__name__)

# 工具调用的信封格式：模型在「行动」阶段若要调用工具，需输出此包裹结构，
# 且除信封外不输出多余内容；否则视为直接给出回答草稿。
_TOOL_CALL_PATTERN = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)


@dataclass
class ToolCall:
    """一次工具调用的解析结果。"""

    name: str
    arguments: dict


@dataclass
class ToolIntent:
    """模型输出里的工具调用意图。

    ``status`` 为 ``none``（没有调用）、``ok``（可执行）或 ``invalid``（有调用意图但格式错误）。
    """

    status: str
    call: ToolCall | None = None


@dataclass
class Tool:
    """可被 Agent 调用的外部工具。

    ``func`` 可以是同步或异步函数，统一在 ``execute`` 中按协程处理。
    ``parameters`` 使用 JSON Schema 描述入参，便于生成提示词与未来接入
    原生函数调用接口。
    """

    name: str
    description: str
    parameters: dict
    func: Callable[[dict], str | Awaitable[str]]
    contract: ToolContract | None = None

    async def execute(self, arguments: dict) -> str:
        """执行工具，返回供模型阅读的观测文本。"""
        result = self.func(arguments)
        if inspect.isawaitable(result):
            result = await result
        return result if isinstance(result, ToolOutput) else str(result)


def format_tool_observation(name: str, arguments: dict, observation: str) -> str:
    """把一次工具执行结果拼装成给模型的观测文本（bound/legacy 两路径唯一实现）。

    打码与路径净化规则与 PAGT-002 保持一致：web_search/file_ops/file_ops_write
    的参数整体打码；code_sandbox 的宿主路径净化。观测格式
    ``[name] 参数={shown} 结果={observation}`` 是模型 Prompt 与 golden 回归的契约，
    不得拆分出第二份实现。
    """
    shown: dict = dict(arguments or {})
    if name in {"web_search", "file_ops", "file_ops_write"}:
        shown = {"input": "[redacted]"}
    if name == "code_sandbox":
        from app.agents.tools.sandbox.runtime import sanitize_host_paths

        shown = {
            key: sanitize_host_paths(value) if isinstance(value, str) else value
            for key, value in (arguments or {}).items()
        }
    return f"[{name}] 参数={shown} 结果={observation}"


class ToolRegistry:
    """按名称管理工具集合。"""

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        self._executor: ToolExecutor | None = None
        if tools:
            for tool in tools:
                self.register(tool)

    def register(self, tool: Tool) -> None:
        """注册或覆盖一个工具。L3 契约工具在注册期即拒绝（审批闭环前不注册）。"""
        if tool.contract is not None and tool.contract.risk.value == "L3":
            raise ValueError("L3 风险工具在审批闭环存在前不得注册成工具")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def describe(self) -> str:
        """生成供大模型阅读的可用工具清单（含参数说明）。"""
        if not self._tools:
            return "（当前无可用的外部工具）"
        blocks: list[str] = []
        for tool in self._tools.values():
            params = json.dumps(tool.parameters, ensure_ascii=False)
            blocks.append(f"- {tool.name}：{tool.description}\n  参数(JSON Schema)：{params}")
        return "\n".join(blocks)

    def describe_contracts(self) -> list[dict]:
        """输出每个工具的十要素契约；contract=None 字段显式标 "missing"。"""
        out: list[dict] = []
        for tool in self._tools.values():
            c = tool.contract
            out.append({
                "name": tool.name,
                "description": tool.description,
                "version": c.version if c else "missing",
                "risk": c.risk.value if c else "missing",
                "side_effect": c.side_effect if c else "missing",
                "timeout_seconds": c.timeout_seconds if c else "missing",
                "max_retries": c.max_retries if c else "missing",
                "idempotent": c.idempotent if c else "missing",
                "conflict_policy": c.conflict_policy if c else "missing",
                "error_model": c.error_model if c else "missing",
                "audit": c.audit if c else "missing",
                "permission": {
                    "authenticated": c.permission.authenticated if c else "missing",
                    "allowed_roles": (
                        sorted(c.permission.allowed_roles) if c and c.permission.allowed_roles
                        else ("missing" if not c else None)
                    ),
                    "tenant_scoped": c.permission.tenant_scoped if c else "missing",
                    "resource": c.permission.resource if c else "missing",
                    "action": c.permission.action if c else "missing",
                },
            })
        return out

    @staticmethod
    def _resolve_role(user_id: str | None) -> str | None:
        """按 user_id 查角色。失败按未授权（fail-closed）。"""
        from app.core.database import Session, engine
        from app.models.user import User

        if not user_id:
            return None
        try:
            with Session(engine) as session:
                user = session.get(User, user_id)
                return user.role if user else None
        except Exception:  # noqa: BLE001
            logger.exception("工具调用角色解析失败：user_id=%s", user_id)
            return None

    def _denied_reason(self, call: ToolCall, tool: Tool, user_id: str | None, tenant_id: str | None) -> str | None:
        """返回拒绝原因；None 表示放行。

        contract=None（契约未声明的外部来源工具，如未治理的 MCP 工具）：放行但
        每次必审计（risk="unlisted"），describe_contracts 对缺失字段显式标
        "missing"。注意：此处无只读/写强制——未治理 MCP 写能力在匿名下也会执行。
        启用 MCP 前必须先为其补契约（运维前置，链 PMCP 族卡），本卡不承担 MCP 契约化。
        """
        arguments = call.arguments or {}
        if "tenant_id" in arguments or "user_id" in arguments:
            return "forged_identity_argument"
        contract = tool.contract
        if contract is None:
            return None
        permission = contract.permission
        if permission.authenticated and not user_id:
            return "authentication_required"
        if permission.tenant_scoped and not tenant_id:
            return "tenant_context_required"
        if permission.allowed_roles is not None:
            role = self._resolve_role(user_id)
            if role not in permission.allowed_roles:
                return "role_not_allowed"
        return None

    async def _record_audit(
        self, call: ToolCall, user_id: str | None, tenant_id: str | None,
        risk: str, allowed: bool, reason: str | None, latency_ms: float,
    ) -> None:
        """写一条 tool_call 审计；details 禁止落 arguments/密钥。失败不阻断业务。"""
        try:
            from app.audit import AuditAction, get_audit_logger

            await get_audit_logger().log(
                action=AuditAction.TOOL_CALL,
                user_id=user_id,
                tenant_id=tenant_id,
                resource_type="tool",
                resource_id=call.name,
                details={
                    "risk": risk,
                    "allowed": allowed,
                    "reason": reason,
                    "latency_ms": round(latency_ms, 2),
                },
            )
        except Exception:  # noqa: BLE001 — 审计故障不阻断业务
            logger.exception("工具调用审计写入失败：tool=%s", call.name)

    def bind_executor(self, executor: ToolExecutor) -> None:
        """绑定独立 Tool Executor；绑定后 run() 薄委托给它（PAGT-006）。"""
        self._executor = executor

    async def run(self, call: ToolCall) -> str:
        """执行一次工具调用，返回结构化观测文本。"""
        bound = getattr(self, "_executor", None)
        if bound is not None:
            result = await bound.execute(call)
            return ToolOutput(result.observation, status=result.status)
        tool = self.get(call.name)
        if tool is None:
            return ToolOutput(f"[工具调用失败] 未找到名为「{call.name}」的工具。", status="error")
        arguments = call.arguments or {}
        user_id = log_context.get_user_id() or None
        tenant_id = log_context.get_tenant_id() or None
        risk = tool.contract.risk.value if tool.contract else "unlisted"

        reason = self._denied_reason(call, tool, user_id, tenant_id)
        started = time.monotonic()
        if reason is not None:
            latency_ms = (time.monotonic() - started) * 1000
            await self._record_audit(call, user_id, tenant_id, risk, False, reason, latency_ms)
            logger.info("工具调用被拒绝：%s reason=%s", call.name, reason)
            return ToolOutput(f"[工具调用被拒绝] {call.name}：调用未授权。", status="denied")

        timeout = tool.contract.timeout_seconds if tool.contract else 30.0
        try:
            async with asyncio.timeout(timeout):
                observation = await tool.execute(arguments)
        except TimeoutError:
            observation = ToolOutput(f"[工具执行错误] {call.name}：执行超时。", status="timeout")
        except Exception as exc:  # noqa: BLE001 — 工具异常不应中断整个管线
            if call.name in {"web_search", "file_ops", "file_ops_write"}:
                logger.warning("工具执行失败：%s type=%s", call.name, type(exc).__name__)
                observation = ToolOutput(f"[工具执行错误] {call.name}：工具暂时不可用。", status="error")
            else:
                logger.exception("工具执行失败：%s", call.name)
                observation = ToolOutput(f"[工具执行错误] {call.name}：{exc}", status="error")
        latency_ms = (time.monotonic() - started) * 1000
        await self._record_audit(call, user_id, tenant_id, risk, True, None, latency_ms)

        return ToolOutput(format_tool_observation(call.name, arguments, observation),
                          status=getattr(observation, "status", "ok"))


def inspect_tool_call(text: str) -> ToolIntent:
    """区分没有调用、有效调用和格式错误。

    缺少闭合标签、多个信封、坏 JSON、名称不是非空字符串、参数不是对象，都算格式错误。
    """
    body = text or ""
    opens = body.count("<tool_call>")
    closes = body.count("</tool_call>")
    if opens == 0 and closes == 0:
        return ToolIntent("none")
    if opens != 1 or closes != 1:
        return ToolIntent("invalid")
    match = _TOOL_CALL_PATTERN.search(body)
    if not match:
        return ToolIntent("invalid")
    try:
        data = json.loads(match.group(1).strip())
    except json.JSONDecodeError:
        return ToolIntent("invalid")
    if not isinstance(data, dict):
        return ToolIntent("invalid")
    name = data.get("name", data.get("tool"))
    if not isinstance(name, str) or not name.strip():
        return ToolIntent("invalid")
    if "arguments" in data:
        args = data["arguments"]
    elif "args" in data:
        args = data["args"]
    else:
        args = {}
    if not isinstance(args, dict):
        return ToolIntent("invalid")
    return ToolIntent("ok", ToolCall(name=name.strip(), arguments=args))


def parse_tool_call(text: str) -> ToolCall | None:
    """解析有效工具调用。没有调用或格式错误时返回 None。"""
    intent = inspect_tool_call(text)
    if intent.status != "ok":
        return None
    return intent.call
