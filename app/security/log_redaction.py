"""把日志脱敏挂到进程的日志系统上。

根 logger 的过滤器不会作用到子 logger。因此安装时同时挂到已有 handler，
并在之后新增的 handler 上补同一层。重复安装不会叠两层。
"""

from __future__ import annotations

import logging
import traceback

from app.security.log_sanitizer import LogSanitizer

REDACTED_LOG_MESSAGE = "日志已省略"
_FILTER_MARK = "_redacts_log_secrets"

_shared_filter: RedactingLogFilter | None = None
_add_handler_patched = False


class RedactingLogFilter(logging.Filter):
    """格式化消息和回溯后再脱敏，并清空 args，避免再次插值。"""

    def __init__(self) -> None:
        super().__init__()
        setattr(self, _FILTER_MARK, True)

    def filter(self, record: logging.LogRecord) -> bool:
        access = _is_uvicorn_access(record)
        try:
            sanitizer = LogSanitizer()
            if access:
                # 访问日志的格式化器要拆开这五个参数，不能改成空元组。
                record.args = _sanitize_args(record.args, sanitizer)
            else:
                record.msg = sanitizer.sanitize(record.getMessage())
                record.args = ()
            exc_text = _formatted_exception(record)
            record.exc_text = sanitizer.sanitize(exc_text) if exc_text else None
            record.exc_info = None
            if isinstance(record.stack_info, str):
                record.stack_info = sanitizer.sanitize(record.stack_info)
        except Exception:
            if access:
                record.args = ("-", "-", "-", "-", 0)
            else:
                record.msg = REDACTED_LOG_MESSAGE
                record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


def install_log_redaction() -> None:
    """把脱敏过滤器挂到根 logger 和各个 handler 上。重复调用不叠两层。"""
    global _add_handler_patched
    filt = _filter()
    _attach_existing(filt)
    if _add_handler_patched:
        return

    original = logging.Logger.addHandler

    def add_handler(self: logging.Logger, hdlr: logging.Handler) -> None:
        original(self, hdlr)
        _ensure_filter(hdlr, filt)

    logging.Logger.addHandler = add_handler  # type: ignore[method-assign]
    _add_handler_patched = True


def _filter() -> RedactingLogFilter:
    global _shared_filter
    if _shared_filter is None:
        _shared_filter = RedactingLogFilter()
    return _shared_filter


def _attach_existing(filt: RedactingLogFilter) -> None:
    root = logging.getLogger()
    _ensure_filter(root, filt)
    for handler in list(root.handlers):
        _ensure_filter(handler, filt)
    if logging.lastResort is not None:
        _ensure_filter(logging.lastResort, filt)
    for obj in list(logging.Logger.manager.loggerDict.values()):
        if not isinstance(obj, logging.Logger):
            continue
        _ensure_filter(obj, filt)
        for handler in list(obj.handlers):
            _ensure_filter(handler, filt)


def _ensure_filter(target: logging.Filterer, filt: RedactingLogFilter) -> None:
    if any(getattr(item, _FILTER_MARK, False) for item in target.filters):
        return
    target.addFilter(filt)


def _is_uvicorn_access(record: logging.LogRecord) -> bool:
    args = record.args
    return record.name == "uvicorn.access" and isinstance(args, tuple) and len(args) == 5


def _sanitize_args(args: tuple[object, ...] | dict[object, object] | None, sanitizer: LogSanitizer) -> object:
    if isinstance(args, tuple):
        return tuple(sanitizer.sanitize(item) if isinstance(item, str) else item for item in args)
    if isinstance(args, dict):
        return {key: sanitizer.sanitize(value) if isinstance(value, str) else value for key, value in args.items()}
    return args


def _formatted_exception(record: logging.LogRecord) -> str:
    """Filter 执行时回溯还在 exc_info 里，exc_text 通常仍是空的。"""
    if record.exc_text:
        return record.exc_text
    info = record.exc_info
    if not isinstance(info, tuple) or info[0] is None:
        return ""
    return "".join(traceback.format_exception(*info))
