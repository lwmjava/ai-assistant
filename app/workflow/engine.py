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
from app.models.user import User
from app.models.workflow import (
    ExecutionStatus,
    TriggerSource,
    Workflow,
    WorkflowExecution,
)
from app.workflow.bridge import WorkflowBridge

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
        """执行工作流一次，失败时在同一条记录内按配置重试。

        流程：
        1. 创建 PENDING 执行记录；
        2. 每次调用 bridge 前重新读取创建者，失效则停用并返回 FAILED；
        3. 第一次之后按指数退避等待，再调用 bridge；
        4. 置 SUCCESS / FAILED + output / error + finished_at + duration_ms；
        5. 仅最终成功时回调 webhook；
        6. 提交并返回。
        """
        pause = sleeper or asyncio.sleep
        prompt = prompt_override or workflow.prompt_template
        execution = WorkflowExecution(
            workflow_id=workflow.id,
            tenant_id=workflow.tenant_id,
            triggered_by=triggered_by,
            status=ExecutionStatus.PENDING.value,
            input=prompt,
        )
        session.add(execution)
        session.commit()
        session.refresh(execution)

        attempts = workflow_retry_limit() + 1
        last_error = ""
        for index in range(attempts):
            current = session.get(Workflow, workflow.id)
            actor = session.get(User, owner.id)
            if current is None or actor is None or not actor.is_active or actor.tenant_id != workflow.tenant_id:
                if current is not None:
                    current.enabled = False
                    current.suspended_owner = True
                    session.add(current)
                execution.status = ExecutionStatus.FAILED.value
                execution.error = "任务创建者已失效（禁用或移出租户），工作流已自动停用"
                execution.finished_at = datetime.now(UTC)
                session.add(execution)
                session.commit()
                session.refresh(execution)
                logger.warning("Workflow %s 因 owner 失效已停用", workflow.id)
                return execution
            workflow = current
            owner = actor
            if index == 0:
                execution.status = ExecutionStatus.RUNNING.value
                execution.started_at = datetime.now(UTC)
                session.add(execution)
                session.commit()
            else:
                await pause(workflow_retry_delay(index))
            try:
                output = await self._bridge.execute(session, owner, prompt)
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                logger.exception("Workflow %s 执行失败", workflow.id)
                continue
            execution.status = ExecutionStatus.SUCCESS.value
            execution.output = output
            execution.error = None
            break
        else:
            execution.status = ExecutionStatus.FAILED.value
            execution.error = f"已达到重试上限 {workflow_retry_limit()}：{last_error}"

        execution.finished_at = datetime.now(UTC)
        if execution.started_at is not None:
            # SQLite 读回的 datetime 会丢失时区信息变为 naive，统一补回 UTC 再相减。
            started = execution.started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            delta = execution.finished_at - started
            execution.duration_ms = int(delta.total_seconds() * 1000)
        session.add(execution)
        session.commit()
        session.refresh(execution)

        if workflow.webhook_url and execution.status == ExecutionStatus.SUCCESS.value:
            await self._fire_webhook(workflow, execution)

        return execution

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
