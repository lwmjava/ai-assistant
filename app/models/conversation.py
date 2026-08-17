"""会话与消息模型（多租户隔离）。

Conversation 归属某个租户下的用户；Message 归属某次会话，
按时间顺序记录用户与助手的每一轮内容，供 Agent 管线回溯上下文。
"""

import uuid

from sqlmodel import Field, Relationship, SQLModel

from app.models.base import TimestampMixin


def _uuid() -> str:
    """生成短 UUID 主键（十六进制字符串）。"""
    return uuid.uuid4().hex


class Conversation(SQLModel, TimestampMixin, table=True):
    """会话：一轮完整对话的容器，归属租户与用户。"""

    __tablename__ = "conversations"

    id: str = Field(default_factory=_uuid, primary_key=True)
    tenant_id: str = Field(index=True)
    user_id: str = Field(index=True)
    title: str | None = Field(default=None)

    messages: list["Message"] = Relationship(
        back_populates="conversation", cascade_delete=True
    )


class Message(SQLModel, TimestampMixin, table=True):
    """消息：会话中的单轮内容（用户或助手）。"""

    __tablename__ = "messages"

    id: str = Field(default_factory=_uuid, primary_key=True)
    conversation_id: str = Field(foreign_key="conversations.id", index=True)
    # user | assistant | system
    role: str = Field(index=True)
    content: str
    # 生成该消息的模型标识（助手消息记录，便于追溯）。
    model: str | None = Field(default=None)

    conversation: Conversation | None = Relationship(back_populates="messages")
