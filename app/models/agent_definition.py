"""Managed agent definitions and immutable published versions."""

from uuid import uuid4

from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin


class AgentDefinition(SQLModel, TimestampMixin, table=True):
    __tablename__ = "agent_definitions"

    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    tenant_id: str | None = Field(default=None, foreign_key="tenants.id", index=True)
    owner_id: str = Field(foreign_key="users.id", index=True)
    scope: str
    name: str
    enabled: bool = False
    active_version: int | None = None
    revision: int = 0


class AgentDefinitionVersion(SQLModel, TimestampMixin, table=True):
    __tablename__ = "agent_definition_versions"

    definition_id: str = Field(foreign_key="agent_definitions.id", primary_key=True)
    version: int = Field(primary_key=True)
    instructions: str
    tool_names_json: str
