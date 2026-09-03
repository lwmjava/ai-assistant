"""审计日志写入器 — 异步写入 DB，失败时降级到 structlog。

提供：
- ``AuditLogger``：审计日志写入器（单例模式）
- ``log()``：异步写入一条审计记录
- ``cleanup_old_logs()``：清理超过保留期的日志

当前实现：逐条写入 DB 持久化。可按需扩展：
- 批量写入缓冲（减少 DB 往返）
- 结构化日志导出（CSV / JSON / NDJSON）
- 合规报表生成
"""

import json
import logging
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, delete, select

from app.audit.models import AuditAction, AuditLog
from app.core.database import engine

logger = logging.getLogger(__name__)

# ── 结构化日志降级 ──
_struct_logger = logging.getLogger("audit")


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

    def __init__(self, *, retention_days: int = 90) -> None:
        """初始化审计日志写入器。

        Args:
            retention_days: 日志保留天数（默认 90 天，符合 PRD 要求）。
        """
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

    async def cleanup_old_logs(self) -> int:
        """清理超过保留期的审计日志。

        Returns:
            删除的记录数。
        """
        cutoff = datetime.now(UTC) - timedelta(days=self._retention_days)
        try:
            with Session(engine) as session:
                stmt = delete(AuditLog).where(AuditLog.created_at < cutoff)
                result = session.exec(stmt)  # type: ignore[arg-type]
                session.commit()
                deleted = getattr(result, "rowcount", 0) or 0
                if deleted > 0:
                    logger.info("已清理 %d 条过期审计日志（保留期 %d 天）", deleted, self._retention_days)
                return deleted
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