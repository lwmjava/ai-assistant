"""审计日志模型 — 可审计事件的数据结构。

定义：
- ``AuditAction``：审计事件类型枚举（登录/登出/CRUD/危险操作）
- ``AuditLog``：SQLModel 持久化模型，每条记录对应一个可审计事件

骨架阶段仅支持 DB 持久化；内核打磨阶段补充：
- 数据保留策略（自动清理超过 N 天的日志）
- 合规报表导出（CSV/JSON）
"""

import uuid
from datetime import UTC, datetime
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

    # ── 系统配置 ──
    SYSTEM_CONFIG_UPDATE = "system_config_update"
    FEATURE_FLAG_TOGGLE = "feature_flag_toggle"

    # ── CLI 危险操作 ──
    CLI_DANGEROUS_OP = "cli_dangerous_op"

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