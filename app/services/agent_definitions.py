"""Authorization and version resolution for managed agents."""

import json

from sqlalchemy import update
from sqlmodel import Session, col

from app.audit.models import AuditLog
from app.core.security import Role
from app.models.agent_definition import AgentDefinition, AgentDefinitionVersion
from app.models.user import User


def can_read_agent(definition: AgentDefinition, user: User) -> bool:
    if definition.scope == "system":
        return True
    if definition.tenant_id != user.tenant_id:
        return False
    return definition.scope == "tenant" or (
        definition.scope == "private" and definition.owner_id == user.id
    )


def can_manage_agent(definition: AgentDefinition, user: User) -> bool:
    if definition.scope == "system":
        return user.role_enum is Role.SYSTEM_ADMIN
    if not can_read_agent(definition, user):
        return False
    return definition.scope == "private" or user.role_enum is Role.TENANT_ADMIN


def resolve_agent_version(
    session: Session, definition_id: str, user: User
) -> AgentDefinitionVersion | None:
    definition = session.get(AgentDefinition, definition_id)
    if (
        definition is None
        or not can_read_agent(definition, user)
        or not definition.enabled
        or definition.active_version is None
    ):
        return None
    return session.get(AgentDefinitionVersion, (definition.id, definition.active_version))


def authorized_agent_tools(
    version: AgentDefinitionVersion, authorized_tools: set[str]
) -> set[str]:
    return authorized_tools.intersection(json.loads(version.tool_names_json))


def restore_agent_version(
    session: Session, user: User, definition_id: str, version: int
) -> AgentDefinitionVersion | None:
    """Restore a pinned version only while the definition remains executable."""
    if resolve_agent_version(session, definition_id, user) is None:
        return None
    return session.get(AgentDefinitionVersion, (definition_id, version))


class AgentRevisionConflictError(Exception):
    """The definition changed after the caller read it."""


def commit_agent_change(
    session: Session,
    definition: AgentDefinition,
    actor: User,
    *,
    expected_revision: int | None,
    operation: str,
    version: AgentDefinitionVersion | None = None,
) -> None:
    """Persist the definition, optional version, and audit as one change."""
    try:
        if expected_revision is None:
            session.add(definition)
            session.flush()
        else:
            with session.no_autoflush:
                result = session.execute(
                    update(AgentDefinition)
                    .where(
                        col(AgentDefinition.id) == definition.id,
                        col(AgentDefinition.revision) == expected_revision,
                    )
                    .values(
                        revision=expected_revision + 1,
                        enabled=definition.enabled,
                        active_version=definition.active_version,
                    )
                )
            if getattr(result, "rowcount", 0) != 1:
                raise AgentRevisionConflictError
            session.refresh(definition)
        if version is not None:
            session.add(version)
        session.add(
            AuditLog(
                user_id=actor.id,
                tenant_id=actor.tenant_id,
                action=f"agent_definition_{operation}",
                resource_type="agent_definition",
                resource_id=definition.id,
                details=json.dumps(
                    {
                        "revision": definition.revision,
                        "active_version": definition.active_version,
                        "enabled": definition.enabled,
                    }
                ),
            )
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
