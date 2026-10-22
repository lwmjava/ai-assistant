"""Fixed feedback contracts exclude conversation bodies and internal failures."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FailureFeedbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attempt_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    kind: Literal["api_error", "no_response"]
    comment: str | None = Field(default=None, max_length=500)


class MessageRatingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assistant_message_id: str = Field(min_length=1, max_length=64)
    rating: Literal["up", "down"]
    reason_code: Literal["off_topic", "factual", "format", "citation", "incomplete", "other"] | None = None
    comment: str | None = Field(default=None, max_length=500)


class AttemptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    conversation_id: str | None
    user_message_id: str | None
    assistant_message_id: str | None
    outcome: str
    feedback_submitted: bool = False
    feedback_id: str | None = None


class FeedbackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tenant_id: str
    user_id: str
    attempt_id: str
    conversation_id: str | None
    kind: str
    comment: str | None
    created_at: datetime


class RatingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tenant_id: str
    user_id: str
    attempt_id: str
    conversation_id: str
    assistant_message_id: str
    rating: str
    reason_code: str | None
    comment: str | None
    created_at: datetime
    updated_at: datetime


class FeedbackPage(BaseModel):
    items: list[FeedbackOut]
    total: int
    page: int
    page_size: int


class RatingPage(BaseModel):
    items: list[RatingOut]
    total: int
    page: int
    page_size: int
