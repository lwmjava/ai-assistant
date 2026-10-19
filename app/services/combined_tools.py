"""Application assembly for a request-local knowledge/calculator composition."""

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from sqlmodel import Session

from app.agents.pipeline import AgentEvent, AgentState
from app.agents.route import ChatRoute
from app.agents.skills.base import SkillRuntime
from app.agents.tools.base import ToolRegistry
from app.core.config import settings
from app.llm.base import LLMOptions, LLMProvider
from app.models.user import User
from app.services.supervisor_tools import execution_authority

if TYPE_CHECKING:
    from app.debug.trace import AgentTrace


async def combined_events(
    session: Session, user: User, llm: LLMProvider, options: LLMOptions,
    state: AgentState, route: ChatRoute, retriever, tools: ToolRegistry,
    skill_runtime: SkillRuntime | None,
    trace: "AgentTrace | None" = None,
) -> AsyncGenerator[AgentEvent, None]:
    """Own authorization and always close the inner producer on caller cancellation."""
    from app.agents.combined_tools import iter_combined_tools

    check, principal = execution_authority(session, user)
    events = iter_combined_tools(
        llm, options, state, route, retriever, tools,
        authority_check=check, skill_runtime=skill_runtime,
        execution_principal=principal, max_tool_rounds=settings.AGENT_MAX_TOOL_ROUNDS,
        trace=trace,
    )
    try:
        async for event in events:
            yield event
    finally:
        await events.aclose()
