"""工作流（Workflow）API 路由。

提供定时任务的 CRUD、执行历史查询、手动触发（WFL-03）与启停开关。
受 ``WORKFLOW_ENABLED`` 全局开关门控；按多租户隔离，权限沿用 ``ROLE_PERMISSIONS``
中的 ``workflows`` 资源（member 可读写本租户工作流）。

执行身份遵循 PRD：以任务「创建者（owner）」身份运行；owner 失效时由引擎自动停用。
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session, select

from app.api.deps import get_current_user, get_db, require_permission
from app.core.config import settings
from app.core.security import Role
from app.models.user import User
from app.models.workflow import (
    ExecutionStatus,
    TriggerSource,
    Workflow,
    WorkflowExecution,
)
from app.workflow.engine import WorkflowEngine

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
class WorkflowCreate(BaseModel):
    name: str
    cron_expr: str
    prompt_template: str
    description: str | None = None
    timezone: str | None = None
    webhook_url: str | None = None
    enabled: bool = True


class WorkflowUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    cron_expr: str | None = None
    prompt_template: str | None = None
    timezone: str | None = None
    webhook_url: str | None = None
    enabled: bool | None = None


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


# ── 内部辅助 ──────────────────────────────────
def _list_stmt(user: User):
    stmt = select(Workflow)
    # 系统管理员跨租户可见；其余仅本租户。
    if user.role_enum.value != Role.SYSTEM_ADMIN.value:
        stmt = stmt.where(Workflow.tenant_id == user.tenant_id)
    return stmt.order_by(Workflow.created_at.desc())


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
    )
    session.add(wf)
    session.commit()
    session.refresh(wf)
    return wf


@router.get("", response_model=list[WorkflowOut])
async def list_workflows(
    session: Session = Depends(get_db),
    user: User = Depends(require_permission("workflows", "read")),
) -> list[Workflow]:
    """列出当前可见的定时任务（系统管理员跨租户，其余本租户）。"""
    return list(session.exec(_list_stmt(user)).all())


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
    return wf


@router.delete("/{workflow_id}", status_code=status.HTTP_200_OK)
async def delete_workflow(
    workflow_id: str,
    session: Session = Depends(get_db),
    user: User = Depends(require_permission("workflows", "delete")),
) -> dict:
    wf = _get_owned(session, user, workflow_id)
    if wf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工作流不存在或无权访问")
    session.delete(wf)
    session.commit()
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
        .order_by(WorkflowExecution.created_at.desc())
    )
    return list(session.exec(stmt).all())


@router.post("/{workflow_id}/run", response_model=ExecutionOut, status_code=status.HTTP_201_CREATED)
async def run_workflow(
    workflow_id: str,
    session: Session = Depends(get_db),
    user: User = Depends(require_permission("workflows", "write")),
) -> WorkflowExecution:
    """手动触发一次执行（WFL-03）。执行身份为任务创建者（owner）。"""
    wf = _get_owned(session, user, workflow_id)
    if wf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工作流不存在或无权访问")
    # owner 可能已从库移除（异常场景）→ 降级为当前触发者，引擎会做失效校验。
    owner = session.get(User, wf.owner_id) or user
    engine = WorkflowEngine()
    execution = await engine.execute(
        session, wf, owner, triggered_by=TriggerSource.MANUAL.value
    )
    return execution


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
