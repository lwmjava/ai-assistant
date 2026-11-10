"""Schemas for prompt variant compare, publish, and rollback."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class PromptSlotOut(BaseModel):
    key: str
    title: str
    source_fingerprint: str
    published_variant_id: str | None = None
    publication_revision: int | None = None


class PromptVariantCreate(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=20000)
    notes: str | None = Field(default=None, max_length=2000)


class PromptVariantOut(BaseModel):
    id: str
    slot_key: str
    label: str
    body: str
    body_fingerprint: str
    status: str
    created_by: str
    notes: str | None
    created_at: datetime
    updated_at: datetime


class PromptCompareCreate(BaseModel):
    slot_key: str = Field(min_length=1, max_length=64)
    baseline_variant_id: str = Field(min_length=1, max_length=64)
    candidate_variant_id: str = Field(min_length=1, max_length=64)
    dataset_id: str = Field(min_length=1, max_length=64)
    dataset_version_id: str = Field(min_length=1, max_length=64)
    provider: Literal["fake"] = "fake"
    max_external_calls: int = Field(default=0, ge=0, le=0)


class PromptCompareOut(BaseModel):
    id: str
    slot_key: str
    baseline_variant_id: str
    candidate_variant_id: str
    status: str
    provider: str
    binding: dict[str, Any]
    baseline_metrics: dict[str, Any]
    candidate_metrics: dict[str, Any]
    missing_metrics: list[str]
    diff: dict[str, Any]
    cost: dict[str, Any]
    call_stats: dict[str, Any]
    auto_publish_blocked: bool
    external_calls_used: int
    max_external_calls: int
    created_at: datetime


class PromptPublicationOut(BaseModel):
    slot_key: str
    active_variant_id: str
    previous_variant_id: str | None
    revision: int
    published_by: str
    published_at: datetime
    note: str | None


class PromptPublishRequest(BaseModel):
    variant_id: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(ge=0)
    note: str | None = Field(default=None, max_length=2000)
    compare_run_id: str | None = Field(default=None, max_length=64)


class PromptRollbackRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    to_variant_id: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=2000)
