"""Read-only evaluation reports with role and report ownership checks."""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import require_permission
from app.models.user import User
from app.schemas.evaluation import ReportDetail, ReportFamily, ReportPage, ReportTrend
from app.services.evaluation_reports import (
    ReportNotFoundError,
    ReportProtocolError,
    ReportService,
    get_report_service,
)

router = APIRouter(prefix="/evaluations", tags=["evaluations"])
reader = require_permission("evaluations", "read")


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
