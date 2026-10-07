"""把日志脱敏挂到进程的日志系统上。

根 logger 的过滤器不会作用到子 logger。因此安装时同时挂到已有 handler，
并在之后新增的 handler 上补同一层。重复安装不会叠两层。
"""

from __future__ import annotations

import logging
import traceback
from collections.abc import Mapping

from app.security.log_sanitizer import LogSanitizer

REDACTED_LOG_MESSAGE = "日志已省略"
_FILTER_MARK = "_redacts_log_secrets"
_RAG_API_EVENTS = {
    "未能删除本次新写的源文件": "rag_source_discard_failed",
    "保存源文件失败: filename=%s": "rag_source_save_failed",
    "上传文档解析成功，但知识库摄取失败: filename=%s": "rag_upload_ingest_failed",
    "创建上传导入任务时保存源文件失败": "rag_import_source_save_failed",
}
_PIPELINE_FAILURE_EVENTS = {
    "Agent 管线执行失败": "agent_pipeline_failed",
    "Agent 流式管线执行失败": "agent_pipeline_stream_failed",
    # 检索故障已在管线内收敛为终态，不是管线失败：事件名必须能区分，
    # 否则运维会把「知识库挂了」读成「整个 Agent 挂了」。
    "管线检索失败，收敛为 unavailable": "agent_pipeline_retrieval_unavailable",
}
_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE", "CONNECT"})

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
            _minimize_content_record(record)
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


def _minimize_content_record(record: logging.LogRecord) -> None:
    """正文可经上传、管线异常或HTTP URL回显，这些边界只保留固定元数据。"""
    if getattr(record, "_content_minimized", False):
        return
    info = record.exc_info
    has_exception = bool(info or record.exc_text)
    if record.name == "httpx":
        method = "unknown"
        status: int | str = "unknown"
        args = record.args
        if record.msg == 'HTTP Request: %s %s "%s %d %s"' and isinstance(args, tuple) and len(args) == 5:
            if isinstance(args[0], str) and args[0] in _HTTP_METHODS:
                method = args[0]
            code = args[3]
            if isinstance(code, int) and not isinstance(code, bool) and 100 <= code <= 599:
                status = code
            event = "http_request_completed"
        else:
            event = "http_client_event"
        record.msg = f"{event} method={method} status={status}"
    elif record.name == "app.api.routes.rag" or (record.name == "app.agents.pipeline" and has_exception):
        events = _RAG_API_EVENTS if record.name == "app.api.routes.rag" else _PIPELINE_FAILURE_EVENTS
        fallback = "rag_api_event" if record.name == "app.api.routes.rag" else "agent_pipeline_failed"
        event = events.get(record.msg, fallback) if isinstance(record.msg, str) else fallback
        error_type = info[0].__name__ if isinstance(info, tuple) and info[0] is not None else "unknown"
        record.msg = f"{event} error_type={error_type}"
    else:
        return
    record.args = ()
    record.exc_info = None
    record.exc_text = None
    record.stack_info = None
    record._content_minimized = True


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


def _sanitize_args(
    args: tuple[object, ...] | Mapping[str, object] | None, sanitizer: LogSanitizer
) -> tuple[object, ...] | Mapping[str, object] | None:
    if isinstance(args, tuple):
        return tuple(sanitizer.sanitize(item) if isinstance(item, str) else item for item in args)
    if isinstance(args, Mapping):
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
