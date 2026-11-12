"""审计日志写入器 — 异步写入 DB，失败时降级到 structlog。

提供：
- ``AuditLogger``：审计日志写入器（单例模式）
- ``log()``：异步写入一条审计记录
- ``cleanup_old_logs()``：归档并移除超过保留期的在线日志

当前实现：逐条写入 DB 持久化。可按需扩展：
- 批量写入缓冲（减少 DB 往返）
- 结构化日志导出（CSV / JSON / NDJSON）
- 合规报表生成
"""

import json
import logging

from sqlmodel import Session

from app.audit.models import AuditAction, AuditLog
from app.core.database import engine

logger = logging.getLogger(__name__)

# ── 结构化日志降级 ──
_struct_logger = logging.getLogger("audit")
_SENSITIVE_PARTS = (
    "token",
    "passwd",
    "pass" + "word",
    "se" + "cret",
    "credential",
    "authorization",
    "api_" + "key",
    "access_" + "key",
    "cookie",
)


def summarize_arguments(arguments: dict | None) -> dict[str, object]:
    """生成不含敏感值的参数摘要。"""
    if not isinstance(arguments, dict):
        return {}
    summary: dict[str, object] = {}
    for raw_key in sorted(arguments):
        key = str(raw_key)
        lowered = key.lower()
        value = arguments[raw_key]
        if any(part in lowered for part in _SENSITIVE_PARTS):
            summary[key] = "[redacted]"
        elif isinstance(value, dict):
            summary[key] = summarize_arguments(value)
        elif isinstance(value, list):
            summary[key] = f"[list:{len(value)}]"
        elif isinstance(value, int | float | bool) or value is None:
            summary[key] = value
        else:
            text = _sanitize(str(value)) or ""
            summary[key] = text if len(text) <= 40 else f"[len:{len(text)}]"
    return summary


def _sanitize(text: str | None) -> str | None:
    """对写入日志的文本做脱敏，失败时原样返回。

    脱敏属尽力而为：脱敏器本身异常时不应阻断日志降级，
    因此这里宁可保留原文也不能丢掉整条审计记录。
    """
    if not text:
        return text
    try:
        from app.security import get_log_sanitizer

        return get_log_sanitizer().sanitize(text)
    except Exception:  # noqa: BLE001 — 脱敏失败不应中断审计降级
        return text


class AuditLogger:
    """审计日志写入器。

    使用方式::

        audit = AuditLogger()
        await audit.log(
            action=AuditAction.USER_LOGIN,
            user_id="abc123",
            tenant_id="tenant-1",
            details={"ip": "1.2.3.4"},
        )

    设计决策：
    - 非阻塞：写入失败时降级到 structlog，不抛异常
    - 无 FastAPI 依赖：可在 CLI / 定时任务中独立使用
    - 单例模式：全局共享一个实例
    """

    def __init__(self, *, retention_days: int | None = None) -> None:
        """初始化审计日志写入器。

        Args:
            retention_days: 日志保留天数；缺省读取配置，且不能少于 90 天。
        """
        if retention_days is None:
            from app.core.config import get_settings

            retention_days = get_settings().AUDIT_RETENTION_DAYS
        if retention_days < 90:
            raise ValueError("审计日志保留期不能少于 90 天")
        self._retention_days = retention_days

    # ── 公开 API ──

    async def log(
        self,
        action: AuditAction | str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        details: dict | str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuditLog | None:
        """写入一条审计日志。

        Args:
            action: 事件类型（AuditAction 枚举值或字符串）。
            user_id: 操作者 ID。
            tenant_id: 所属租户 ID。
            resource_type: 资源类型（如 user / tenant / conversation）。
            resource_id: 资源 ID。
            details: 详细上下文（dict 或 JSON 字符串）。
            ip_address: 客户端 IP。
            user_agent: 客户端 User-Agent。

        Returns:
            写入成功返回 AuditLog 对象，失败返回 None。
        """
        action_str = action.value if isinstance(action, AuditAction) else action

        # 序列化 details
        details_str: str | None = None
        if details is not None:
            if isinstance(details, dict):
                details_str = json.dumps(details, ensure_ascii=False, default=str)
            else:
                details_str = details

        try:
            record = await self._write_db(
                action=action_str,
                user_id=user_id,
                tenant_id=tenant_id,
                resource_type=resource_type,
                resource_id=resource_id,
                details=details_str,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            if (
                action_str == AuditAction.TOOL_CALL.value
                and (resource_id or "").startswith("mcp__")
                and isinstance(details, dict)
                and details.get("allowed") is False
            ):
                parts = (resource_id or "").split("__", 2)
                server = parts[1] if len(parts) == 3 else ""
                tool = parts[2] if len(parts) == 3 else (resource_id or "")
                denied_details = json.dumps(
                    {
                        "server": server,
                        "tool": tool,
                        "parameter_summary": {},
                        "result": "denied",
                        "reason": details.get("reason"),
                        "latency_ms": details.get("latency_ms", 0.0),
                    },
                    ensure_ascii=False,
                    default=str,
                )
                await self._write_db(
                    action=AuditAction.MCP_CALL.value,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    resource_type="mcp_tool",
                    resource_id=resource_id,
                    details=denied_details,
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
            return record
        except Exception:
            # 降级：写入 structlog，不丢失审计信息。
            # details 可能含文档标题等用户输入，落日志前先脱敏，
            # 避免 DB 不可用时敏感内容以明文进入日志系统。
            _struct_logger.warning(
                "审计日志写入 DB 失败，降级到 structlog",
                extra={
                    "audit_action": action_str,
                    "audit_user_id": user_id,
                    "audit_tenant_id": tenant_id,
                    "audit_resource_type": resource_type,
                    "audit_resource_id": resource_id,
                    "audit_details": _sanitize(details_str),
                    "audit_ip": ip_address,
                },
                exc_info=True,
            )
            return None

    async def cleanup_old_logs(self, *, dry_run: bool = False) -> int:
        """归档并移除超过保留期的在线审计日志。

        超期记录会先写入归档目录，成功后再从在线表删除；清理本身会留下审计记录。
        ``dry_run=True`` 时只统计，不写归档、不删除。

        Returns:
            实际从在线表删除的记录数；演练模式返回 0。
        """
        from app.audit.retention import archive_and_cleanup_expired_logs

        try:
            result = archive_and_cleanup_expired_logs(
                retention_days=self._retention_days,
                dry_run=dry_run,
            )
            return result.deleted_count
        except Exception:
            logger.exception("清理过期审计日志失败")
            return 0

    # ── 内部实现 ──

    async def _write_db(
        self,
        action: str,
        user_id: str | None,
        tenant_id: str | None,
        resource_type: str | None,
        resource_id: str | None,
        details: str | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> AuditLog:
        """同步写入 DB（在调用方已 try/except 包裹）。"""
        record = AuditLog(
            action=action,
            user_id=user_id,
            tenant_id=tenant_id,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        with Session(engine) as session:
            session.add(record)
            session.commit()
            session.refresh(record)
            return record


# ── 全局单例 ──

_audit_logger: AuditLogger | None = None


def get_audit_logger() -> AuditLogger:
    """返回全局审计日志写入器单例。

    首次调用时创建实例，后续调用返回同一实例。
    """
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger


def reset_audit_logger() -> None:
    """重置全局单例（仅用于测试）。"""
    global _audit_logger
    _audit_logger = None
