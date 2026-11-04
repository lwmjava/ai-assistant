"""HTTP contracts for managed agent definitions."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AgentScope = Literal["private", "tenant", "system"]


class AgentDefinitionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    scope: AgentScope
    name: str = Field(min_length=1, max_length=120)


class AgentDefinitionRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)


class AgentDefinitionPublish(AgentDefinitionRevision):
    instructions: str = Field(min_length=1, max_length=16000)
    tool_names: list[str] = Field(max_length=64)


class AgentDefinitionRollback(AgentDefinitionRevision):
    version: int = Field(gt=0)


class AgentDefinitionEnabled(AgentDefinitionRevision):
    enabled: bool


class AgentDefinitionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    scope: AgentScope
    enabled: bool
    revision: int
    active_version: int | None
