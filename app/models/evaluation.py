"""Tenant-scoped evaluation datasets, immutable versions, and feedback candidates."""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, Column, Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin


class EvaluationDataset(SQLModel, TimestampMixin, table=True):
    __tablename__ = "evaluation_datasets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_eval_dataset_tenant_slug"),
        CheckConstraint("family IN ('retrieval','generation','agent')"),
    )

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    tenant_id: str = Field(index=True)
    name: str = Field(max_length=200)
    slug: str = Field(max_length=120, index=True)
    family: str = Field(max_length=32)
    created_by: str = Field(index=True)
    description: str | None = Field(default=None, max_length=1000)


class EvaluationDatasetVersion(SQLModel, TimestampMixin, table=True):
    __tablename__ = "evaluation_dataset_versions"
    __table_args__ = (
        UniqueConstraint("dataset_id", "version", name="uq_eval_dataset_version"),
        CheckConstraint(
            "status IN ('draft','pending_review','approved','rejected')"
        ),
        CheckConstraint("source IN ('upload','feedback','ai_generated')"),
    )

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    dataset_id: str = Field(index=True)
    tenant_id: str = Field(index=True)
    version: str = Field(max_length=64)
    status: str = Field(default="draft", max_length=32, index=True)
    source: str = Field(default="upload", max_length=32)
    content_hash: str = Field(max_length=64)
    schema_name: str = Field(default="managed-eval-case-v1", max_length=64)
    cases_json: str = Field(sa_column=Column(Text, nullable=False))
    index_json: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    case_count: int = Field(default=0)
    is_frozen: bool = Field(default=False)
    created_by: str = Field(index=True)
    reviewed_by: str | None = Field(default=None)
    review_decision: str | None = Field(default=None, max_length=64)
    review_notes: str | None = Field(default=None, max_length=2000)


class EvaluationFeedbackCandidate(SQLModel, TimestampMixin, table=True):
    __tablename__ = "evaluation_feedback_candidates"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "source_type",
            "source_id",
            name="uq_eval_candidate_source",
        ),
        CheckConstraint("source_type IN ('feedback','rating')"),
        CheckConstraint("status IN ('pending','accepted','rejected','withdrawn')"),
    )

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    tenant_id: str = Field(index=True)
    source_type: str = Field(max_length=16)
    source_id: str = Field(index=True)
    attempt_id: str | None = Field(default=None, index=True)
    conversation_id: str | None = Field(default=None)
    snapshot_json: str = Field(sa_column=Column(Text, nullable=False))
    status: str = Field(default="pending", max_length=32, index=True)
    nominated_by: str = Field(index=True)
    source_user_id: str = Field(index=True)
    reviewed_by: str | None = Field(default=None)
    review_notes: str | None = Field(default=None, max_length=2000)
    target_dataset_id: str | None = Field(default=None)
