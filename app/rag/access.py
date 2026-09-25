"""知识库权限判定。

检索面（``can_read_document``）由 ``RAG_KB_SCOPE`` 控制，默认只放行同租户当前版。
控制面（列表 / 详情 / 删除 / 重解析）走 ``can_control_document``：
成员仅自己的当前版；租户管理员看本租户全部版本；系统管理员可跨租户。
对话检索不使用控制面判定。资源级 ACL 仍为 Planned。
"""

from __future__ import annotations

from app.core.config import settings
from app.core.security import Role
from app.models.rag import Document
from app.models.user import User

VALID_KB_SCOPES = frozenset({"tenant", "uploader"})


def kb_scope() -> str:
    raw = (settings.RAG_KB_SCOPE or "tenant").strip().lower()
    return raw if raw in VALID_KB_SCOPES else "tenant"


def same_tenant(owner_tenant_id: str, user: User) -> bool:
    return owner_tenant_id == user.tenant_id


def is_kb_admin(user: User) -> bool:
    return user.role_enum in {Role.SYSTEM_ADMIN, Role.TENANT_ADMIN}


def can_read_document(doc: Document, user: User) -> bool:
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
    """删除 / 重解析。与控制面读权限相同。"""
    return can_control_document(doc, user)


def can_read_import(owner_user_id: str, owner_tenant_id: str, user: User) -> bool:
    if not same_tenant(owner_tenant_id, user):
        return False
    if kb_scope() == "tenant":
        return True
    if user.role_enum == Role.SYSTEM_ADMIN:
        return True
    return owner_user_id == user.id


def restrict_list_to_uploader(user: User) -> bool:
    """列表是否仍按上传者收窄。"""
    if kb_scope() == "tenant":
        return False
    return user.role_enum != Role.SYSTEM_ADMIN
