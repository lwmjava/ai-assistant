"""请求内技能授权租约与恢复校验；运行时不持有数据库访问逻辑。"""

import json
from copy import deepcopy
from datetime import UTC, datetime

from sqlmodel import Session

from app.agents.skills.base import (
    SkillBindingRejected,
    SkillContext,
    SkillMatch,
    SkillMode,
    SkillRuntime,
    valid_skill_binding,
)
from app.agents.skills.manager import SkillManager
from app.agents.tools.base import ToolRegistry
from app.core.config import settings
from app.core.security import Role
from app.models.user import Tenant, User


def _current_manifests(fresh: Session, user: User, principal: tuple):
    from app.services.skill_service import manifests_for_chat

    development = (
        not settings.AUTH_ENABLED and principal[:3] == (
            "__auth_disabled__", "__auth_disabled__", Role.SYSTEM_ADMIN.value,
        )
    )
    actor = user if development else fresh.get(User, principal[0])
    if actor is None or not actor.is_active or (
        actor.id, actor.tenant_id, actor.role, actor.token_version,
    ) != principal:
        raise SkillBindingRejected()
    if not development:
        tenant = fresh.get(Tenant, actor.tenant_id)
        if actor.role_enum is not Role.SYSTEM_ADMIN and (tenant is None or not tenant.is_active):
            raise SkillBindingRejected()
    return {item.name: item for item in manifests_for_chat(fresh, actor)}


def _verify_versions(fresh: Session, user: User, principal: tuple, versions: list[dict]) -> None:
    if not settings.SKILL_ENABLED:
        raise SkillBindingRejected()
    current = _current_manifests(fresh, user, principal)
    for identity in versions:
        actual = current.get(identity["name"])
        if actual is None or not actual.enabled or SkillManager._identity(actual) != identity:
            raise SkillBindingRejected()


def make_skill_runtime(
    session: Session, user: User, context: SkillContext | None, tools: ToolRegistry | None,
) -> SkillRuntime:
    """冻结已激活版本，注入和新工具执行前用独立会话重读授权。"""
    if context is None:
        return SkillRuntime()
    frozen = deepcopy(context)
    principal = (user.id, user.tenant_id, user.role, user.token_version)
    bind = session.get_bind()

    def verify() -> None:
        with Session(bind) as fresh:
            _verify_versions(fresh, user, principal, frozen.versions)

    runtime = SkillRuntime(frozen, verify)
    runtime.registered_tool_names = frozenset(tool.name for tool in tools.all()) if tools else frozenset()
    if not valid_skill_binding(runtime.binding()) or not frozen.versions:
        raise SkillBindingRejected()
    runtime.check()
    runtime.record("service", "matched")
    if frozen.mode is SkillMode.TOOL:
        available = SkillManager().collect_tools(frozen, tools)
        runtime.record("service", "bound" if available else "missing_binding")
    return runtime


def validate_resume_skill_binding(session: Session, user: User, snapshot: dict) -> None:
    """在记忆压缩、模型匹配和恢复写入前拒绝无法证明有效的旧技能绑定。"""
    binding = snapshot.get("skill_binding")
    if not isinstance(binding, dict) or not valid_skill_binding(binding):
        raise SkillBindingRejected()
    if binding["skills"]:
        principal = (user.id, user.tenant_id, user.role, user.token_version)
        with Session(session.get_bind()) as fresh:
            _verify_versions(fresh, user, principal, binding["skills"])
        # 从当前授权声明重建模式和绑定，不能信任历史 JSON 自称的工具名单。
        restore_skill_context(session, user, snapshot)


def restore_skill_context(session: Session, user: User, snapshot: dict) -> SkillContext | None:
    """同版本恢复原选择，不重新向模型提议技能，也不消费快照中的正文或权限。"""
    from app.services.skill_service import fence_untrusted_skill

    binding = snapshot.get("skill_binding")
    if not isinstance(binding, dict) or not valid_skill_binding(binding):
        raise SkillBindingRejected()
    if not binding["skills"]:
        return None
    if len(binding["skills"]) != 1 or not settings.SKILL_ENABLED:
        raise SkillBindingRejected()
    principal = (user.id, user.tenant_id, user.role, user.token_version)
    with Session(session.get_bind()) as fresh:
        current = _current_manifests(fresh, user, principal)
        identity = binding["skills"][0]
        actual = current.get(identity["name"])
        if actual is None or not actual.enabled or SkillManager._identity(actual) != identity:
            raise SkillBindingRejected()
        context = SkillManager().activate([SkillMatch(actual, confidence=1.0)])
    if SkillRuntime(context).binding() != binding:
        raise SkillBindingRejected()
    if context.origin != "builtin" and context.prompt_injection:
        context.prompt_injection = fence_untrusted_skill(context.prompt_injection, context.origin)
    return context


def precheck_resume_skill_binding(session: Session, user: User, conversation_id: str, message_id: str) -> None:
    """先识别技能兼容冲突，避免兼容存储读取器把冲突快照当损坏行删除。"""
    from app.services.agent_state_service import RESUMABLE_STATUS, AgentStateService

    row = AgentStateService(session, tenant_id=user.tenant_id, user_id=user.id)._find_row(
        conversation_id, message_id,
    )
    if row is None or row.status not in RESUMABLE_STATUS:
        return
    if row.expires_at is not None and row.expires_at < datetime.now(UTC).replace(tzinfo=None):
        return
    try:
        snapshot = json.loads(row.state_json)
    except (json.JSONDecodeError, TypeError):
        return  # 与技能绑定无关的损坏 JSON 仍交给原存储层处理。
    if isinstance(snapshot, dict):
        validate_resume_skill_binding(session, user, snapshot)
