"""持久审批。一行对应一次中高风险动作，批准后最多执行一次。"""

import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin


def _uuid() -> str:
    return uuid.uuid4().hex


class ActionApproval(SQLModel, TimestampMixin, table=True):
    """发起人待决定的动作。参数原文只留在服务端，接口只返回摘要。"""

    __tablename__ = "action_approvals"

    id: str = Field(default_factory=_uuid, primary_key=True)
    tenant_id: str = Field(index=True)
    requester_id: str = Field(index=True)
    token_version: int = Field(default=0)
    resource: str = Field(default="tools")
    action: str = Field(index=True)
    subject: str = Field(default="")
    parameter_summary: str = Field(default="")
    parameter_digest: str
    arguments_json: str
    risk: str
    status: str = Field(default="pending", index=True)
    expires_at: datetime
    decided_at: datetime | None = Field(default=None)
    executed_at: datetime | None = Field(default=None)
    execution_count: int = Field(default=0)
    result_code: str = Field(default="")
