"""Authorized, payload-free evaluation report responses."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EvaluationTargetType = Literal["rag", "agent", "workflow"]


class EvaluationTargetOut(BaseModel):
    id: str
    name: str
    target_type: EvaluationTargetType
    status: Literal["available", "owner_unavailable"]
    metadata: dict[str, str | int | bool | None]


class EvaluationTargetPage(BaseModel):
    items: list[EvaluationTargetOut]


class EvaluationExecutionUserOut(BaseModel):
    id: str
    username: str
    role: Literal["tenant_admin", "member"]


class EvaluationExecutionUserPage(BaseModel):
    items: list[EvaluationExecutionUserOut]


ReportFamily = Literal["retrieval", "generation", "agent", "workflow"]
ReportStatus = Literal["passed", "failed", "unmeasured", "unsupported", "unavailable"]


class ReportMetric(BaseModel):
    name: str
    value: float | None
    unit: Literal["ratio", "count", "ms", "CNY"] = "ratio"


class ReportFailure(BaseModel):
    case_id: str
    category: str | None = None
    reason: str


class ReportSummary(BaseModel):
    id: str
    family: ReportFamily
    scope: Literal["global", "tenant"]
    status: ReportStatus
    protocol: str | None = None
    recorded_at: str | None = None
    metadata: dict[str, str | int | None] = {}
    metrics: list[ReportMetric] = []


class ReportDetail(ReportSummary):
    failures: list[ReportFailure] = []
    failure_count: int = 0


class ReportPage(BaseModel):
    items: list[ReportSummary]
    total: int
    page: int
    page_size: int


class TrendPoint(BaseModel):
    report_id: str
    recorded_at: str | None
    value: float | None
    status: ReportStatus
    protocol: str | None
    metadata: dict[str, str | int | None]


class ReportTrend(BaseModel):
    family: ReportFamily
    metric: str
    points: list[TrendPoint]


DatasetVersionStatus = Literal["draft", "pending_review", "approved", "rejected"]
DatasetSource = Literal["upload", "feedback", "ai_generated"]
CandidateStatus = Literal["pending", "accepted", "rejected", "withdrawn"]
CandidateSourceType = Literal["feedback", "rating"]


class DatasetCreate(BaseModel):
    name: str
    slug: str
    family: ReportFamily
    description: str | None = None
    tenant_id: str | None = None


class DatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    name: str
    slug: str
    family: ReportFamily
    created_by: str
    description: str | None = None


class DatasetPage(BaseModel):
    items: list[DatasetOut]
    total: int
    page: int
    page_size: int


class DatasetVersionImport(BaseModel):
    version: str
    cases: list[dict]
    source: DatasetSource = "upload"
    index: dict | None = None
    is_frozen: bool = False


class DatasetVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    dataset_id: str
    tenant_id: str
    version: str
    status: DatasetVersionStatus
    source: DatasetSource
    content_hash: str
    schema_name: str
    case_count: int
    is_frozen: bool
    created_by: str
    reviewed_by: str | None = None
    review_decision: str | None = None
    review_notes: str | None = None


class DatasetVersionDetail(DatasetVersionOut):
    cases: list[dict]
    index: dict | None = None


class DatasetVersionPage(BaseModel):
    items: list[DatasetVersionOut]
    total: int
    page: int
    page_size: int


class DatasetReviewIn(BaseModel):
    decision: Literal["approve", "reject"]
    notes: str | None = None


class CandidateCreate(BaseModel):
    source_type: CandidateSourceType
    source_id: str
    target_dataset_id: str | None = None


class CandidateOut(BaseModel):
    id: str
    tenant_id: str
    source_type: CandidateSourceType
    source_id: str
    attempt_id: str | None = None
    conversation_id: str | None = None
    status: CandidateStatus
    nominated_by: str
    source_user_id: str
    reviewed_by: str | None = None
    review_notes: str | None = None
    target_dataset_id: str | None = None
    snapshot: dict


class CandidatePage(BaseModel):
    items: list[CandidateOut]
    total: int
    page: int
    page_size: int


class CandidateReviewIn(BaseModel):
    decision: Literal["accept", "reject", "withdraw"]
    notes: str | None = None


EvaluationRunner = Literal["retrieval", "generation", "agent", "workflow"]
EvaluationJobMode = Literal["smoke", "synthetic", "real"]
EvaluationJobStatus = Literal[
    "queued", "running", "cancel_requested", "succeeded", "failed", "cancelled", "interrupted"
]
EvaluationCaseResultStatus = Literal["pending", "running", "passed", "failed", "error", "skipped"]


class EvaluationJobCreate(BaseModel):
    dataset_id: str | None = Field(default=None, min_length=1, max_length=128)
    dataset_version_id: str | None = Field(default=None, min_length=1, max_length=128)
    runner: EvaluationRunner | None = None
    mode: EvaluationJobMode | None = None
    tenant_id: str | None = Field(default=None, min_length=1, max_length=128)
    target_type: EvaluationTargetType | None = None
    target_ids: list[str] = Field(default_factory=list, max_length=100)
    execution_user_id: str | None = Field(default=None, max_length=128)
    enable_generation: bool = False
    enable_judge: bool = False
    max_cost_yuan: float = Field(default=0, ge=0, le=5, allow_inf_nan=False)
    data_egress_confirmed: bool = False
    idempotency_key: str = Field(min_length=1, max_length=128)
    max_external_calls: int = Field(default=0, ge=0, le=1000)


class EvaluationJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    submitted_by: str
    dataset_id: str
    dataset_version_id: str
    job_kind: Literal["internal_smoke", "real_resource"]
    target_type: EvaluationTargetType | None = None
    adapter_name: str | None = None
    adapter_version: str | None = None
    metric_schema_version: str | None = None
    execution_user_id: str | None = None
    runner: EvaluationRunner
    mode: EvaluationJobMode
    status: EvaluationJobStatus
    input_hash: str
    max_external_calls: int
    external_calls_used: int
    case_total: int
    case_completed: int
    report_id: str | None = None
    report_hash: str | None = None
    error_code: str | None = None
    cancel_requested_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class EvaluationJobPage(BaseModel):
    items: list[EvaluationJobOut]
    total: int
    page: int
    page_size: int


class EvaluationCaseResultOut(BaseModel):
    id: str
    job_id: str
    tenant_id: str
    case_id: str
    status: EvaluationCaseResultStatus
    metrics: dict
    assertions: list[dict]
    evidence: dict
    error_code: str | None = None
    elapsed_ms: int | None = None
    external_calls_used: int


class EvaluationCaseResultPage(BaseModel):
    items: list[EvaluationCaseResultOut]
    total: int
    page: int
    page_size: int


class RetrievalVariant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: Literal["native", "langchain", "llamaindex"]
    top_k: int = Field(ge=1, le=50)


class RetrievalComparisonCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    baseline: RetrievalVariant
    candidate: RetrievalVariant


class RetrievalComparisonCandidate(BaseModel):
    rank: int
    chunk_id: str
    document_id: str
    source: str | None = None
    score: float
    similarity: float
    version_status: str
    retrieval_origin: str
    parent_id: str | None = None
    expanded_from_chunk_id: str | None = None
    score_inherited_from_chunk_id: str | None = None
    content_preview: str


class RetrievalComparisonRun(BaseModel):
    variant: RetrievalVariant
    effective_backend: str
    elapsed_ms: float
    candidates: list[RetrievalComparisonCandidate]


class RetrievalComparisonOut(BaseModel):
    identity: dict[str, str | int | None]
    baseline: RetrievalComparisonRun
    candidate: RetrievalComparisonRun


class RetrievalStrategiesOut(BaseModel):
    backends: list[str]
    top_k_min: int = 1
    top_k_max: int = 50
