"""Tenant-scoped evaluation datasets, immutable versions, and feedback candidates."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, Column, Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin


class EvaluationDataset(SQLModel, TimestampMixin, table=True):
    __tablename__ = "evaluation_datasets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_eval_dataset_tenant_slug"),
        CheckConstraint("family IN ('retrieval','generation','agent','workflow')"),
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


class EvaluationJob(SQLModel, TimestampMixin, table=True):
    __tablename__ = "evaluation_jobs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "submitted_by", "idempotency_key", name="uq_eval_job_idempotency"
        ),
        CheckConstraint(
            "status IN ('queued','running','cancel_requested','succeeded','failed','cancelled','interrupted')"
        ),
        CheckConstraint("runner IN ('retrieval','generation','agent','workflow')"),
        CheckConstraint("mode IN ('smoke','synthetic','real')"),
        CheckConstraint("job_kind IN ('internal_smoke','real_resource')"),
        CheckConstraint("target_type IS NULL OR target_type IN ('rag','agent','workflow')"),
        CheckConstraint("max_external_calls >= 0"),
        CheckConstraint("external_calls_used >= 0"),
        CheckConstraint("case_total >= 0"),
        CheckConstraint("case_completed >= 0"),
    )

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    tenant_id: str = Field(index=True)
    submitted_by: str = Field(index=True)
    dataset_id: str = Field(index=True)
    dataset_version_id: str = Field(index=True)
    job_kind: str = Field(default="internal_smoke", max_length=32, index=True)
    target_type: str | None = Field(default=None, max_length=32, index=True)
    target_ids_json: str = Field(default="[]", sa_column=Column(Text, nullable=False))
    target_snapshot_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    adapter_name: str | None = Field(default=None, max_length=128)
    adapter_version: str | None = Field(default=None, max_length=64)
    metric_schema_version: str | None = Field(default=None, max_length=64)
    execution_user_id: str | None = Field(default=None, index=True)
    execution_principal_json: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    options_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    budget_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    runner: str = Field(max_length=32, index=True)
    mode: str = Field(max_length=32)
    status: str = Field(default="queued", max_length=32, index=True)
    idempotency_key: str = Field(max_length=128)
    request_hash: str = Field(max_length=64)
    input_hash: str = Field(max_length=64)
    max_external_calls: int = Field(default=0)
    external_calls_used: int = Field(default=0)
    case_total: int = Field(default=0)
    case_completed: int = Field(default=0)
    report_id: str | None = Field(default=None, max_length=160)
    report_path: str | None = Field(default=None, max_length=500)
    report_hash: str | None = Field(default=None, max_length=64)
    error_code: str | None = Field(default=None, max_length=64)
    cancel_requested_at: datetime | None = Field(default=None)
    started_at: datetime | None = Field(default=None)
    finished_at: datetime | None = Field(default=None)


class EvaluationCaseResult(SQLModel, TimestampMixin, table=True):
    __tablename__ = "evaluation_case_results"
    __table_args__ = (
        UniqueConstraint("job_id", "case_id", name="uq_eval_case_result_job_case"),
        CheckConstraint("status IN ('pending','running','passed','failed','error','skipped')"),
        CheckConstraint("external_calls_used >= 0"),
    )

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    job_id: str = Field(index=True)
    tenant_id: str = Field(index=True)
    case_id: str = Field(max_length=160)
    status: str = Field(default="pending", max_length=32, index=True)
    metrics_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    assertions_json: str = Field(default="[]", sa_column=Column(Text, nullable=False))
    evidence_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    error_code: str | None = Field(default=None, max_length=64)
    elapsed_ms: int | None = Field(default=None)
    external_calls_used: int = Field(default=0)
