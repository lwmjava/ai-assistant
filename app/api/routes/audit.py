"""审计日志 Admin API。

仅 system_admin / system_viewer 可访问。
提供审计日志查询、过滤、分页功能。

端点：
- ``GET /api/admin/audit-logs`` — 查询审计日志（支持多条件过滤 + 分页）
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, field_validator
from sqlmodel import Session, col, select

from app.api.deps import get_current_user, get_db
from app.audit.models import AuditAction, AuditLog
from app.core.security import Role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/audit-logs", tags=["admin-audit"])


# ── 响应模型 ──


class AuditLogOut(BaseModel):
    """审计日志条目（对外输出）。"""

    id: str
    user_id: str | None
    tenant_id: str | None
    action: str
    resource_type: str | None
    resource_id: str | None
    details: str | None
    ip_address: str | None
    user_agent: str | None
    created_at: str

    @classmethod
    def from_orm(cls, log: AuditLog) -> "AuditLogOut":
        return cls(
            id=log.id,
            user_id=log.user_id,
            tenant_id=log.tenant_id,
            action=log.action,
            resource_type=log.resource_type,
            resource_id=log.resource_id,
            details=log.details,
            ip_address=log.ip_address,
            user_agent=log.user_agent,
            created_at=log.created_at.isoformat(),
        )


class AuditLogPage(BaseModel):
    """分页审计日志响应。"""

    items: list[AuditLogOut]
    total: int
    page: int
    page_size: int


# ── 权限守卫 ──


def _require_system_admin_or_viewer(user=Depends(get_current_user)):
    """仅允许 system_admin / system_viewer 访问审计日志。"""
    if user.role_enum not in (Role.SYSTEM_ADMIN, Role.SYSTEM_VIEWER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="仅系统管理员可查看审计日志",
        )
    return user


# ── 端点 ──


@router.get("", response_model=AuditLogPage)
def list_audit_logs(
    *,
    # 过滤条件
    action: str | None = Query(None, description="按事件类型过滤（如 user_login）"),
    user_id: str | None = Query(None, description="按操作者 ID 过滤"),
    tenant_id: str | None = Query(None, description="按租户 ID 过滤"),
    resource_type: str | None = Query(None, description="按资源类型过滤（如 user / tenant）"),
    resource_id: str | None = Query(None, description="按资源 ID 过滤"),
    # 时间范围
    since: str | None = Query(None, description="起始时间（ISO 8601），含此时间点"),
    until: str | None = Query(None, description="结束时间（ISO 8601），含此时间点"),
    # 分页
    page: int = Query(1, ge=1, description="页码（1-based）"),
    page_size: int = Query(50, ge=1, le=200, description="每页条数（上限 200）"),
    # 依赖
    _admin: None = Depends(_require_system_admin_or_viewer),
    session: Session = Depends(get_db),
) -> AuditLogPage:
    """查询审计日志（支持多条件过滤 + 分页）。

    默认按创建时间倒序排列。
    """
    # 构建查询
    stmt = select(AuditLog)

    if action:
        stmt = stmt.where(AuditLog.action == action)
    if user_id:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if tenant_id:
        stmt = stmt.where(AuditLog.tenant_id == tenant_id)
    if resource_type:
        stmt = stmt.where(AuditLog.resource_type == resource_type)
    if resource_id:
        stmt = stmt.where(AuditLog.resource_id == resource_id)

    # 时间范围过滤
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
            stmt = stmt.where(AuditLog.created_at >= since_dt)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效的 since 时间格式: {since}")

    if until:
        try:
            until_dt = datetime.fromisoformat(until)
            stmt = stmt.where(AuditLog.created_at <= until_dt)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效的 until 时间格式: {until}")

    # 统计总数
    count_stmt = select(col(AuditLog.id)).select_from(AuditLog)
    # 复用过滤条件（手动构建以保持一致性）
    count_stmt = _apply_filters_to_select(count_stmt, action, user_id, tenant_id, resource_type, resource_id, since, until)
    total = len(session.exec(count_stmt).all())

    # 分页 + 排序
    offset = (page - 1) * page_size
    stmt = stmt.order_by(col(AuditLog.created_at).desc()).offset(offset).limit(page_size)

    logs = session.exec(stmt).all()
    items = [AuditLogOut.from_orm(log) for log in logs]

    return AuditLogPage(items=items, total=total, page=page, page_size=page_size)


def _apply_filters_to_select(
    stmt,
    action: str | None,
    user_id: str | None,
    tenant_id: str | None,
    resource_type: str | None,
    resource_id: str | None,
    since: str | None,
    until: str | None,
):
    """将过滤条件应用到 select 语句（用于计数查询）。"""
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if user_id:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if tenant_id:
        stmt = stmt.where(AuditLog.tenant_id == tenant_id)
    if resource_type:
        stmt = stmt.where(AuditLog.resource_type == resource_type)
    if resource_id:
        stmt = stmt.where(AuditLog.resource_id == resource_id)
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
            stmt = stmt.where(AuditLog.created_at >= since_dt)
        except ValueError:
            pass
    if until:
        try:
            until_dt = datetime.fromisoformat(until)
            stmt = stmt.where(AuditLog.created_at <= until_dt)
        except ValueError:
            pass
    return stmt