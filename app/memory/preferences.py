"""用户级跨会话记忆开关。部署开关关闭时，个人开关不能把它打开。"""

from sqlmodel import Session

from app.core.config import settings
from app.models.user import User
from app.models.user_memory_preference import UserMemoryPreference


def user_memory_enabled(session: Session, user: User) -> bool:
    row = session.get(UserMemoryPreference, user.id)
    if row is None:
        return True
    return row.tenant_id == user.tenant_id and bool(row.enabled)


def memory_access_open(session: Session, user: User) -> bool:
    return bool(settings.MEMORY_CROSS_SESSION_ENABLED) and user_memory_enabled(session, user)


def set_user_memory_enabled(session: Session, user: User, enabled: bool) -> UserMemoryPreference:
    row = session.get(UserMemoryPreference, user.id)
    if row is None:
        row = UserMemoryPreference(user_id=user.id, tenant_id=user.tenant_id, enabled=enabled)
    else:
        row.tenant_id = user.tenant_id
        row.enabled = enabled
    session.add(row)
    session.commit()
    session.refresh(row)
    return row
