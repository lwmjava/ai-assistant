"""Durable, single-conversation memory snapshot."""

from datetime import datetime

from sqlalchemy import Column, Text
from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin


class ConversationMemorySnapshot(SQLModel, TimestampMixin, table=True):
    __tablename__ = "conversation_memory_snapshots"

    conversation_id: str = Field(primary_key=True, foreign_key="conversations.id")
    tenant_id: str = Field(index=True)
    user_id: str = Field(index=True)
    user_token_version: int
    summary: str = Field(sa_column=Column(Text, nullable=False))
    compressed_count: int
    prefix_hash: str = Field(max_length=64)
    strategy: str = Field(max_length=32)
    policy_version: str = Field(max_length=64)
    compressor_version: str = Field(max_length=64)
    expires_at: datetime = Field(index=True)
    invalidated_at: datetime | None = Field(default=None, index=True)
    invalidation_reason: str | None = Field(default=None, max_length=64)
