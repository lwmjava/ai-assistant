"""知识库权限判定（ADR-0001）。

读路径由 ``RAG_KB_SCOPE`` 控制：
- ``tenant``（本阶段 To-Be 默认）：同租户 ``is_current=true`` 可读；
- ``uploader``：回滚到上传者私有列表/详情（系统管理员仍可见同租户当前文档）。

写路径（删除 / 重解析）不看该开关：成员仅自己的文档；租户/系统管理员可写同租户当前文档。
跨租户一律拒绝。资源级 ACL 仍为 Planned，本模块不实现。
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


def can_write_document(doc: Document, user: User) -> bool:
    """删除 / 重解析。"""
    if not same_tenant(doc.tenant_id, user):
        return False
    if is_kb_admin(user):
        return bool(doc.is_current)
    return doc.user_id == user.id


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
