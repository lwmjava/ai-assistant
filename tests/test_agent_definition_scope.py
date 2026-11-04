"""Managed Agent scope and tool boundaries."""

import pytest

from app.models.agent_definition import AgentDefinition, AgentDefinitionVersion
from app.models.user import User
from app.services.agent_definitions import (
    authorized_agent_tools,
    can_manage_agent,
    can_read_agent,
)


@pytest.mark.parametrize(
    ("scope", "tenant", "actor", "role", "read", "manage"),
    [
        ("private", "one", "owner", "member", True, True),
        ("private", "one", "other", "tenant_admin", False, False),
        ("tenant", "one", "other", "member", True, False),
        ("tenant", "one", "other", "tenant_admin", True, True),
        ("tenant", "two", "other", "tenant_admin", False, False),
        ("system", "two", "other", "tenant_admin", True, False),
        ("system", "two", "other", "system_admin", True, True),
    ],
)
def test_scope_permissions(scope, tenant, actor, role, read, manage):
    definition = AgentDefinition(scope=scope, tenant_id="one", owner_id="owner", name="Agent")
    user = User(id=actor, tenant_id=tenant, role=role)

    assert can_read_agent(definition, user) is read
    assert can_manage_agent(definition, user) is manage


def test_tools_require_both_authorizations():
    version = AgentDefinitionVersion(
        definition_id="agent",
        version=1,
        instructions="Use authorized tools.",
        tool_names_json='["both", "definition_only"]',
    )

    assert authorized_agent_tools(version, {"both", "actor_only"}) == {"both"}
