"""WorkflowScheduler：基于 asyncio 的轻量 cron 调度循环。

在应用 lifespan 启动（``start_scheduler``）与关闭（``stop_scheduler``）。循环周期性
扫描启用的工作流，按各自时区计算下一次触发时间，到点则触发执行（cron 来源）。
单任务 / 单 tick 异常不影响其他任务与循环本身。

croniter 未安装时调度器不启动（仅手动触发可用）；``WORKFLOW_ENABLED`` 关闭时同样不启动。
"""

import asyncio
import logging
from datetime import UTC, datetime

from sqlmodel import Session, select

from app.core.config import settings
from app.core.database import engine
from app.models.user import User
from app.models.workflow import TriggerSource, Workflow
from app.workflow.engine import WorkflowEngine, next_run_time

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None
# 进程级「上次触发时间」缓存，避免同周期 / 重启后立即重复触发。
_last_run: dict[str, datetime] = {}


def _is_scheduler_runnable() -> bool:
    if not settings.WORKFLOW_ENABLED:
        return False
    try:
        from app.workflow.engine import _require_croniter

        _require_croniter()
    except Exception:  # noqa: BLE001
        logger.warning("croniter 未安装，调度器不启动（仅手动触发可用）")
        return False
    return True


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
            select(Workflow)
            .where(Workflow.enabled.is_(True))
            .where(Workflow.suspended_owner.is_(False))
        ).all()
    for wf in workflows:
        try:
            nxt = next_run_time(wf.cron_expr, wf.timezone)
            last = _last_run.get(wf.id)
            if last is None:
                # 首轮基准：最近一次成功执行时间，否则取创建时间（不补跑历史）。
                last = _most_recent_execution_time(wf.id) or wf.created_at
            # SQLite 读回的 datetime 丢失时区信息，统一补回 UTC 再比较。
            if last is not None and last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
            if nxt <= now and last < nxt:
                _last_run[wf.id] = nxt
                await _trigger(wf)
        except Exception:  # noqa: BLE001
            logger.exception("Workflow %s 调度判定失败", wf.id)


def _most_recent_execution_time(workflow_id: str) -> datetime | None:
    """查询该工作流最近一次成功执行的结束时间（用于首轮触发基准）。"""
    from app.models.workflow import ExecutionStatus, WorkflowExecution

    with Session(engine) as session:
        row = session.exec(
            select(WorkflowExecution)
            .where(WorkflowExecution.workflow_id == workflow_id)
            .where(WorkflowExecution.status == ExecutionStatus.SUCCESS.value)
            .order_by(WorkflowExecution.finished_at.desc())
            .limit(1)
        ).first()
    return row.finished_at if row else None


async def _trigger(wf: Workflow) -> None:
    """加载 owner 并以 cron 来源触发一次执行；owner 失效则停用工作流。"""
    with Session(engine) as session:
        owner = session.get(User, wf.owner_id)
        if owner is None:
            wf.enabled = False
            wf.suspended_owner = True
            session.add(wf)
            session.commit()
            logger.warning("Workflow %s 的 owner 不存在，已停用", wf.id)
            return
        engine_svc = WorkflowEngine()
        await engine_svc.execute(
            session, wf, owner, triggered_by=TriggerSource.CRON.value
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
