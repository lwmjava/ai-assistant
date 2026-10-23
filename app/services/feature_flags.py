"""Feature Flag 管理服务。

提供代码默认值与数据库覆盖值的合并视图，支持系统管理员持久化切换。
切换后即时反映到 ``app.core.config.settings``，使运行时代码按新值执行。
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlmodel import Session, select

from app.audit.models import AuditAction, AuditLog
from app.core.config import settings
from app.models.feature_flag import FeatureFlag

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FlagView:
    """单个 Feature Flag 的合并后视图。"""

    key: str
    value: bool
    default: bool
    description: str
    overridden: bool
    updated_at: datetime | None


#: 代码层登记的默认开关。数据库仅保存覆盖值，描述与默认值在这里维护。
#: key 使用小写，映射到 ``app.core.config.Settings`` 的属性名为大写。
_CODE_DEFAULTS: dict[str, tuple[bool, str]] = {
    "rag_enabled": (True, "是否将检索上下文注入对话管线"),
    "memory_enabled": (True, "是否启用对话记忆压缩"),
    "skill_enabled": (True, "是否启用 YAML 声明式技能系统"),
    "security_injection_detection": (True, "是否启用 Prompt 注入检测"),
    "security_block_on_injection": (False, "检测到注入时是否阻断请求（默认仅告警）"),
    "audit_enabled": (True, "是否持久化审计日志"),
    "evolution_enabled": (False, "是否启用进化系统"),
    "mcp_enabled": (False, "是否启用 MCP 客户端"),
    "workflow_enabled": (False, "是否启用工作流引擎"),
    "rag_import_enabled": (True, "是否启用异步文档导入平台"),
}


def _settings_attr(key: str) -> str:
    """把 API/DB 使用的小写 key 映射到 Settings 的大写属性名。"""
    return key.upper()


def _apply_flag(key: str, value: bool) -> None:
    """把单个开关值写到运行期 settings。未登记的 key 静默跳过。"""
    attr = _settings_attr(key)
    if key in _CODE_DEFAULTS and hasattr(settings, attr):
        setattr(settings, attr, value)


def list_flags(session: Session) -> list[FlagView]:
    """返回所有登记开关的合并视图（代码默认值 + 数据库覆盖值）。"""
    rows = session.exec(select(FeatureFlag)).all()
    overrides = {row.key: row for row in rows}

    result: list[FlagView] = []
    for key, (default, description) in _CODE_DEFAULTS.items():
        override = overrides.get(key)
        result.append(
            FlagView(
                key=key,
                value=override.value if override is not None else default,
                default=default,
                description=description,
                overridden=override is not None,
                updated_at=override.updated_at if override is not None else None,
            )
        )
    return sorted(result, key=lambda item: item.key)


def get_flag(session: Session, key: str) -> FlagView | None:
    """返回单个开关的合并视图；未在代码默认值中登记时返回 None。"""
    key = key.strip().lower()
    if key not in _CODE_DEFAULTS:
        return None
    default, description = _CODE_DEFAULTS[key]
    row = session.get(FeatureFlag, key)
    return FlagView(
        key=key,
        value=row.value if row is not None else default,
        default=default,
        description=description,
        overridden=row is not None,
        updated_at=row.updated_at if row is not None else None,
    )


def get_flag_value(session: Session, key: str) -> bool:
    """返回单个开关的当前生效值；未登记时返回 False。"""
    view = get_flag(session, key)
    return view.value if view is not None else False


def apply_persisted_flags(session: Session) -> None:
    """把数据库中所有已持久化的覆盖值应用回运行期 settings。

    用于启动加载或刷新场景；未登记的 key 会被忽略。
    """
    for row in session.exec(select(FeatureFlag)).all():
        if row.key in _CODE_DEFAULTS:
            _apply_flag(row.key, row.value)


def set_flag(
    session: Session,
    key: str,
    value: bool,
    *,
    actor_id: str | None = None,
    actor_tenant_id: str | None = None,
) -> FlagView:
    """设置开关值；未登记的 key 会抛出 KeyError。

    数据库不存在时新建行，存在时更新；更新后同时写回 settings，使运行期行为
    立即生效。若提供了操作者信息，会在同一事务中写入审计日志；审计写入失败时
    整体回滚并抛出 RuntimeError。返回合并视图。
    """
    key = key.strip().lower()
    if key not in _CODE_DEFAULTS:
        raise KeyError(key)

    try:
        row = session.get(FeatureFlag, key)
        if row is None:
            default, description = _CODE_DEFAULTS[key]
            row = FeatureFlag(key=key, value=value, description=description)
            session.add(row)
        else:
            row.value = value

        if actor_id is not None:
            session.add(
                AuditLog(
                    action=AuditAction.FEATURE_FLAG_TOGGLE.value,
                    user_id=actor_id,
                    tenant_id=actor_tenant_id,
                    resource_type="feature_flag",
                    resource_id=key,
                    details={"value": value},
                )
            )
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.exception("feature_flag_update_failed key=%s value=%s", key, value)
        raise RuntimeError("功能开关变更未能写入审计") from exc

    session.refresh(row)
    _apply_flag(key, value)
    view = get_flag(session, key)
    if view is None:
        raise RuntimeError("功能开关写入后读取失败")
    return view
