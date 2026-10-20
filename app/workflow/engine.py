"""WorkflowEngine：cron 解析、任务执行编排与执行历史。

- ``next_run_time``：用可选依赖 ``croniter`` 解析标准 5 字段 cron，按任务时区返回
  下一次触发的 aware datetime；croniter 缺失时抛 ``CroniterNotAvailableError``。
- ``WorkflowEngine.execute``：编排一次执行（每次尝试前复核创建者 → 记录 → 调用 bridge →
  失败时按配置指数退避重试 → 落库结果 → 可选 webhook 回调），是手动触发与调度器共用的执行入口。
"""

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from sqlmodel import Session

from app.core.config import settings
from app.models.user import Tenant, User
from app.models.workflow import (
    ExecutionStatus,
    TriggerSource,
    Workflow,
    WorkflowExecution,
)
from app.workflow.bridge import WorkflowBridge
from app.workflow.control import ActiveExecution, database_key, finish, register

logger = logging.getLogger(__name__)
_RETRY_CEILING = 3
_DELAY_CEILING_SECONDS = 30.0


def workflow_retry_limit() -> int:
    """额外重试次数。默认 0，超过 3 的配置按 3。"""
    raw = settings.WORKFLOW_RETRY_MAX
    if isinstance(raw, bool) or not isinstance(raw, int):
        return 0
    return max(0, min(_RETRY_CEILING, raw))


def workflow_retry_delay(retry_number: int) -> float:
    """第 retry_number 次重试前的等待秒数，基数和单次等待都不超过 30 秒。"""

    def bounded(raw: object) -> float:
        if isinstance(raw, bool) or not isinstance(raw, int | float):
            return 0.0
        number = float(raw)
        if not math.isfinite(number) or number < 0:
            return 0.0
        return min(number, _DELAY_CEILING_SECONDS)

    base = bounded(settings.WORKFLOW_RETRY_BASE_SECONDS)
    cap = bounded(settings.WORKFLOW_RETRY_MAX_DELAY_SECONDS)
    delay = base * (2 ** (retry_number - 1))
    if not math.isfinite(delay) or delay < 0:
        return 0.0
    return min(delay, cap, _DELAY_CEILING_SECONDS)


class CroniterNotAvailableError(RuntimeError):
    """croniter 未安装时，调用 cron 计算抛此错误（而非启动崩溃）。"""


def _require_croniter():
    try:
        import croniter  # noqa: F401
    except ImportError:
        raise CroniterNotAvailableError(
            "未安装可选依赖 croniter，无法解析 cron 表达式；"
            "请 pip install croniter，或仅使用手动触发。"
        )
    return croniter


def next_run_time(cron_expr: str, tz: str, after: datetime | None = None) -> datetime:
    """计算 cron 表达式在给定时区下的下一次触发时间（aware datetime）。

    Args:
        cron_expr: 标准 5 字段 cron（分 时 日 月 周）。
        tz: IANA 时区名（如 Asia/Shanghai）。
        after: 基准时间；默认当前 UTC。

    Returns:
        目标时区的 aware datetime，表示下一次触发时刻。
    """
    croniter = _require_croniter()
    zone = ZoneInfo(tz)
    base = after or datetime.now(UTC)
    if base.tzinfo is None:
        base = base.replace(tzinfo=UTC)
    local_base = base.astimezone(zone)
    nxt = croniter.croniter(cron_expr, local_base).get_next(datetime)
    if nxt.tzinfo is None:
        nxt = nxt.replace(tzinfo=zone)
    return nxt


def latest_due_time(cron_expr: str, tz: str, now: datetime) -> datetime:
    """返回不晚于 now 的最近 cron 时刻，统一使用 aware UTC。

    候选语义沿用 croniter 的任务时区解析；微秒边界包含恰好到点的时刻。
    """
    croniter = _require_croniter()
    zone = ZoneInfo(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    now_utc = now.astimezone(UTC)
    local_base = now_utc.astimezone(zone) + timedelta(microseconds=1)
    candidate = croniter.croniter(cron_expr, local_base).get_prev(datetime)
    if candidate.tzinfo is None:
        candidate = candidate.replace(tzinfo=zone)
    due = candidate.astimezone(UTC)
    if due > now_utc:
        raise ValueError("Cron candidate is later than the scan time")
    return due


class WorkflowEngine:
    """执行单个工作流并维护其执行历史。"""

    def __init__(self, bridge: WorkflowBridge | None = None) -> None:
        self._bridge = bridge or WorkflowBridge()

    async def execute(
        self,
        session: Session,
        workflow: Workflow,
        owner: User,
        triggered_by: str = TriggerSource.MANUAL.value,
        prompt_override: str | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
    ) -> WorkflowExecution:
        """以快照总时限执行；合作式取消完成清理后由本调用唯一写入终态。"""
        workflow_id, tenant_id, owner_id = workflow.id, workflow.tenant_id, workflow.owner_id
        timeout = workflow.timeout_seconds
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 3600:
            raise ValueError("工作流超时必须为1至3600秒的整数")
        prompt = prompt_override or workflow.prompt_template
        execution = WorkflowExecution(
            workflow_id=workflow_id, tenant_id=tenant_id, triggered_by=triggered_by,
            status=ExecutionStatus.RUNNING.value, input=prompt, started_at=datetime.now(UTC),
        )
        session.add(execution)
        session.commit()
        session.refresh(execution)
        execution_id = execution.id
        started_at = execution.started_at
        db_key = database_key(session.get_bind())
        child = asyncio.create_task(self._attempts(
            session, workflow_id, tenant_id, owner_id, owner.id, prompt, sleeper or asyncio.sleep,
        ))
        entry = ActiveExecution(workflow_id, tenant_id, db_key, child)
        register(execution_id, entry)
        timer = asyncio.get_running_loop().call_later(timeout, entry.stop, "WORKFLOW_TIMEOUT")
        parent_cancelled = False
        outcome: tuple[str, str | None, str | None] = ("failed", None, "WORKFLOW_CANCELLED")
        try:
            # shield 防止父调用的重复取消再次打断子任务 finally。
            while True:
                try:
                    outcome = await asyncio.shield(child)
                    break
                except asyncio.CancelledError:
                    current = asyncio.current_task()
                    if current is not None and current.cancelling():
                        parent_cancelled = True
                    entry.stop("WORKFLOW_CANCELLED")
                    if child.done():
                        if not child.cancelled():
                            try:
                                outcome = child.result()
                            except Exception:
                                outcome = ("failed", None, "WORKFLOW_EXECUTION_FAILED")
                        break
                except Exception:
                    logger.exception("Workflow %s 执行异常", workflow_id)
                    outcome = ("failed", None, "WORKFLOW_EXECUTION_FAILED")
                    break
            if entry.reason is not None:
                outcome = ("failed", None, entry.reason)
            elif child.cancelled():
                outcome = ("failed", None, "WORKFLOW_CANCELLED")
            # 失败事务与未提交的尝试写入不能污染终态保存。
            if outcome[0] != "success":
                session.rollback()
            row = session.get(WorkflowExecution, execution_id, populate_existing=True)
            if row is None:
                raise RuntimeError("工作流执行记录不存在")
            row.status, row.output, row.error = outcome
            row.finished_at = datetime.now(UTC)
            if started_at is not None:
                started = started_at if started_at.tzinfo else started_at.replace(tzinfo=UTC)
                row.duration_ms = int((row.finished_at - started).total_seconds() * 1000)
            session.add(row)
            session.commit()
            session.refresh(row)
            execution = row
            logger.info("Workflow execution finished workflow_id=%s execution_id=%s status=%s reason=%s",
                        workflow_id, execution_id, row.status, entry.reason)
        except BaseException:
            entry.persistence_failed = True
            session.rollback()
            raise
        finally:
            timer.cancel()
            finish(execution_id, entry)
        if parent_cancelled:
            raise asyncio.CancelledError
        current_workflow = session.get(Workflow, workflow_id)
        if current_workflow is not None and current_workflow.webhook_url and execution.status == "success":
            await self._fire_webhook(current_workflow, execution)
        return execution

    async def _attempts(
        self, session: Session, workflow_id: str, tenant_id: str, owner_id: str,
        supplied_owner_id: str, prompt: str, pause: Callable[[float], Awaitable[None]],
    ) -> tuple[str, str | None, str | None]:
        """在同一执行中有限重试；每次真正调用前重新复核持久身份。"""
        limit = workflow_retry_limit()
        last_error = ""
        for index in range(limit + 1):
            if index:
                await pause(workflow_retry_delay(index))
            current = session.get(Workflow, workflow_id, populate_existing=True)
            actor = session.get(User, owner_id, populate_existing=True)
            tenant = session.get(Tenant, tenant_id, populate_existing=True)
            if (current is None or current.tenant_id != tenant_id or current.owner_id != owner_id
                    or supplied_owner_id != owner_id or actor is None or not actor.is_active
                    or actor.tenant_id != tenant_id):
                if current is not None:
                    current.enabled = False
                    current.suspended_owner = True
                    session.add(current)
                    session.commit()
                return "failed", None, "任务创建者已失效（禁用或移出租户），工作流已自动停用"
            if tenant is None or not tenant.is_active:
                return "failed", None, "WORKFLOW_TENANT_UNAVAILABLE"
            try:
                output = await self._bridge.execute(session, actor, prompt)
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                session.rollback()
                logger.warning("Workflow %s attempt failed type=%s", workflow_id, type(exc).__name__)
                continue
            return "success", output, None
        return "failed", None, f"已达到重试上限 {limit}：{last_error}"

    async def _fire_webhook(self, workflow: Workflow, execution: WorkflowExecution) -> None:
        """执行完成后回调 webhook（P2）；失败仅告警，不影响主结果。"""
        url = workflow.webhook_url
        if not url:
            return
        try:
            payload = {
                "workflow_id": workflow.id,
                "execution_id": execution.id,
                "status": execution.status,
                "output": execution.output,
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(url, json=payload)
        except Exception:  # noqa: BLE001
            logger.exception("Workflow %s webhook 回调失败（不影响主结果）", workflow.id)
