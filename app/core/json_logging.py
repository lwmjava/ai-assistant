"""结构化 JSON 日志：JsonLogFormatter + setup_logging（幂等安装）。

标准错误保留一份，同时把同一条日志写入项目根目录下的滚动文件。
与 app/security/log_redaction.py 的脱敏 Filter 正交：Filter 在 emit 前
改写 record.msg，Formatter 在写出时输出 JSON，二者可共存、不冲突。
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.config import _PROJECT_ROOT, settings
from app.core.log_context import get_tenant_id, get_trace_id, get_user_id

_FORMATTER_MARK = "_json_log_formatter"
_STDERR_MARK = "_json_log_stderr"
_FILE_MARK = "_json_log_file"
_FILE_MAX_BYTES = 10 * 1024 * 1024
_FILE_BACKUP_COUNT = 5


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


def _log_file_path() -> Path:
    """配置为空时仍落到默认文件，避免进程只把日志留在标准错误。"""
    configured = (settings.LOG_FILE or "").strip() or "logs/app.log"
    path = Path(configured)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    return path.resolve()


def _ensure_stderr_handler(root: logging.Logger) -> None:
    """根 logger 自己持有标准错误输出。有了文件 handler 后，lastResort 不再代打。"""
    for handler in root.handlers:
        if getattr(handler, _STDERR_MARK, False):
            _apply_to_handler(handler)
            return
    handler = logging.StreamHandler(sys.stderr)
    setattr(handler, _STDERR_MARK, True)
    root.addHandler(handler)
    _apply_to_handler(handler)


def _ensure_file_handler(root: logging.Logger) -> None:
    """把 JSON 日志追加到滚动文件。同一路径重复安装不会再加一个 handler。"""
    path = _log_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    resolved = str(path)
    for handler in root.handlers:
        if getattr(handler, _FILE_MARK, None) == resolved:
            _apply_to_handler(handler)
            return
    handler = RotatingFileHandler(
        resolved,
        maxBytes=_FILE_MAX_BYTES,
        backupCount=_FILE_BACKUP_COUNT,
        encoding="utf-8",
    )
    setattr(handler, _FILE_MARK, resolved)
    root.addHandler(handler)
    _apply_to_handler(handler)


def setup_logging() -> None:
    """按 LOG_LEVEL 设根 logger 级别，并为各 handler 安装 JSON formatter。

    幂等：已安装的 handler 跳过；重复调用不会叠多层。
    标准错误和日志文件各保留一份相同内容。
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
    _ensure_stderr_handler(root)
    _ensure_file_handler(root)
