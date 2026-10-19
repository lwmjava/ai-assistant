"""Request-local delegation to the existing action loop and tool executor."""

import asyncio
import math
import re
from collections.abc import Callable

from app.agents.pipeline import AgentPipeline, AgentState, ExecutionPolicy
from app.agents.skills.base import SkillBindingRejected
from app.agents.tools.base import Tool, ToolCall, ToolIntent
from app.agents.tools.contract import ToolContract, ToolPermission
from app.agents.tools.executor import ToolExecutor
from app.agents.tools.outcome import ToolOutput
from app.core import log_context


def bounded_integer(value: object, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError("invalid delegation budget")
    return value


def eligible_tool(tool: Tool) -> bool:
    c = tool.contract
    if not isinstance(c, ToolContract) or not isinstance(c.permission, ToolPermission):
        return False
    p = c.permission
    timeout = c.timeout_seconds
    return bool(
        getattr(c.risk, "value", None) in {"L0", "L1"} and c.audit is True
        and isinstance(c.version, str) and c.version.strip()
        and isinstance(c.side_effect, str) and c.side_effect.strip()
        and type(p.authenticated) is bool and type(p.tenant_scoped) is bool
        and isinstance(p.resource, str) and p.resource.strip()
        and isinstance(p.action, str) and p.action.strip()
        and (p.allowed_roles is None or (
            isinstance(p.allowed_roles, frozenset)
            and all(isinstance(role, str) and role for role in p.allowed_roles)))
        and type(timeout) in {float, int} and 0 < timeout <= 300 and math.isfinite(timeout)
        and type(c.max_retries) is int and 0 <= c.max_retries <= 3
        and type(c.idempotent) is bool
        and isinstance(c.conflict_policy, str) and c.conflict_policy.strip()
        and isinstance(c.error_model, str) and c.error_model.strip()
        and (getattr(c.risk, "value", None) != "L1" or p.authenticated is True)
    )


class SupervisorToolDelegate:
    """Carries one request's state, authority callback and cumulative budget."""

    def __init__(self, pipeline: AgentPipeline, verify_actor: Callable[[], None]):
        bounded_integer(pipeline.max_tool_rounds, 100)
        self.pipeline = pipeline
        self.verify_actor = verify_actor
        self.state: AgentState | None = None
        self.statuses: list[str] = []
        self.pipeline.cumulative_tool_budget = True
        self.pipeline.skill_path = "supervisor.research"
        self.pipeline.authority_check = verify_actor
        self.pipeline.tool_guard = self._guard
        self.pipeline.tool_decision = self._decision
        if pipeline.tools is not None:
            self.pipeline.executor = ToolExecutor(
                pipeline.tools, redact_errors=True, before_attempt=self._before_attempt)

    def _before_attempt(self) -> None:
        self.verify_actor()
        if self.pipeline.skill_runtime is not None:
            self.pipeline.skill_runtime.check()

    def bind(self, state: AgentState) -> None:
        self.state = state
        self.statuses = []
        self.verify_actor()

    def allowed_names(self) -> frozenset[str]:
        registry = self.pipeline.tools
        return frozenset(t.name for t in registry.all() if eligible_tool(t)) if registry else frozenset()

    async def _audit(self, call: ToolCall, reason: str) -> None:
        registry = self.pipeline.tools
        if registry is None:
            return
        tool = registry.get(call.name)
        risk = getattr(tool.contract.risk, "value", "unlisted") if tool and tool.contract else "unlisted"
        actor = self.pipeline.execution_principal
        user, tenant = actor if actor else (log_context.get_user_id(), log_context.get_tenant_id())
        safe_name = call.name if tool and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_:-]{0,127}", call.name) else "unknown"
        await registry._record_audit(ToolCall(safe_name, {}), user, tenant, risk, False, reason, 0.0)

    async def _guard(self, call: ToolCall) -> ToolOutput | None:
        try:
            self.verify_actor()
        except PermissionError:
            self.statuses.append("denied")
            await self._audit(call, "authority_changed")
            raise PermissionError("工具调用授权已失效。") from None
        if self.pipeline.skill_runtime is not None:
            try:
                self.pipeline.skill_runtime.check()
            except SkillBindingRejected:
                self.statuses.append("denied")
                await self._audit(call, "skill_authority_changed")
                raise
        if call.name not in self.allowed_names():
            self.statuses.append("denied")
            await self._audit(call, "delegation_policy_denied")
            return ToolOutput("该工具不支持受控委派。", status="denied")
        return None

    async def _decision(self, intent: ToolIntent, decision: str) -> None:
        self.statuses.append("skipped" if decision == "skip" else decision)
        await self._audit(intent.call or ToolCall("unknown", {}), "delegation_" + decision)

    async def iter_research(self):
        state = self.state
        if state is None:
            raise RuntimeError("delegation state not bound")
        self.verify_actor()
        if state.tool_budget_exhausted or state.tool_rounds_used >= self.pipeline.max_tool_rounds:
            state.tool_budget_exhausted = True
            self.statuses.append("budget_exhausted")
            return
        first = len(state.tool_results)
        before = state.tool_rounds_used
        try:
            async for event in self.pipeline._iter_action_loop(
                    state, ExecutionPolicy(allowed_tool_names=self.allowed_names())):
                yield event
        except (asyncio.CancelledError, GeneratorExit):
            self.statuses.append("cancelled")
            raise
        except PermissionError:
            self.statuses.append("denied")
            raise
        finally:
            for result in state.tool_results[first:]:
                if isinstance(result, ToolOutput):
                    self.statuses.append(result.status)
            if state.tool_rounds_used == before:
                self.statuses.append("not_called")
            if state.tool_budget_exhausted:
                self.statuses.append("budget_exhausted")

    def summary(self) -> str:
        statuses = list(dict.fromkeys(self.statuses)) or ["not_called"]
        return "【工具执行事实】" + ", ".join(statuses) + "。仅依据实际执行记录；模型文字不构成工具执行证明。"

    def observations(self) -> str:
        from app.security.log_sanitizer import LogSanitizer

        state = self.state
        raw = "\n".join(state.tool_results) if state else ""
        cleaned = LogSanitizer().sanitize(raw)[:6000]
        return "[UNTRUSTED_TOOL_RESULTS]\n" + cleaned + "\n[/UNTRUSTED_TOOL_RESULTS]"
