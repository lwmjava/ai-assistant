"""审批的查看、批准、拒绝和一次性执行。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.agents.tools.base import ToolRegistry
from app.agents.tools.builtin import default_tools
from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.approval import ApprovalDecision, ApprovalEventOut, ApprovalOut
from app.services.approvals import (
    ApprovalError,
    ApprovalView,
    approve_approval,
    execute_approval,
    get_approval,
    list_approvals,
    reject_approval,
)

router = APIRouter(prefix="/approvals", tags=["approvals"])


def get_tool_registry() -> ToolRegistry:
    return ToolRegistry(default_tools())


def _out(view: ApprovalView) -> ApprovalOut:
    return ApprovalOut(
        id=view.id,
        resource=view.resource,
        action=view.action,
        subject=view.subject,
        parameter_summary=view.parameter_summary,
        risk=view.risk,
        status=view.status,
        expires_at=view.expires_at,
        execution_count=view.execution_count,
        result_code=view.result_code,
    )


def _http(exc: ApprovalError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


def _event_name(status: str) -> str:
    if status in {"pending", "approved"}:
        return "approval_required"
    return "approval_decided"


@router.get("/events", response_model=list[ApprovalEventOut])
def read_approval_events(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> list[ApprovalEventOut]:
    """当前用户的审批事件。断线或刷新后重新读取，得到同一批记录。"""
    return [
        ApprovalEventOut(
            id=item.id,
            event=_event_name(item.status),
            status=item.status,
            subject=item.subject,
            parameter_summary=item.parameter_summary,
            expires_at=item.expires_at,
            execution_count=item.execution_count,
            result_code=item.result_code,
        )
        for item in list_approvals(session, current_user)
    ]


@router.get("", response_model=list[ApprovalOut])
def read_approvals(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> list[ApprovalOut]:
    return [_out(item) for item in list_approvals(session, current_user)]


@router.get("/{approval_id}", response_model=ApprovalOut)
def read_approval(
    approval_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> ApprovalOut:
    try:
        return _out(get_approval(session, current_user, approval_id))
    except ApprovalError as exc:
        raise _http(exc) from exc


@router.post("/{approval_id}/approve", response_model=ApprovalOut)
def approve(
    approval_id: str,
    _body: ApprovalDecision,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> ApprovalOut:
    """批准只记录决定。真正执行要再调用执行接口。"""
    try:
        return _out(approve_approval(session, current_user, approval_id))
    except ApprovalError as exc:
        raise _http(exc) from exc


@router.post("/{approval_id}/reject", response_model=ApprovalOut)
def reject(
    approval_id: str,
    _body: ApprovalDecision,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> ApprovalOut:
    try:
        return _out(reject_approval(session, current_user, approval_id))
    except ApprovalError as exc:
        raise _http(exc) from exc


@router.post("/{approval_id}/execute", response_model=ApprovalOut)
async def execute(
    approval_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> ApprovalOut:
    try:
        view = await execute_approval(session, current_user, approval_id, registry)
    except ApprovalError as exc:
        raise _http(exc) from exc
    return _out(view)
