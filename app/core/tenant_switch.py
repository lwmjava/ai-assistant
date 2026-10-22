"""单进程内租户切换：锁住用户行，避免并发切换交错写。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import update
from sqlmodel import Session, col

from app.core.idempotency import switch_tenant_keys
from app.models.user import Tenant, User
from app.services.membership import (
    InactiveTenantError,
    NotMemberError,
    get_membership,
)


def switch_tenant_locked(session: Session, user: User, tenant_id: str) -> User:
    """是成员且租户未停用时才改当前租户并递增 token_version。

    同一用户并发切换时串行化；同一目标租户的短时重复请求复用已完成结果。
    """
    cache_key = f"{user.id}:{tenant_id}"
    cached_id = switch_tenant_keys.get(cache_key)
    if cached_id == user.id and user.tenant_id == tenant_id:
        session.refresh(user)
        return user

    if get_membership(session, user.id, tenant_id) is None:
        raise NotMemberError(tenant_id)
    tenant = session.get(Tenant, tenant_id)
    if tenant is None or not tenant.is_active:
        raise InactiveTenantError(tenant_id)

    now = datetime.now(UTC)
    result = session.execute(
        update(User).where(col(User.id) == user.id).values(updated_at=now)
    )
    if int(getattr(result, "rowcount", 0) or 0) == 0:
        raise NotMemberError(tenant_id)
    locked = session.get(User, user.id)
    if locked is None:
        raise NotMemberError(tenant_id)
    if locked.tenant_id == tenant_id:
        switch_tenant_keys.put_if_absent(cache_key, locked.id)
        return locked

    locked.tenant_id = tenant_id
    locked.token_version += 1
    session.add(locked)
    session.commit()
    session.refresh(locked)
    switch_tenant_keys.put_if_absent(cache_key, locked.id)
    return locked
