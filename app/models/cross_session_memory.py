"""Traceable metadata for one cross-session memory.

The vector itself is written through the configured vector store. This row
keeps the owner, source message, expiry, and embedding identity so a hit can
be checked again before it is shown.
"""

from datetime import datetime

from sqlalchemy import Column, Text
from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin


class CrossSessionMemory(SQLModel, TimestampMixin, table=True):
    __tablename__ = "cross_session_memories"

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    user_id: str = Field(index=True)
    user_token_version: int
    source_conversation_id: str = Field(index=True)
    source_message_id: str = Field(index=True)
    content: str = Field(sa_column=Column(Text, nullable=False))
    embedding_json: str = Field(sa_column=Column(Text, nullable=False))
    embedding_provider: str = Field(max_length=64)
    embedding_model: str = Field(max_length=128)
    embedding_dim: int
    policy_version: str = Field(max_length=64)
    source_grade: str = Field(default="user_statement", max_length=32, index=True)
    content_hash: str = Field(default="", max_length=64, index=True)
    subject_key: str | None = Field(default=None, max_length=64, index=True)
    conflict_group_id: str | None = Field(default=None, max_length=64, index=True)
    supersedes_id: str | None = Field(default=None, max_length=64, index=True)
    superseded_at: datetime | None = Field(default=None, index=True)
    expires_at: datetime = Field(index=True)
    invalidated_at: datetime | None = Field(default=None, index=True)
