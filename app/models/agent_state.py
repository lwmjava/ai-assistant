"""Agent 运行时状态 checkpoint 表（PAGT-006，ADR-0010 决定3）。

每「一次用户消息的执行」一行 checkpoint：JSON 快照列 + 绑定列 + TTL。
不复用 messages 表（messages 语义是按轮次最终问答内容）；本表只存单次执行的中间态，
纯追加-覆盖，不存业务原文。
"""

import uuid
from datetime import datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin


def _uuid() -> str:
    """生成短 UUID 主键（十六进制字符串）。"""
    return uuid.uuid4().hex


class AgentRunState(SQLModel, TimestampMixin, table=True):
    """一次用户消息执行的中断-续跑 checkpoint。"""

    __tablename__ = "agent_run_states"
    # 同一 (conversation_id, message_id) 唯一一行 checkpoint（upsert）。
    __table_args__ = (
        UniqueConstraint("conversation_id", "message_id", name="ix_agent_run_states_conv_msg"),
    )

    id: str = Field(default_factory=_uuid, primary_key=True)
    tenant_id: str = Field(index=True)
    user_id: str = Field(index=True)
    conversation_id: str = Field(foreign_key="conversations.id", index=True)
    # 本次执行对应的用户消息；唯一约束 (conversation_id, message_id)。
    message_id: str | None = Field(default=None, index=True)
    # understanding/plan/retrieval/action/reflect/respond/done/error
    stage: str = Field(default="action")
    # 行动循环已用工具轮次。
    round_no: int = Field(default=0)
    tool_budget_exhausted: bool = Field(default=False)
    # running/suspended/done/error
    status: str = Field(default="running")
    # AgentState 可序列化子集（不含密钥；工具结果已在执行层脱敏）。
    state_json: str = Field(default="")
    error: str | None = Field(default=None)
    expires_at: datetime | None = Field(default=None, index=True)
