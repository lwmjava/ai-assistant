"""Application-owned authorization and assembly for Supervisor tool delegation."""

from collections.abc import Callable
from typing import TYPE_CHECKING

from sqlmodel import Session

from app.agents.pipeline import AgentPipeline
from app.agents.supervisor_tools import SupervisorToolDelegate
from app.agents.tools.executor import ToolExecutor
from app.core.config import settings
from app.core.security import Role
from app.models.user import Tenant, User

if TYPE_CHECKING:
    from app.agents.supervisor import SupervisorGraph


def execution_authority(
    session: Session, user: User,
) -> tuple[Callable[[], None], tuple[str | None, str | None]]:
    """Share application-owned fresh identity checks without exposing SQL to Agents."""
    principal = (user.id, user.tenant_id, user.role, user.token_version)
    development = not settings.AUTH_ENABLED and principal[:3] == (
        "__auth_disabled__", "__auth_disabled__", Role.SYSTEM_ADMIN.value,
    )
    bind = session.get_bind()

    def verify_actor() -> None:
        if development:
            if settings.AUTH_ENABLED:
                raise PermissionError("本次执行授权已失效，请重新发起请求。")
            return
        try:
            with Session(bind) as fresh:
                current = fresh.get(User, principal[0])
                tenant = fresh.get(Tenant, principal[1])
                valid = current is not None and current.is_active and (
                    current.id, current.tenant_id, current.role, current.token_version,
                ) == principal and tenant is not None and tenant.is_active
        except Exception:
            raise PermissionError("无法核验本次执行授权，请重新发起请求。") from None
        if not valid:
            raise PermissionError("本次执行授权已失效，请重新发起请求。")

    return verify_actor, (None, None) if development else (user.id, user.tenant_id)


def wire_supervisor_tools(session: Session, user: User, graph: "SupervisorGraph") -> None:
    """Attach the existing action loop, leaving the Agent without database access."""
    if graph.tools is None or not graph.tools.all():
        return
    verify_actor, principal = execution_authority(session, user)
    pipeline = AgentPipeline(graph.llm, options=graph.options, tools=graph.tools)
    pipeline.executor = ToolExecutor(graph.tools)
    pipeline.max_tool_rounds = settings.AGENT_MAX_TOOL_ROUNDS
    pipeline.skill_runtime = graph.skill_runtime
    pipeline.skill_path = "supervisor.research"
    pipeline.execution_principal = principal
    graph.tool_delegate = SupervisorToolDelegate(pipeline, verify_actor)
