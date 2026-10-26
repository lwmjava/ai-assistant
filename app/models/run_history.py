"""Persistent, redacted execution history."""

from datetime import datetime

from sqlalchemy import CheckConstraint, Column, Text
from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin


class RunHistory(SQLModel, TimestampMixin, table=True):
    __tablename__ = "run_history"
    __table_args__ = (
        CheckConstraint("status IN ('succeeded','failed')"),
        CheckConstraint("duration_ms >= 0"),
        CheckConstraint("llm_call_count >= 0"),
        CheckConstraint("tool_call_count >= 0"),
    )

    id: str = Field(primary_key=True, max_length=64)
    tenant_id: str = Field(index=True)
    user_id: str = Field(index=True)
    conversation_id: str = Field(index=True)
    status: str = Field(index=True, max_length=16)
    error_code: str | None = Field(default=None, max_length=64)
    started_at: datetime
    finished_at: datetime
    duration_ms: float = 0.0
    llm_call_count: int = 0
    tool_call_count: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    usage_source: str | None = Field(default=None, max_length=64)
    events_json: str = Field(default="[]", sa_column=Column(Text, nullable=False))
