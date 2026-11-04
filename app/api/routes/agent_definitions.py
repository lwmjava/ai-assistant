"""Manage scoped agent definitions."""

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, col, select

from app.agents.tools.builtin import default_tools
from app.api.deps import get_current_user, get_db
from app.models.agent_definition import AgentDefinition, AgentDefinitionVersion
from app.models.user import User
from app.schemas.agent_definition import (
    AgentDefinitionCreate,
    AgentDefinitionEnabled,
    AgentDefinitionOut,
    AgentDefinitionPublish,
    AgentDefinitionRollback,
)
from app.services.agent_definitions import (
    AgentRevisionConflictError,
    can_manage_agent,
    can_read_agent,
    commit_agent_change,
)

router = APIRouter(prefix="/agent-definitions", tags=["agent-definitions"])


def _managed(session: Session, definition_id: str, user: User) -> AgentDefinition:
    definition = session.get(AgentDefinition, definition_id)
    if definition is None or not can_manage_agent(definition, user):
        raise HTTPException(404, "Agent definition unavailable")
    return definition


def _save(
    session: Session,
    definition: AgentDefinition,
    user: User,
    operation: str,
    expected_revision: int | None,
    version: AgentDefinitionVersion | None = None,
) -> AgentDefinitionOut:
    try:
        commit_agent_change(
            session, definition, user, expected_revision=expected_revision,
            operation=operation, version=version,
        )
    except AgentRevisionConflictError as exc:
        raise HTTPException(409, "Agent definition revision changed") from exc
    return AgentDefinitionOut.model_validate(definition)


@router.get("")
def list_definitions(
    session: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> list[AgentDefinitionOut]:
    definitions = session.exec(
        select(AgentDefinition).where(
            (col(AgentDefinition.tenant_id) == user.tenant_id)
            | (col(AgentDefinition.scope) == "system")
        )
    )
    return [
        AgentDefinitionOut.model_validate(definition)
        for definition in definitions if can_read_agent(definition, user)
    ]


@router.post("", status_code=201)
def create_definition(
    data: AgentDefinitionCreate,
    session: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> AgentDefinitionOut:
    definition = AgentDefinition(
        tenant_id=None if data.scope == "system" else user.tenant_id,
        owner_id=user.id, scope=data.scope, name=data.name,
    )
    if not can_manage_agent(definition, user):
        raise HTTPException(403, "Agent scope forbidden")
    return _save(session, definition, user, "create", None)


@router.post("/{definition_id}/publish")
def publish_definition(
    definition_id: str, data: AgentDefinitionPublish,
    session: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> AgentDefinitionOut:
    definition = _managed(session, definition_id, user)
    if set(data.tool_names) - {tool.name for tool in default_tools()}:
        raise HTTPException(422, "Unknown agent tool")
    version = AgentDefinitionVersion(
        definition_id=definition.id, version=data.expected_revision + 1,
        instructions=data.instructions, tool_names_json=json.dumps(sorted(set(data.tool_names))),
    )
    definition.active_version = version.version
    return _save(session, definition, user, "publish", data.expected_revision, version)


@router.post("/{definition_id}/rollback")
def rollback_definition(
    definition_id: str, data: AgentDefinitionRollback,
    session: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> AgentDefinitionOut:
    definition = _managed(session, definition_id, user)
    if session.get(AgentDefinitionVersion, (definition.id, data.version)) is None:
        raise HTTPException(404, "Agent version unavailable")
    definition.active_version = data.version
    return _save(session, definition, user, "rollback", data.expected_revision)


@router.put("/{definition_id}/enabled")
def set_definition_enabled(
    definition_id: str, data: AgentDefinitionEnabled,
    session: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> AgentDefinitionOut:
    definition = _managed(session, definition_id, user)
    if data.enabled and definition.active_version is None:
        raise HTTPException(409, "Publish an agent version before enabling")
    definition.enabled = data.enabled
    return _save(session, definition, user, "enable" if data.enabled else "disable", data.expected_revision)
