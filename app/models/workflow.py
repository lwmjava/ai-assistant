"""工作流与执行历史模型（多租户隔离）。

Workflow 描述一个「定时任务」：按 cron 表达式周期性触发 Agent 执行一段 Prompt 模板，
以创建者（owner）身份运行并注入其租户上下文。WorkflowExecution 记录每一次执行的
输入 / 输出 / 状态 / 耗时，供用户回溯（WFL-02）。
"""

import uuid
from datetime import UTC, datetime
from enum import Enum

from sqlmodel import Field, Relationship, SQLModel

from app.models.base import TimestampMixin


def _uuid() -> str:
    """生成短 UUID 主键（十六进制字符串）。"""
    return uuid.uuid4().hex


class ExecutionStatus(str, Enum):
    """单次执行的状态机。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class TriggerSource(str, Enum):
    """执行触发来源。"""

    MANUAL = "manual"  # 用户手动触发（WFL-03）
    CRON = "cron"  # 调度器定时触发


class Workflow(SQLModel, TimestampMixin, table=True):
    """工作流（定时任务）：归属租户，以创建者身份执行 Prompt 模板。"""

    __tablename__ = "workflows"

    id: str = Field(default_factory=_uuid, primary_key=True)
    tenant_id: str = Field(index=True)
    # 创建者：执行时以其身份运行（注入 user_id + tenant_id），配额计入其租户。
    owner_id: str = Field(index=True)
    name: str
    description: str | None = Field(default=None)
    # 标准 5 字段 cron 表达式（分 时 日 月 周），由 croniter 解析。
    cron_expr: str
    # 执行时注入 Agent 管线的 Prompt；可包含简单占位符，未来支持上下文注入。
    prompt_template: str
    # cron 解析时区（IANA 名称），默认取系统配置。
    timezone: str = Field(default="Asia/Shanghai")
    # 是否启用调度；创建者失效时会被自动置 False 并标记 suspended_owner。
    enabled: bool = Field(default=True)
    # 创建者被禁用 / 移出租户 → 自动禁用并标记，需 tenant_admin 介入。
    suspended_owner: bool = Field(default=False)
    # 执行完成后的回调 URL（P2）；为空则不回调。
    webhook_url: str | None = Field(default=None)

    executions: list["WorkflowExecution"] = Relationship(
        back_populates="workflow", cascade_delete=True
    )


class WorkflowExecution(SQLModel, TimestampMixin, table=True):
    """工作流的一次执行记录（审计 / 历史回溯）。"""

    __tablename__ = "workflow_executions"

    id: str = Field(default_factory=_uuid, primary_key=True)
    workflow_id: str = Field(foreign_key="workflows.id", index=True)
    # 冗余存储 tenant_id 便于按租户隔离查询，避免每次 join。
    tenant_id: str = Field(index=True)
    triggered_by: str = Field(default=TriggerSource.MANUAL.value)  # manual | cron
    status: str = Field(default=ExecutionStatus.PENDING.value)
    # 本次实际执行的输入（解析后的 Prompt）与模型输出 / 错误。
    input: str | None = Field(default=None)
    output: str | None = Field(default=None)
    error: str | None = Field(default=None)
    # 执行起止时间与耗时（毫秒），独立于 created_at 以精确反映运行窗口。
    started_at: datetime | None = Field(default=None)
    finished_at: datetime | None = Field(default=None)
    duration_ms: int | None = Field(default=None)

    workflow: Workflow | None = Relationship(back_populates="executions")
