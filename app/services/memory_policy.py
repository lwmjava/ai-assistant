"""Single authorization policy for conversation memory and snapshots."""

from sqlmodel import Session

from app.models.conversation import Conversation
from app.models.user import Tenant, User

MEMORY_POLICY_VERSION = "conversation-memory-v1"


class MemoryPolicyError(PermissionError):
    pass


def assert_memory_access(
    session: Session, user: User, conversation: Conversation
) -> None:
    current = session.get(User, user.id)
    tenant = session.get(Tenant, user.tenant_id)
    same_identity = current is not None and (
        current.tenant_id,
        current.role,
        current.token_version,
        current.is_active,
    ) == (user.tenant_id, user.role, user.token_version, True)
    same_tenant = conversation.tenant_id == user.tenant_id
    owns = conversation.user_id == user.id
    same_tenant_system_admin = user.role == "system_admin" and same_tenant
    if (
        not same_identity
        or tenant is None
        or not tenant.is_active
        or conversation.deleted_at is not None
        or not same_tenant
        or not (owns or same_tenant_system_admin)
    ):
        raise MemoryPolicyError("conversation memory is unavailable")
