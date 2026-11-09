"""审批接口契约。不包含参数原文。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ApprovalDecision(BaseModel):
    """显式决定。对话里的一句话不能填进这里充当批准。"""

    model_config = ConfigDict(extra="forbid")


class ApprovalEventOut(BaseModel):
    """对话里重放的审批事件。刷新后仍是同一条记录，不含参数原文。"""

    id: str
    event: str
    status: str
    subject: str
    parameter_summary: str
    expires_at: datetime
    execution_count: int
    result_code: str = ""


class ApprovalOut(BaseModel):
    id: str
    resource: str
    action: str
    subject: str
    parameter_summary: str
    risk: str
    status: str
    expires_at: datetime
    execution_count: int
    result_code: str = ""
