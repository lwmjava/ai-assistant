"""Authorized, payload-free evaluation report responses."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

ReportFamily = Literal["retrieval", "generation", "agent"]
ReportStatus = Literal["passed", "failed", "unmeasured", "unsupported", "unavailable"]


class ReportMetric(BaseModel):
    name: str
    value: float | None
    unit: Literal["ratio", "count", "ms"] = "ratio"


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
