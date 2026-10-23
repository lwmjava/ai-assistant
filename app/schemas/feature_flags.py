"""功能开关 API 契约。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class FeatureFlagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    value: bool
    default: bool
    description: str | None = None
    overridden: bool = False
    updated_at: datetime | None = None


class FeatureFlagUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: bool = Field(description="开关目标值")
