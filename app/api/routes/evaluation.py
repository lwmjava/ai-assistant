"""Read-only evaluation reports plus authorized dataset and candidate management."""

from __future__ import annotations

import json
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.engine import Connection
from sqlmodel import Session

from app.api.deps import audit_event, get_db, require_system_admin
from app.audit import AuditAction
from app.models.user import User
from app.rag.service import RAGService
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
    EvaluationCaseResultOut,
    EvaluationCaseResultPage,
    EvaluationCaseResultStatus,
    EvaluationExecutionUserOut,
    EvaluationExecutionUserPage,
    EvaluationJobCreate,
    EvaluationJobOut,
    EvaluationJobPage,
    EvaluationTargetOut,
    EvaluationTargetPage,
    EvaluationTargetType,
    ReportDetail,
    ReportFamily,
    ReportPage,
    ReportTrend,
    RetrievalComparisonCreate,
    RetrievalComparisonOut,
    RetrievalStrategiesOut,
)
from app.services.evaluation_comparison import (
    RetrievalComparisonConflictError,
    RetrievalComparisonService,
    RetrievalComparisonValidationError,
)
from app.services.evaluation_datasets import (
    DatasetAccessError,
    DatasetConflictError,
    DatasetValidationError,
    EvaluationDatasetService,
)
from app.services.evaluation_jobs import (
    EvaluationJobAccessError,
    EvaluationJobConflictError,
    EvaluationJobService,
    EvaluationJobValidationError,
    dispatcher,
)
from app.services.evaluation_reports import (
    ReportNotFoundError,
    ReportProtocolError,
    ReportService,
    get_report_service,
)
from app.services.evaluation_targets import (
    EvaluationTargetNotFoundError,
    EvaluationTargetService,
)

router = APIRouter(prefix="/evaluations", tags=["evaluations"])
reader = require_system_admin
writer = require_system_admin
deleter = require_system_admin


def dataset_service(session: Session = Depends(get_db)) -> EvaluationDatasetService:
    return EvaluationDatasetService(session)


def job_service(session: Session = Depends(get_db)) -> EvaluationJobService:
    return EvaluationJobService(session)


def report_service(session: Session = Depends(get_db)) -> ReportService:
    return get_report_service(session)


def target_service(session: Session = Depends(get_db)) -> EvaluationTargetService:
    return EvaluationTargetService(session)


def comparison_service(user: User = Depends(reader), session: Session = Depends(get_db)) -> RetrievalComparisonService:
    return RetrievalComparisonService(RAGService(session, user.tenant_id, reader=user))


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


def _job_http(exc: Exception) -> HTTPException:
    if isinstance(exc, EvaluationJobAccessError):
        return HTTPException(404, "任务不存在或无权访问")
    if isinstance(exc, EvaluationJobConflictError):
        return HTTPException(409, "任务状态或幂等键冲突")
    messages = {
        "quality_disabled": (
            'RAG 生成质量评测尚未启用，请管理员配置价格并启用 EVALU'
            'ATION_QUALITY_ENABLED，或改选 RAG 检索。'
        ),
        "quality_pricing_unavailable": (
            '生成模型服务或价格配置不完整，或价格与当前模'
            '型不匹配；请管理员核对质量评测模型及价格配置。'
        ),
        "quality_embedding_pricing_unavailable": (
            'Embedding 服务或价格配置不完整，或价格与当前'
            '模型不匹配；请管理员核对 Embedding 计费配置。'
        ),
        "quality_capability_unavailable": (
            '当前生成模型缺少已登记能力，或模型能力'
            '校验未启用；请管理员核对模型能力配置。'
        ),
        "quality_call_budget_insufficient": (
            '模型请求上限不足：每条用例至少需要 2 次请求（Embedding、生成），启用 Judge 时'
            '至少 3 次；自动 Silver 最多 10 条用例，可将上限设为 30 或减少已审核测试集用例。'
        ),
        "quality_case_limit": (
            '生成质量评测仅支持 1 至 10 条用'
            '例，请选择符合数量限制的已审核测试集。'
        ),
        "quality_budget_invalid": (
            '质量评测预算必须为 1 至 30 次模型请'
            '求，费用上限须大于 0 且不超过 5 元。'
        ),
        "quality_data_confirmation_required": (
            '请先确认所选文档和测试集为合成或已'
            '脱敏数据，并授权发送到当前模型服务。'
        ),
        "judge_requires_generation": "启用 Judge 前须启用 RAG 生成质量评测。",
    }
    return HTTPException(422, messages.get(str(exc), "请求不符合评测任务策略"))


def _version_out(row) -> DatasetVersionOut:
    return DatasetVersionOut.model_validate(row)


@router.get("/targets", response_model=EvaluationTargetPage)
def list_targets(
    tenant_id: str = Query(min_length=1, max_length=128),
    target_type: EvaluationTargetType = Query(),
    _: User = Depends(reader),
    service: EvaluationTargetService = Depends(target_service),
) -> EvaluationTargetPage:
    try:
        return EvaluationTargetPage(
            items=[EvaluationTargetOut.model_validate(item) for item in service.list_targets(tenant_id, target_type)]
        )
    except EvaluationTargetNotFoundError:
        raise HTTPException(404, "租户不存在或不可用") from None


@router.get("/execution-users", response_model=EvaluationExecutionUserPage)
def list_execution_users(
    tenant_id: str = Query(min_length=1, max_length=128),
    _: User = Depends(reader),
    service: EvaluationTargetService = Depends(target_service),
) -> EvaluationExecutionUserPage:
    try:
        return EvaluationExecutionUserPage(
            items=[
                EvaluationExecutionUserOut.model_validate(item)
                for item in service.execution_users(tenant_id)
            ]
        )
    except EvaluationTargetNotFoundError:
        raise HTTPException(404, "租户不存在或不可用") from None


@router.get("/retrieval/strategies", response_model=RetrievalStrategiesOut)
def retrieval_strategies(
    svc: RetrievalComparisonService = Depends(comparison_service),
) -> RetrievalStrategiesOut:
    return RetrievalStrategiesOut(backends=svc.strategies())


@router.post("/retrieval/compare", response_model=RetrievalComparisonOut)
async def compare_retrieval(
    body: RetrievalComparisonCreate,
    svc: RetrievalComparisonService = Depends(comparison_service),
) -> RetrievalComparisonOut:
    try:
        return await svc.compare(body.query, body.baseline, body.candidate)
    except RetrievalComparisonValidationError:
        raise HTTPException(422, "检索比较参数无效或后端不可用") from None
    except RetrievalComparisonConflictError:
        raise HTTPException(409, "比较期间检索后端或索引身份发生变化") from None
    except (PermissionError, RuntimeError, OSError):
        raise HTTPException(503, "检索比较暂不可用") from None


@router.get("/reports", response_model=ReportPage)
def list_reports(
    family: ReportFamily | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(reader),
    service: ReportService = Depends(report_service),
) -> ReportPage:
    return service.list_reports(user, family, page, page_size)


@router.get("/trend", response_model=ReportTrend)
def trend(
    family: ReportFamily,
    metric: str = Query(min_length=1, max_length=64),
    user: User = Depends(reader),
    service: ReportService = Depends(report_service),
) -> ReportTrend:
    try:
        return service.trend(user, family, metric)
    except ReportProtocolError:
        raise HTTPException(422, "不支持的指标") from None


@router.get("/reports/{report_id}", response_model=ReportDetail)
def report_detail(
    report_id: str,
    user: User = Depends(reader),
    service: ReportService = Depends(report_service),
) -> ReportDetail:
    try:
        return service.detail(user, report_id)
    except ReportNotFoundError:
        raise HTTPException(404, "报告不存在或无权访问") from None
    except ReportProtocolError:
        raise HTTPException(422, "不支持或无效的报告协议") from None
    except OSError:
        raise HTTPException(503, "报告暂不可用") from None


@router.get("/jobs", response_model=EvaluationJobPage)
def list_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(reader),
    svc: EvaluationJobService = Depends(job_service),
) -> EvaluationJobPage:
    rows, total = svc.list(user, page=page, page_size=page_size)
    return EvaluationJobPage(
        items=[EvaluationJobOut.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/jobs", response_model=EvaluationJobOut, status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    request: Request,
    body: EvaluationJobCreate,
    user: User = Depends(writer),
    svc: EvaluationJobService = Depends(job_service),
) -> EvaluationJobOut:
    try:
        if body.target_type is not None:
            if body.tenant_id is None:
                raise EvaluationJobValidationError("target_tenant_required")
            row, created = svc.create_real(
                user,
                tenant_id=body.tenant_id,
                dataset_id=body.dataset_id,
                dataset_version_id=body.dataset_version_id,
                target_type=body.target_type,
                target_ids=body.target_ids,
                execution_user_id=body.execution_user_id,
                enable_generation=body.enable_generation,
                enable_judge=body.enable_judge,
                idempotency_key=body.idempotency_key,
                max_external_calls=body.max_external_calls,
                max_cost_yuan=body.max_cost_yuan,
                data_egress_confirmed=body.data_egress_confirmed,
            )
        else:
            if (
                body.runner is None
                or body.mode is None
                or body.dataset_id is None
                or body.dataset_version_id is None
            ):
                raise EvaluationJobValidationError("runner_and_mode_required")
            row, created = svc.create(
                user,
                dataset_id=body.dataset_id,
                dataset_version_id=body.dataset_version_id,
                runner=body.runner,
                mode=body.mode,
                idempotency_key=body.idempotency_key,
                max_external_calls=body.max_external_calls,
            )
    except (EvaluationJobAccessError, EvaluationJobConflictError, EvaluationJobValidationError) as exc:
        raise _job_http(exc) from None
    if created:
        bind = svc.session.get_bind()
        dispatcher.submit(bind.engine if isinstance(bind, Connection) else bind, row.id)
        await audit_event(
            request,
            AuditAction.EVALUATION_JOB_CREATE,
            user=user,
            resource_type="evaluation_job",
            resource_id=row.id,
            details={
                "target_tenant_id": row.tenant_id,
                "target_type": row.target_type,
                "target_ids": body.target_ids,
                "initiator_user_id": user.id,
                "execution_user_id": row.execution_user_id,
                "adapter_name": row.adapter_name,
                "adapter_version": row.adapter_version,
                "max_external_calls": row.max_external_calls,
            },
        )
    return EvaluationJobOut.model_validate(row)


@router.get("/jobs/{job_id}", response_model=EvaluationJobOut)
def get_job(
    job_id: str,
    user: User = Depends(reader),
    svc: EvaluationJobService = Depends(job_service),
) -> EvaluationJobOut:
    try:
        return EvaluationJobOut.model_validate(svc.get(user, job_id))
    except EvaluationJobAccessError as exc:
        raise _job_http(exc) from None


@router.get("/jobs/{job_id}/cases", response_model=EvaluationCaseResultPage)
def list_job_cases(
    job_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    user: User = Depends(reader),
    svc: EvaluationJobService = Depends(job_service),
) -> EvaluationCaseResultPage:
    try:
        rows, total = svc.list_case_results(user, job_id, page=page, page_size=page_size)
    except EvaluationJobAccessError as exc:
        raise _job_http(exc) from None
    return EvaluationCaseResultPage(
        items=[
            EvaluationCaseResultOut(
                id=row.id,
                job_id=row.job_id,
                tenant_id=row.tenant_id,
                case_id=row.case_id,
                status=cast(EvaluationCaseResultStatus, row.status),
                metrics=json.loads(row.metrics_json),
                assertions=json.loads(row.assertions_json),
                evidence=json.loads(row.evidence_json),
                error_code=row.error_code,
                elapsed_ms=row.elapsed_ms,
                external_calls_used=row.external_calls_used,
            )
            for row in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/jobs/{job_id}/cancel", response_model=EvaluationJobOut)
async def cancel_job(
    request: Request,
    job_id: str,
    user: User = Depends(writer),
    svc: EvaluationJobService = Depends(job_service),
) -> EvaluationJobOut:
    try:
        row = svc.cancel(user, job_id)
    except (EvaluationJobAccessError, EvaluationJobConflictError) as exc:
        raise _job_http(exc) from None
    dispatcher.request_cancel(job_id)
    await audit_event(
        request,
        AuditAction.EVALUATION_JOB_CANCEL,
        user=user,
        resource_type="evaluation_job",
        resource_id=row.id,
        details={
            "target_tenant_id": row.tenant_id,
            "target_type": row.target_type,
            "initiator_user_id": user.id,
            "execution_user_id": row.execution_user_id,
        },
    )
    return EvaluationJobOut.model_validate(row)


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
        row = svc.review_version(user, dataset_id, version_id, decision=body.decision, notes=body.notes)
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
        row = svc.review_candidate(user, candidate_id, decision=body.decision, notes=body.notes)
    except (DatasetAccessError, DatasetValidationError) as exc:
        raise _dataset_http(exc) from None
    return _candidate_out(row)
