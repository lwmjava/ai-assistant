"""WorkflowEngine：cron 解析、任务执行编排与执行历史。

- ``next_run_time``：用可选依赖 ``croniter`` 解析标准 5 字段 cron，按任务时区返回
  下一次触发的 aware datetime；croniter 缺失时抛 ``CroniterNotAvailableError``。
- ``WorkflowEngine.execute``：编排一次执行（owner 失效校验 → 记录 → 调用 bridge →
  落库结果 → 可选 webhook 回调），是手动触发与调度器共用的执行入口。
"""

import logging
from datetime import UTC, datetime
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
    ) -> WorkflowExecution:
        """执行工作流一次，落库执行记录并返回。

        流程：
        1. owner 失效校验（禁用 / 不在原租户）→ 标记 suspended_owner，返回 FAILED；
        2. 创建 PENDING 执行记录，置 RUNNING + started_at；
        3. 调用 bridge 执行 Prompt；
        4. 置 SUCCESS / FAILED + output / error + finished_at + duration_ms；
        5. 若有 webhook_url 且成功，异步回调（失败不影响主结果）；
        6. 提交并返回。
        """
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

        # 1. owner 失效校验
        if not owner.is_active or owner.tenant_id != workflow.tenant_id:
            workflow.enabled = False
            workflow.suspended_owner = True
            session.add(workflow)
            execution.status = ExecutionStatus.FAILED.value
            execution.error = "任务创建者已失效（禁用或移出租户），工作流已自动停用"
            execution.finished_at = datetime.now(UTC)
            session.add(execution)
            session.commit()
            session.refresh(execution)
            logger.warning("Workflow %s 因 owner 失效已停用", workflow.id)
            return execution

        # 2. 进入运行
        execution.status = ExecutionStatus.RUNNING.value
        execution.started_at = datetime.now(UTC)
        session.add(execution)
        session.commit()

        # 3. 执行
        try:
            output = await self._bridge.execute(session, owner, prompt)
            execution.status = ExecutionStatus.SUCCESS.value
            execution.output = output
        except Exception as exc:  # noqa: BLE001
            execution.status = ExecutionStatus.FAILED.value
            execution.error = str(exc)
            logger.exception("Workflow %s 执行失败", workflow.id)

        # 4. 收尾
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

        # 5. webhook 回调（P2）
        if workflow.webhook_url and execution.status == ExecutionStatus.SUCCESS.value:
            await self._fire_webhook(workflow, execution)

        return execution

    async def _fire_webhook(self, workflow: Workflow, execution: WorkflowExecution) -> None:
        """执行完成后回调 webhook（P2）；失败仅告警，不影响主结果。"""
        try:
            payload = {
                "workflow_id": workflow.id,
                "execution_id": execution.id,
                "status": execution.status,
                "output": execution.output,
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(workflow.webhook_url, json=payload)
        except Exception:  # noqa: BLE001
            logger.exception("Workflow %s webhook 回调失败（不影响主结果）", workflow.id)
