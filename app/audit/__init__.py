"""审计日志模块。

提供可审计事件的全生命周期管理：
- 事件类型定义（``AuditAction`` 枚举）
- 审计日志写入（``AuditLogger``，DB 持久化 + structlog 降级）
- 日志保留与归档清理（``archive_and_cleanup_expired_logs()`` / ``cleanup_old_logs()``）
- Admin API 查询（``routes/audit.py``）

对外暴露：
- ``AuditAction``：审计事件类型枚举
- ``AuditLog``：审计日志 SQLModel
- ``AuditLogger``：审计日志写入器
- ``get_audit_logger`` / ``reset_audit_logger``：全局单例
"""

from app.audit.logger import AuditLogger, get_audit_logger, reset_audit_logger, summarize_arguments
from app.audit.models import AuditAction, AuditLog
from app.audit.retention import AuditRetentionResult, archive_and_cleanup_expired_logs

__all__ = [
    "AuditAction",
    "AuditLog",
    "AuditLogger",
    "get_audit_logger",
    "reset_audit_logger",
    "summarize_arguments",
    "AuditRetentionResult",
    "archive_and_cleanup_expired_logs",
]
