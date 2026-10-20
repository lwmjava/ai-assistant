"""WorkflowScheduler：基于 asyncio 的轻量 cron 调度循环。

在应用 lifespan 启动（``start_scheduler``）与关闭（``stop_scheduler``）。循环周期性
扫描启用的工作流，按各自时区计算最近到期时刻，到点则触发执行（cron 来源）。
单任务 / 单 tick 异常不影响其他任务与循环本身。

croniter 未安装时调度器不启动（仅手动触发可用）；``WORKFLOW_ENABLED`` 关闭时同样不启动。
"""

import asyncio
import logging
from contextvars import ContextVar
from datetime import UTC, datetime

from sqlmodel import Session, col, select

from app.audit.models import WorkflowOwnerAlert
from app.core.config import settings
from app.core.database import engine
from app.models.user import Tenant, User
from app.models.workflow import TriggerSource, Workflow, WorkflowExecution
from app.workflow.engine import WorkflowEngine, _require_croniter, latest_due_time

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None
# 进程级已消费时刻；数据库创建时间仅提供保守恢复，不保证跨重启恰好一次。
_last_run: dict[str, datetime] = {}
_scan_due: ContextVar[datetime | None] = ContextVar("workflow_scan_due", default=None)


def schedule_mode() -> str:
    """返回当前进程的调度可用性：disabled、scheduled 或 manual-only。"""
    if not settings.WORKFLOW_ENABLED:
        return "disabled"
    try:
        _require_croniter()
    except Exception:  # noqa: BLE001
        return "manual-only"
    return "scheduled"


def _is_scheduler_runnable() -> bool:
    mode = schedule_mode()
    if mode == "manual-only":
        logger.warning("croniter 未安装，调度器不启动（仅手动触发可用）")
    return mode == "scheduled"


async def _loop() -> None:
    logger.info("Workflow 调度器已启动，扫描间隔 %.0fs", settings.WORKFLOW_SCHEDULER_INTERVAL)
    while True:
        try:
            await _tick()
        except Exception:  # noqa: BLE001
            logger.exception("Workflow 调度器 tick 异常")
        try:
            await asyncio.sleep(settings.WORKFLOW_SCHEDULER_INTERVAL)
        except asyncio.CancelledError:
            break


async def _tick() -> None:
    """扫描一次：对到点的启用工作流触发执行。"""
    now = datetime.now(UTC)
    with Session(engine) as session:
        workflows = session.exec(
            select(Workflow).where(col(Workflow.enabled).is_(True)).where(col(Workflow.suspended_owner).is_(False))
        ).all()
    for wf in workflows:
        try:
            due = latest_due_time(wf.cron_expr, wf.timezone, now)
            if due < _as_utc(wf.created_at):
                continue
            consumed = [
                _as_utc(value)
                for value in (
                    _last_run.get(wf.id),
                    _most_recent_execution_time(wf.id, wf.tenant_id),
                )
                if value is not None
            ]
            if not consumed or due > max(consumed):
                token = _scan_due.set(due)
                try:
                    await _trigger(wf)
                    _last_run[wf.id] = due
                finally:
                    _scan_due.reset(token)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Workflow %s 调度失败（%s）", wf.id, type(exc).__name__)


def _as_utc(value: datetime) -> datetime:
    """SQLite 的 naive 时间沿用 UTC 存储约定。"""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _most_recent_execution_time(workflow_id: str, tenant_id: str) -> datetime | None:
    """读取同租户 cron 全部状态的最近尝试创建时间，manual 不参与水位。"""

    with Session(engine) as session:
        row = session.exec(
            select(WorkflowExecution.created_at)
            .where(col(WorkflowExecution.workflow_id) == workflow_id)
            .where(col(WorkflowExecution.tenant_id) == tenant_id)
            .where(col(WorkflowExecution.triggered_by) == TriggerSource.CRON.value)
            .order_by(col(WorkflowExecution.created_at).desc())
            .limit(1)
        ).first()
    return row


async def _trigger(wf: Workflow) -> None:
    """加载 owner 并以 cron 来源触发一次执行；owner 失效则停用工作流。"""
    with Session(engine) as session:
        current = session.get(Workflow, wf.id)
        if current is None or not current.enabled or current.suspended_owner:
            return
        if (
            current.cron_expr != wf.cron_expr
            or current.timezone != wf.timezone
            or _as_utc(current.created_at) != _as_utc(wf.created_at)
        ):
            return
        wf = current
        owner = session.get(User, wf.owner_id)
        if owner is None:
            if not wf.suspended_owner:
                session.add(WorkflowOwnerAlert(
                    tenant_id=wf.tenant_id,
                    workflow_id=wf.id,
                    workflow_name=wf.name,
                    owner_id=wf.owner_id,
                    owner_username=None,
                    reason="owner_deleted",
                    detail="创建者账号不存在，工作流已自动停用",
                ))
            wf.enabled = False
            wf.suspended_owner = True
            session.add(wf)
            session.commit()
            logger.warning("Workflow %s 的 owner 不存在，已停用", wf.id)
            return
        tenant = session.get(Tenant, wf.tenant_id)
        if tenant is None or not tenant.is_active:
            logger.warning("Workflow %s 的租户不可用，本次不执行", wf.id)
            return
        engine_svc = WorkflowEngine()
        due = _scan_due.get()
        if due is None:
            due = latest_due_time(wf.cron_expr, wf.timezone, datetime.now(UTC))
        await engine_svc.execute(
            session, wf, owner, triggered_by=TriggerSource.CRON.value, scheduled_due=due,
        )


async def start_scheduler() -> None:
    """启动调度器后台任务（幂等；不可运行时不创建任务）。"""
    global _task
    if _task is not None and not _task.done():
        return
    if not _is_scheduler_runnable():
        return
    _task = asyncio.create_task(_loop())
    logger.info("Workflow 调度器任务已创建")


async def stop_scheduler() -> None:
    """停止调度器后台任务（幂等）。"""
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        _task = None
    logger.info("Workflow 调度器已停止")
