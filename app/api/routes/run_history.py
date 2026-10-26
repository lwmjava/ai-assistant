from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.api.deps import get_db, require_system_admin
from app.models.user import User
from app.schemas.run_history import RunHistoryDetail, RunHistoryOut, RunHistoryPage
from app.services.run_history import RunHistoryAccessError, RunHistoryService

router = APIRouter(prefix="/run-history", tags=["run-history"])
reader = require_system_admin


def service(session: Session = Depends(get_db)) -> RunHistoryService:
    return RunHistoryService(session)


@router.get("", response_model=RunHistoryPage)
def list_run_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    conversation_id: str | None = Query(None, max_length=128),
    user: User = Depends(reader),
    svc: RunHistoryService = Depends(service),
) -> RunHistoryPage:
    rows, total = svc.list(user, page=page, page_size=page_size, conversation_id=conversation_id)
    return RunHistoryPage(
        items=[RunHistoryOut.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{run_id}", response_model=RunHistoryDetail)
def get_run_history(
    run_id: str,
    user: User = Depends(reader),
    svc: RunHistoryService = Depends(service),
) -> RunHistoryDetail:
    try:
        return svc.get(user, run_id)
    except RunHistoryAccessError:
        raise HTTPException(404, "运行记录不存在或无权访问") from None
