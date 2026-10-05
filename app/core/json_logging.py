"""结构化 JSON 日志：JsonLogFormatter + setup_logging（幂等安装）。

与 app/security/log_redaction.py 的脱敏 Filter 正交：Filter 在 emit 前
改写 record.msg，Formatter 在写盘时输出 JSON，二者可共存、不冲突。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from app.core.config import settings
from app.core.log_context import get_tenant_id, get_trace_id, get_user_id

_FORMATTER_MARK = "_json_log_formatter"


class JsonLogFormatter(logging.Formatter):
    """把单条日志输出为一行合法 JSON，含 ts/level/logger/message 与三字段上下文。"""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": get_trace_id(),
            "user_id": get_user_id(),
            "tenant_id": get_tenant_id(),
        }
        return json.dumps(payload, ensure_ascii=False, default=str)


def _apply_to_handler(handler: logging.Handler) -> None:
    """给单个 handler 换上 JsonLogFormatter；已装过则跳过（幂等）。"""
    if getattr(handler, _FORMATTER_MARK, False):
        return
    handler.setFormatter(JsonLogFormatter())
    setattr(handler, _FORMATTER_MARK, True)


def setup_logging() -> None:
    """按 LOG_LEVEL 设根 logger 级别，并为各 handler 安装 JSON formatter。

    幂等：已安装的 handler 跳过；重复调用不会叠多层。
    """
    root = logging.getLogger()
    root.setLevel(settings.LOG_LEVEL)
    _handlers: list[logging.Handler] = []
    for obj in [root] + list(logging.Logger.manager.loggerDict.values()):
        if not isinstance(obj, logging.Logger):
            continue
        _handlers.extend(obj.handlers)
    if logging.lastResort is not None:
        _handlers.append(logging.lastResort)
    for handler in _handlers:
        _apply_to_handler(handler)
