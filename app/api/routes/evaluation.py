"""Read-only evaluation reports plus authorized dataset and candidate management."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlmodel import Session

from app.api.deps import get_db, require_permission
from app.models.user import User
from app.schemas.evaluation import (
    CandidateCreate,
    CandidateOut,
    CandidatePage,
    CandidateReviewIn,
    DatasetCreate,
    DatasetOut,
    DatasetPage,
    DatasetReviewIn,
    DatasetVersionDetail,
    DatasetVersionImport,
    DatasetVersionOut,
    DatasetVersionPage,
    ReportDetail,
    ReportFamily,
    ReportPage,
    ReportTrend,
)
from app.services.evaluation_datasets import (
    DatasetAccessError,
    DatasetConflictError,
    DatasetValidationError,
    EvaluationDatasetService,
)
from app.services.evaluation_reports import (
    ReportNotFoundError,
    ReportProtocolError,
    ReportService,
    get_report_service,
)

router = APIRouter(prefix="/evaluations", tags=["evaluations"])
reader = require_permission("evaluations", "read")
writer = require_permission("evaluations", "write")
deleter = require_permission("evaluations", "delete")


def dataset_service(session: Session = Depends(get_db)) -> EvaluationDatasetService:
    return EvaluationDatasetService(session)


def _dataset_http(exc: Exception) -> HTTPException:
    if isinstance(exc, DatasetAccessError):
        return HTTPException(404, "资源不存在或无权访问")
    if isinstance(exc, DatasetConflictError):
        return HTTPException(409, "资源冲突")
    return HTTPException(422, "请求不合法或违反评测数据策略")


def _candidate_out(row) -> CandidateOut:
    return CandidateOut(
        id=row.id,
        tenant_id=row.tenant_id,
        source_type=row.source_type,
        source_id=row.source_id,
        attempt_id=row.attempt_id,
        conversation_id=row.conversation_id,
        status=row.status,
        nominated_by=row.nominated_by,
        source_user_id=row.source_user_id,
        reviewed_by=row.reviewed_by,
        review_notes=row.review_notes,
        target_dataset_id=row.target_dataset_id,
        snapshot=json.loads(row.snapshot_json),
    )


def _version_out(row) -> DatasetVersionOut:
    return DatasetVersionOut.model_validate(row)


@router.get("/reports", response_model=ReportPage)
def list_reports(
    family: ReportFamily | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(reader),
    service: ReportService = Depends(get_report_service),
) -> ReportPage:
    return service.list_reports(user, family, page, page_size)


@router.get("/trend", response_model=ReportTrend)
def trend(
    family: ReportFamily,
    metric: str = Query(min_length=1, max_length=64),
    user: User = Depends(reader),
    service: ReportService = Depends(get_report_service),
) -> ReportTrend:
    try:
        return service.trend(user, family, metric)
    except ReportProtocolError:
        raise HTTPException(422, "不支持的指标") from None


@router.get("/reports/{report_id}", response_model=ReportDetail)
def report_detail(
    report_id: str,
    user: User = Depends(reader),
    service: ReportService = Depends(get_report_service),
) -> ReportDetail:
    try:
        return service.detail(user, report_id)
    except ReportNotFoundError:
        raise HTTPException(404, "报告不存在或无权访问") from None
    except ReportProtocolError:
        raise HTTPException(422, "不支持或无效的报告协议") from None
    except OSError:
        raise HTTPException(503, "报告暂不可用") from None


@router.get("/datasets", response_model=DatasetPage)
def list_datasets(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(reader),
    svc: EvaluationDatasetService = Depends(dataset_service),
) -> DatasetPage:
    items, total = svc.list_datasets(user, page=page, page_size=page_size)
    return DatasetPage(
        items=[DatasetOut.model_validate(row) for row in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/datasets", response_model=DatasetOut)
def create_dataset(
    body: DatasetCreate,
    user: User = Depends(writer),
    svc: EvaluationDatasetService = Depends(dataset_service),
) -> DatasetOut:
    try:
        row = svc.create_dataset(
            user,
            name=body.name,
            slug=body.slug,
            family=body.family,
            description=body.description,
            tenant_id=body.tenant_id,
        )
    except (DatasetAccessError, DatasetConflictError, DatasetValidationError) as exc:
        raise _dataset_http(exc) from None
    return DatasetOut.model_validate(row)


@router.get("/datasets/{dataset_id}", response_model=DatasetOut)
def get_dataset(
    dataset_id: str,
    user: User = Depends(reader),
    svc: EvaluationDatasetService = Depends(dataset_service),
) -> DatasetOut:
    try:
        row = svc.get_dataset(user, dataset_id)
    except DatasetAccessError:
        raise HTTPException(404, "资源不存在或无权访问") from None
    return DatasetOut.model_validate(row)


@router.get("/datasets/{dataset_id}/versions", response_model=DatasetVersionPage)
def list_versions(
    dataset_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(reader),
    svc: EvaluationDatasetService = Depends(dataset_service),
) -> DatasetVersionPage:
    try:
        items, total = svc.list_versions(user, dataset_id, page=page, page_size=page_size)
    except DatasetAccessError:
        raise HTTPException(404, "资源不存在或无权访问") from None
    return DatasetVersionPage(
        items=[_version_out(row) for row in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/datasets/{dataset_id}/versions", response_model=DatasetVersionOut)
def import_version(
    dataset_id: str,
    body: DatasetVersionImport,
    user: User = Depends(writer),
    svc: EvaluationDatasetService = Depends(dataset_service),
) -> DatasetVersionOut:
    try:
        row = svc.import_version(
            user,
            dataset_id,
            version=body.version,
            cases=body.cases,
            source=body.source,
            index=body.index,
            is_frozen=body.is_frozen,
        )
    except (DatasetAccessError, DatasetConflictError, DatasetValidationError) as exc:
        raise _dataset_http(exc) from None
    return _version_out(row)


@router.get("/datasets/{dataset_id}/versions/{version_id}", response_model=DatasetVersionDetail)
def export_version(
    dataset_id: str,
    version_id: str,
    user: User = Depends(reader),
    svc: EvaluationDatasetService = Depends(dataset_service),
) -> DatasetVersionDetail:
    try:
        _, version, cases = svc.get_version(user, dataset_id, version_id)
    except (DatasetAccessError, DatasetValidationError) as exc:
        raise _dataset_http(exc) from None
    index = json.loads(version.index_json) if version.index_json else None
    base = _version_out(version)
    return DatasetVersionDetail(**base.model_dump(), cases=cases, index=index)


@router.post("/datasets/{dataset_id}/versions/{version_id}/submit", response_model=DatasetVersionOut)
def submit_version(
    dataset_id: str,
    version_id: str,
    user: User = Depends(writer),
    svc: EvaluationDatasetService = Depends(dataset_service),
) -> DatasetVersionOut:
    try:
        row = svc.submit_version(user, dataset_id, version_id)
    except (DatasetAccessError, DatasetValidationError) as exc:
        raise _dataset_http(exc) from None
    return _version_out(row)


@router.post("/datasets/{dataset_id}/versions/{version_id}/review", response_model=DatasetVersionOut)
def review_version(
    dataset_id: str,
    version_id: str,
    body: DatasetReviewIn,
    user: User = Depends(writer),
    svc: EvaluationDatasetService = Depends(dataset_service),
) -> DatasetVersionOut:
    try:
        row = svc.review_version(
            user, dataset_id, version_id, decision=body.decision, notes=body.notes
        )
    except (DatasetAccessError, DatasetValidationError) as exc:
        raise _dataset_http(exc) from None
    return _version_out(row)


@router.delete("/datasets/{dataset_id}/versions/{version_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_version(
    dataset_id: str,
    version_id: str,
    user: User = Depends(deleter),
    svc: EvaluationDatasetService = Depends(dataset_service),
) -> Response:
    try:
        svc.delete_version(user, dataset_id, version_id)
    except (DatasetAccessError, DatasetValidationError) as exc:
        raise _dataset_http(exc) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/candidates", response_model=CandidatePage)
def list_candidates(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = None,
    user: User = Depends(reader),
    svc: EvaluationDatasetService = Depends(dataset_service),
) -> CandidatePage:
    items, total = svc.list_candidates(user, page=page, page_size=page_size, status=status)
    return CandidatePage(
        items=[_candidate_out(row) for row in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/candidates", response_model=CandidateOut)
def create_candidate(
    body: CandidateCreate,
    user: User = Depends(writer),
    svc: EvaluationDatasetService = Depends(dataset_service),
) -> CandidateOut:
    try:
        row = svc.nominate_candidate(
            user,
            source_type=body.source_type,
            source_id=body.source_id,
            target_dataset_id=body.target_dataset_id,
        )
    except (DatasetAccessError, DatasetConflictError, DatasetValidationError) as exc:
        raise _dataset_http(exc) from None
    return _candidate_out(row)


@router.post("/candidates/{candidate_id}/review", response_model=CandidateOut)
def review_candidate(
    candidate_id: str,
    body: CandidateReviewIn,
    user: User = Depends(writer),
    svc: EvaluationDatasetService = Depends(dataset_service),
) -> CandidateOut:
    try:
        row = svc.review_candidate(
            user, candidate_id, decision=body.decision, notes=body.notes
        )
    except (DatasetAccessError, DatasetValidationError) as exc:
        raise _dataset_http(exc) from None
    return _candidate_out(row)
