"""工作流（Workflow）API 路由。

提供定时任务的 CRUD、执行历史查询、手动触发（WFL-03）与启停开关。
受 ``WORKFLOW_ENABLED`` 全局开关门控；按多租户隔离，权限沿用 ``ROLE_PERMISSIONS``
中的 ``workflows`` 资源（member 可读写本租户工作流）。

执行身份遵循 PRD：以任务「创建者（owner）」身份运行；owner 失效时由引擎自动停用。
"""

import math
from datetime import datetime
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlmodel import Session, col, select

from app.api.deps import audit_event, get_db, require_permission
from app.audit.models import AuditAction, WorkflowOwnerAlert
from app.core.config import settings
from app.core.security import Role
from app.models.user import User
from app.models.workflow import (
    TriggerSource,
    Workflow,
    WorkflowExecution,
)
from app.workflow.admission import WorkflowAdmissionError, forget_workflow, reserve_deletion
from app.workflow.control import cancel_execution, database_key
from app.workflow.engine import WorkflowEngine
from app.workflow.scheduler import schedule_mode

router = APIRouter(
    prefix="/workflows",
    tags=["workflows"],
    dependencies=[Depends(lambda: _require_enabled())],
)


def _require_enabled() -> None:
    """工作流引擎未启用时拒绝访问（503）。"""
    if not settings.WORKFLOW_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="工作流引擎未启用（WORKFLOW_ENABLED=false）",
        )


# ── 请求 / 响应模型 ──────────────────────────────
def _safe_timeout_input(value: object) -> object:
    """保留严格整数校验，避免校验错误响应包含不可序列化的非有限浮点。"""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


class WorkflowCreate(BaseModel):
    name: str
    cron_expr: str
    prompt_template: str
    description: str | None = None
    timezone: str | None = None
    webhook_url: str | None = None
    enabled: bool = True
    timeout_seconds: int = Field(default=300, strict=True, ge=1, le=3600)

    _normalize_timeout = field_validator("timeout_seconds", mode="before")(_safe_timeout_input)


class WorkflowUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    cron_expr: str | None = None
    prompt_template: str | None = None
    timezone: str | None = None
    webhook_url: str | None = None
    enabled: bool | None = None
    # 不验证省略的默认值；显式 null 由非可空整数类型拒绝。
    timeout_seconds: int = Field(default=cast(int, None), strict=True, ge=1, le=3600)

    _normalize_timeout = field_validator("timeout_seconds", mode="before")(_safe_timeout_input)


class WorkflowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    owner_id: str
    name: str
    description: str | None
    cron_expr: str
    prompt_template: str
    timezone: str
    enabled: bool
    timeout_seconds: int
    suspended_owner: bool
    webhook_url: str | None
    created_at: datetime
    updated_at: datetime


class ExecutionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workflow_id: str
    tenant_id: str
    triggered_by: str
    status: str
    input: str | None
    output: str | None
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    created_at: datetime
    updated_at: datetime


class ToggleIn(BaseModel):
    enabled: bool


class WorkflowAlertOut(BaseModel):
    """创建者失效告警（FLOW-005）：tenant_admin 可查询，不外发。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    workflow_id: str
    workflow_name: str
    owner_id: str
    owner_username: str | None
    reason: str
    detail: str | None
    created_at: datetime


# ── 内部辅助 ──────────────────────────────────
def _list_stmt(user: User):
    stmt = select(Workflow)
    # 系统管理员跨租户可见；其余仅本租户。
    if user.role_enum.value != Role.SYSTEM_ADMIN.value:
        stmt = stmt.where(Workflow.tenant_id == user.tenant_id)
    return stmt.order_by(col(Workflow.created_at).desc())


def _get_owned(session: Session, user: User, workflow_id: str) -> Workflow | None:
    wf = session.get(Workflow, workflow_id)
    if wf is None:
        return None
    if user.role_enum.value == Role.SYSTEM_ADMIN.value:
        return wf
    if wf.tenant_id == user.tenant_id:
        return wf
    return None


# ── 端点 ──────────────────────────────────
@router.post("", response_model=WorkflowOut, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    request: Request,
    payload: WorkflowCreate,
    session: Session = Depends(get_db),
    user: User = Depends(require_permission("workflows", "write")),
) -> Workflow:
    """创建定时任务（WFL-01）。以当前用户为 owner，归属其租户。"""
    wf = Workflow(
        tenant_id=user.tenant_id,
        owner_id=user.id,
        name=payload.name,
        description=payload.description,
        cron_expr=payload.cron_expr,
        prompt_template=payload.prompt_template,
        timezone=payload.timezone or settings.WORKFLOW_DEFAULT_TIMEZONE,
        webhook_url=payload.webhook_url,
        enabled=payload.enabled,
        timeout_seconds=payload.timeout_seconds,
    )
    session.add(wf)
    session.commit()
    session.refresh(wf)
    await audit_event(
        request,
        AuditAction.WORKFLOW_CREATE,
        user=user,
        resource_type="workflow",
        resource_id=wf.id,
        details={"name": wf.name, "cron_expr": wf.cron_expr, "enabled": wf.enabled},
    )
    return wf


@router.get("", response_model=list[WorkflowOut])
async def list_workflows(
    response: Response,
    session: Session = Depends(get_db),
    user: User = Depends(require_permission("workflows", "read")),
) -> list[Workflow]:
    """列出当前可见的定时任务（系统管理员跨租户，其余本租户）。"""
    mode = schedule_mode()
    if mode != "disabled":
        response.headers["X-Workflow-Schedule"] = mode
    return list(session.exec(_list_stmt(user)).all())


@router.get("/alerts", response_model=list[WorkflowAlertOut])
async def list_workflow_alerts(
    session: Session = Depends(get_db),
    user: User = Depends(require_permission("workflows", "read")),
) -> list[WorkflowOwnerAlert]:
    """创建者失效告警列表：本租户可见；系统管理员跨租户，其余按租户隔离。"""
    stmt = select(WorkflowOwnerAlert).order_by(col(WorkflowOwnerAlert.created_at).desc())
    if user.role_enum.value != Role.SYSTEM_ADMIN.value:
        stmt = stmt.where(WorkflowOwnerAlert.tenant_id == user.tenant_id)
    return list(session.exec(stmt).all())


@router.get("/{workflow_id}", response_model=WorkflowOut)
async def get_workflow(
    workflow_id: str,
    session: Session = Depends(get_db),
    user: User = Depends(require_permission("workflows", "read")),
) -> Workflow:
    wf = _get_owned(session, user, workflow_id)
    if wf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工作流不存在或无权访问")
    return wf


@router.put("/{workflow_id}", response_model=WorkflowOut)
async def update_workflow(
    request: Request,
    workflow_id: str,
    payload: WorkflowUpdate,
    session: Session = Depends(get_db),
    user: User = Depends(require_permission("workflows", "write")),
) -> Workflow:
    wf = _get_owned(session, user, workflow_id)
    if wf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工作流不存在或无权访问")
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(wf, field, value)
    session.add(wf)
    session.commit()
    session.refresh(wf)
    await audit_event(
        request,
        AuditAction.WORKFLOW_UPDATE,
        user=user,
        resource_type="workflow",
        resource_id=wf.id,
        # 只记录变更了哪些字段，不记录完整内容，避免日志膨胀与敏感值落库。
        details={"updated_fields": sorted(data.keys())},
    )
    return wf


@router.delete("/{workflow_id}", status_code=status.HTTP_200_OK)
async def delete_workflow(
    request: Request,
    workflow_id: str,
    session: Session = Depends(get_db),
    user: User = Depends(require_permission("workflows", "delete")),
) -> dict:
    wf = _get_owned(session, user, workflow_id)
    if wf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工作流不存在或无权访问")
    try:
        lease = reserve_deletion(session.get_bind(), wf.id, wf.tenant_id)
    except WorkflowAdmissionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.code) from None
    try:
        session.delete(wf)
        session.commit()
        forget_workflow(session.get_bind(), workflow_id, wf.tenant_id)
    finally:
        lease.release()
    await audit_event(
        request,
        AuditAction.WORKFLOW_DELETE,
        user=user,
        resource_type="workflow",
        resource_id=workflow_id,
    )
    return {"deleted": True, "id": workflow_id}


@router.get("/{workflow_id}/executions", response_model=list[ExecutionOut])
async def list_executions(
    workflow_id: str,
    session: Session = Depends(get_db),
    user: User = Depends(require_permission("workflows", "read")),
) -> list[WorkflowExecution]:
    """查看任务执行历史（WFL-02）：时间 / 状态 / 输出 / 耗时。"""
    wf = _get_owned(session, user, workflow_id)
    if wf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工作流不存在或无权访问")
    stmt = (
        select(WorkflowExecution)
        .where(WorkflowExecution.workflow_id == workflow_id)
        .order_by(col(WorkflowExecution.created_at).desc())
    )
    return list(session.exec(stmt).all())


@router.post(
    "/{workflow_id}/run", response_model=ExecutionOut, status_code=status.HTTP_201_CREATED,
    responses={409: {"description": "工作流已有活动执行或进程并发容量已满，不新增执行记录"}},
)
async def run_workflow(
    request: Request,
    workflow_id: str,
    session: Session = Depends(get_db),
    user: User = Depends(require_permission("workflows", "write")),
) -> WorkflowExecution:
    """手动触发一次执行（WFL-03）。执行身份为任务创建者（owner）。"""
    wf = _get_owned(session, user, workflow_id)
    if wf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工作流不存在或无权访问")
    owner = session.get(User, wf.owner_id)
    if owner is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="工作流创建者已失效，不能执行")
    engine = WorkflowEngine()
    try:
        execution = await engine.execute(session, wf, owner, triggered_by=TriggerSource.MANUAL.value)
    except WorkflowAdmissionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.code) from None
    await audit_event(
        request,
        AuditAction.WORKFLOW_EXECUTE,
        user=user,
        resource_type="workflow",
        resource_id=workflow_id,
        details={
            "trigger": TriggerSource.MANUAL.value,
            "status": execution.status,
            "execution_id": execution.id,
        },
    )
    return execution


@router.post("/{workflow_id}/executions/{execution_id}/cancel", response_model=ExecutionOut)
async def cancel_workflow_execution(
    request: Request,
    workflow_id: str,
    execution_id: str,
    session: Session = Depends(get_db),
    user: User = Depends(require_permission("workflows", "write")),
) -> WorkflowExecution:
    """协作取消当前进程的执行并等待收尾；终态幂等，无本进程任务返回409。"""
    wf = _get_owned(session, user, workflow_id)
    if wf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工作流不存在或无权访问")
    execution = session.get(WorkflowExecution, execution_id)
    if (execution is None or execution.workflow_id != wf.id
            or execution.tenant_id != wf.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="执行记录不存在或无权访问")
    if execution.status not in {"success", "failed"}:
        try:
            cancelled = await cancel_execution(
                execution_id, wf.id, wf.tenant_id, database_key(session.get_bind())
            )
        except RuntimeError:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail="执行终态暂无法保存，请稍后查询") from None
        if not cancelled:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="本进程无可取消的活动执行")
    # 不读取旧请求的 identity map；执行所有者负责提交终态。
    with Session(session.get_bind()) as fresh:
        final = fresh.get(WorkflowExecution, execution_id)
        if final is None or final.workflow_id != wf.id or final.tenant_id != wf.tenant_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="执行记录不存在或无权访问")
        if final.status not in {"success", "failed"}:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail="执行仍在收尾，请稍后查询")
        fresh.expunge(final)
    await audit_event(
        request, AuditAction.WORKFLOW_EXECUTE, user=user,
        resource_type="workflow", resource_id=workflow_id,
        details={"operation": "cancel", "execution_id": execution_id, "status": final.status},
    )
    return final


@router.post("/{workflow_id}/toggle", response_model=WorkflowOut)
async def toggle_workflow(
    workflow_id: str,
    payload: ToggleIn,
    session: Session = Depends(get_db),
    user: User = Depends(require_permission("workflows", "write")),
) -> Workflow:
    """启用 / 禁用定时任务（不影响手动触发）。"""
    wf = _get_owned(session, user, workflow_id)
    if wf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工作流不存在或无权访问")
    wf.enabled = payload.enabled
    session.add(wf)
    session.commit()
    session.refresh(wf)
    return wf
