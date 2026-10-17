"""知识库权限判定。

检索面（``can_read_document``）由 ``RAG_KB_SCOPE`` 控制，默认只放行同租户当前版。
控制面（列表 / 详情 / 删除 / 重解析）走 ``can_control_document``：
成员仅自己的当前版；租户管理员看本租户全部版本；系统管理员可跨租户。
对话检索不使用控制面判定。资源级 ACL 仍为 Planned。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings
from app.core.security import Role
from app.models.rag import Document
from app.models.user import User

VALID_KB_SCOPES = frozenset({"tenant", "uploader"})


def kb_scope() -> str:
    raw = (settings.RAG_KB_SCOPE or "tenant").strip().lower()
    return raw if raw in VALID_KB_SCOPES else "tenant"


@dataclass(frozen=True)
class ReadScope:
    """检索面的有效读范围。

    ``uploader_id`` 为 ``None`` 表示同租户全部当前版可见（默认是 tenant 模式，
    或 uploader 模式下的系统管理员）；非空则只放行该上传者的文档。
    软删、非当前版与跨租户由检索 SQL 的既有条件承担，不在此重复判定。
    """

    tenant_id: str
    uploader_id: str | None = None

    def assert_subject_tenant(self, tenant_id: str) -> None:
        """校验使用该范围的主体属于同一租户。

        范围一旦被跨租户复用就等于放宽隔离，因此直接报错而不是静默忽略。
        """
        if tenant_id != self.tenant_id:
            raise ValueError("检索读范围与主体租户不一致，已拒绝本次检索")


def read_scope_for(user: User | None) -> ReadScope | None:
    """按鉴权主体推导检索读范围；无主体时返回 ``None``。

    ``None`` **等同同租户全可读**（沿用仅按租户过滤的历史行为），不构成 uploader
    隔离。因此面向终端用户的入口必须显式传入主体，不能依赖默认；只有无终端鉴权
    主体的后台路径（导入任务、评测脚本）才允许使用 ``None``。
    """
    if user is None:
        return None
    uploader_id: str | None = None
    if kb_scope() == "uploader" and user.role_enum != Role.SYSTEM_ADMIN:
        uploader_id = user.id
    return ReadScope(tenant_id=user.tenant_id, uploader_id=uploader_id)


def same_tenant(owner_tenant_id: str, user: User) -> bool:
    return owner_tenant_id == user.tenant_id


def is_kb_admin(user: User) -> bool:
    return user.role_enum in {Role.SYSTEM_ADMIN, Role.TENANT_ADMIN}


def can_read_document(doc: Document, user: User) -> bool:
    if doc.deleted_at is not None:
        return False
    if not same_tenant(doc.tenant_id, user):
        return False
    if not doc.is_current:
        return False
    if kb_scope() == "tenant":
        return True
    if user.role_enum == Role.SYSTEM_ADMIN:
        return True
    return doc.user_id == user.id


def can_control_document(doc: Document, user: User) -> bool:
    """列表、详情、删除、重解析。"""
    if user.role_enum == Role.SYSTEM_ADMIN:
        return True
    if not same_tenant(doc.tenant_id, user):
        return False
    if user.role_enum == Role.TENANT_ADMIN:
        return True
    return doc.user_id == user.id and bool(doc.is_current)


def can_write_document(doc: Document, user: User) -> bool:
    """删除 / 重解析。已软删的文档不能再删或重解析。"""
    if doc.deleted_at is not None:
        return False
    return can_control_document(doc, user)


def can_read_import(owner_user_id: str, owner_tenant_id: str, user: User) -> bool:
    if user.role_enum == Role.SYSTEM_ADMIN:
        return True
    if not same_tenant(owner_tenant_id, user):
        return False
    if kb_scope() == "tenant":
        return True
    return owner_user_id == user.id


def restrict_list_to_uploader(user: User) -> bool:
    """列表是否仍按上传者收窄。"""
    if kb_scope() == "tenant":
        return False
    return user.role_enum != Role.SYSTEM_ADMIN
