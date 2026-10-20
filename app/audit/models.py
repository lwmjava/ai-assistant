"""审计日志模型 — 可审计事件的数据结构。

定义：
- ``AuditAction``：审计事件类型枚举（登录/登出/CRUD/危险操作）
- ``AuditLog``：SQLModel 持久化模型，每条记录对应一个可审计事件

当前实现：DB 持久化。可按需扩展：
- 数据保留策略（自动清理超过 N 天的日志）
- 合规报表导出（CSV/JSON）
"""

import uuid
from enum import Enum

from sqlmodel import JSON, Column, Field, SQLModel

from app.models.base import TimestampMixin


def _uuid() -> str:
    """生成短 UUID 主键。"""
    return uuid.uuid4().hex


class AuditAction(str, Enum):
    """审计事件类型。

    按 PRD §5.3.2 权限矩阵 + 设计文档 §5.1.6 危险操作清单定义。
    """

    # ── 认证 ──
    USER_LOGIN = "user_login"
    USER_LOGOUT = "user_logout"
    USER_TOKEN_REFRESH = "user_token_refresh"

    # ── 用户管理 ──
    USER_CREATE = "user_create"
    USER_UPDATE = "user_update"
    USER_DELETE = "user_delete"
    USER_DISABLE = "user_disable"
    USER_ENABLE = "user_enable"
    USER_PASSWORD_RESET = "user_password_reset"
    USER_ROLE_CHANGE = "user_role_change"

    # ── 租户管理 ──
    TENANT_CREATE = "tenant_create"
    TENANT_UPDATE = "tenant_update"
    TENANT_DELETE = "tenant_delete"
    TENANT_ACTIVATE = "tenant_activate"
    TENANT_DEACTIVATE = "tenant_deactivate"
    QUOTA_UPDATE = "quota_update"
    EXPORT_REQUESTED = "export_requested"

    # ── 对话 ──
    CONVERSATION_CREATE = "conversation_create"
    CONVERSATION_DELETE = "conversation_delete"

    # ── 知识库 ──
    KNOWLEDGE_BASE_UPLOAD = "knowledge_base_upload"
    KNOWLEDGE_BASE_DELETE = "knowledge_base_delete"
    KNOWLEDGE_BASE_REINDEX = "knowledge_base_reindex"

    # ── 工作流 ──
    WORKFLOW_CREATE = "workflow_create"
    WORKFLOW_UPDATE = "workflow_update"
    WORKFLOW_DELETE = "workflow_delete"
    WORKFLOW_EXECUTE = "workflow_execute"

    # ── 技能 ──
    SKILL_CREATE = "skill_create"
    SKILL_UPDATE = "skill_update"
    SKILL_DISABLE = "skill_disable"
    SKILL_ENABLE = "skill_enable"
    SKILL_DELETE = "skill_delete"

    # ── 系统配置 ──
    SYSTEM_CONFIG_UPDATE = "system_config_update"
    FEATURE_FLAG_TOGGLE = "feature_flag_toggle"

    # ── CLI 危险操作 ──
    CLI_DANGEROUS_OP = "cli_dangerous_op"

    # ── 工具调用（PAGT-002）──
    TOOL_CALL = "tool_call"

    # ── 其他 ──
    OTHER = "other"


class AuditLog(SQLModel, TimestampMixin, table=True):
    """审计日志持久化模型。

    每条记录记录一个可审计事件，包含：
    - 谁（user_id / tenant_id）
    - 做了什么（action）
    - 对哪个资源（resource_type / resource_id）
    - 详细上下文（details — JSON）
    - 来自哪里（ip_address / user_agent）
    """

    __tablename__ = "audit_logs"

    id: str = Field(default_factory=_uuid, primary_key=True)
    # 操作者信息（可为空：匿名操作 / 系统内部操作）
    user_id: str | None = Field(default=None, index=True)
    tenant_id: str | None = Field(default=None, index=True)
    # 事件类型
    action: str = Field(index=True)  # AuditAction 枚举值
    # 资源信息
    resource_type: str | None = Field(default=None, index=True)  # user / tenant / conversation / ...
    resource_id: str | None = Field(default=None)
    # 详细上下文（JSON 文本，避免字段爆炸）
    details: str | None = Field(default=None, sa_column=Column(JSON))
    # 客户端信息
    ip_address: str | None = Field(default=None)
    user_agent: str | None = Field(default=None)

    # ``created_at`` 由 TimestampMixin 提供，用作审计时间线。
    # ``updated_at`` 由 TimestampMixin 提供，审计日志原则上不可变，但保留该字段以兼容。

    def __repr__(self) -> str:
        return (
            f"AuditLog(id={self.id!r}, action={self.action!r}, "
            f"user_id={self.user_id!r}, resource={self.resource_type!r}/{self.resource_id!r})"
        )


class WorkflowOwnerAlert(SQLModel, TimestampMixin, table=True):
    """创建者失效告警：工作流因 owner 被禁用 / 移出租户 / 删除而自动停用时落库。

    与审计日志分离：审计记录「谁在何时做了什么操作」，本表面向
    tenant_admin 提供「哪个工作流、因原创建者何故被停用」的可查询告警。
    只落库，不外发邮件 / Webhook（见 FLOW-005 契约）。
    """

    __tablename__ = "workflow_owner_alerts"

    id: str = Field(default_factory=_uuid, primary_key=True)
    # 告警归属租户，多租户隔离查询键。
    tenant_id: str = Field(index=True)
    workflow_id: str = Field(index=True)
    # 名称快照：工作流随后可能被删除，快照保留供管理员追溯。
    workflow_name: str
    owner_id: str = Field(index=True)
    # 用户名快照：owner 被删除时为 None。
    owner_username: str | None = Field(default=None)
    # owner_disabled | owner_moved_out | owner_deleted | owner_invalid
    reason: str = Field(index=True)
    detail: str | None = Field(default=None)
