"""Durable execution references and private, sanitized user feedback."""

import uuid

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin


class ChatAttempt(SQLModel, TimestampMixin, table=True):
    __tablename__ = "chat_attempts"
    __table_args__ = (CheckConstraint("outcome IN ('pending','success','api_error','no_response','stopped')"),)

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    user_id: str = Field(index=True)
    conversation_id: str | None = Field(default=None, index=True)
    user_message_id: str | None = Field(default=None, index=True)
    assistant_message_id: str | None = Field(default=None, index=True)
    outcome: str = Field(default="pending")


class ChatFeedback(SQLModel, TimestampMixin, table=True):
    __tablename__ = "chat_feedbacks"
    __table_args__ = (
        UniqueConstraint("user_id", "attempt_id", name="uq_feedback_owner_attempt"),
        CheckConstraint("kind IN ('api_error','no_response')"),
    )

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    tenant_id: str = Field(index=True)
    user_id: str = Field(index=True)
    attempt_id: str = Field(index=True)
    conversation_id: str | None = Field(default=None)
    kind: str
    comment: str | None = Field(default=None)


class MessageRating(SQLModel, TimestampMixin, table=True):
    __tablename__ = "message_ratings"
    __table_args__ = (
        UniqueConstraint("user_id", "assistant_message_id", name="uq_rating_owner_message"),
        CheckConstraint("rating IN ('up','down')"),
    )

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    tenant_id: str = Field(index=True)
    user_id: str = Field(index=True)
    conversation_id: str
    assistant_message_id: str = Field(index=True)
    attempt_id: str
    rating: str
    reason_code: str | None = Field(default=None)
    comment: str | None = Field(default=None)
