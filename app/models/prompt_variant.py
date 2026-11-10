"""Managed prompt variants, offline compares, and manual publications."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, Column, Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin


class PromptVariant(SQLModel, TimestampMixin, table=True):
    __tablename__ = "prompt_variants"
    __table_args__ = (
        UniqueConstraint("slot_key", "label", name="uq_prompt_variant_slot_label"),
        CheckConstraint("status IN ('draft','archived')"),
    )

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    slot_key: str = Field(max_length=64, index=True)
    label: str = Field(max_length=120)
    body: str = Field(sa_column=Column(Text, nullable=False))
    body_fingerprint: str = Field(max_length=64)
    status: str = Field(default="draft", max_length=32, index=True)
    created_by: str = Field(index=True)
    notes: str | None = Field(default=None, max_length=2000)


class PromptPublication(SQLModel, TimestampMixin, table=True):
    __tablename__ = "prompt_publications"
    __table_args__ = (
        UniqueConstraint("slot_key", name="uq_prompt_publication_slot"),
        CheckConstraint("revision >= 0"),
    )

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    slot_key: str = Field(max_length=64, index=True)
    active_variant_id: str = Field(index=True)
    previous_variant_id: str | None = Field(default=None)
    revision: int = Field(default=0)
    published_by: str = Field(index=True)
    published_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    note: str | None = Field(default=None, max_length=2000)


class PromptCompareRun(SQLModel, TimestampMixin, table=True):
    __tablename__ = "prompt_compare_runs"
    __table_args__ = (
        CheckConstraint("provider IN ('fake')"),
        CheckConstraint(
            "status IN ('succeeded','failed','incomplete')"
        ),
        CheckConstraint("external_calls_used >= 0"),
        CheckConstraint("max_external_calls >= 0"),
    )

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    tenant_id: str = Field(index=True)
    created_by: str = Field(index=True)
    slot_key: str = Field(max_length=64, index=True)
    baseline_variant_id: str = Field(index=True)
    candidate_variant_id: str = Field(index=True)
    dataset_id: str = Field(index=True)
    dataset_version_id: str = Field(index=True)
    dataset_version: str = Field(max_length=64)
    source_version: str = Field(max_length=64)
    model_version: str = Field(max_length=64)
    evaluation_version: str = Field(max_length=64)
    provider: str = Field(default="fake", max_length=32)
    status: str = Field(default="succeeded", max_length=32, index=True)
    max_external_calls: int = Field(default=0)
    external_calls_used: int = Field(default=0)
    binding_json: str = Field(sa_column=Column(Text, nullable=False))
    baseline_metrics_json: str = Field(sa_column=Column(Text, nullable=False))
    candidate_metrics_json: str = Field(sa_column=Column(Text, nullable=False))
    missing_metrics_json: str = Field(default="[]", sa_column=Column(Text, nullable=False))
    diff_json: str = Field(sa_column=Column(Text, nullable=False))
    cost_json: str = Field(sa_column=Column(Text, nullable=False))
    call_stats_json: str = Field(sa_column=Column(Text, nullable=False))
    auto_publish_blocked: bool = Field(default=True)
