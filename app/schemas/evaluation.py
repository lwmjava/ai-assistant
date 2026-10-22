"""Authorized, payload-free evaluation report responses."""

from typing import Literal

from pydantic import BaseModel

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
