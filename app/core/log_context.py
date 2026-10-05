"""日志请求上下文：在请求生命周期内携带 trace_id / user_id / tenant_id。

用 contextvars 实现，避免全局可变状态跨请求串扰；Formatter 从中读取，
请求中间件与认证依赖写入。
"""

from __future__ import annotations

import contextvars

_trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("log_trace_id", default="")
_user_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("log_user_id", default="")
_tenant_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("log_tenant_id", default="")


def get_trace_id() -> str:
    """当前请求的 trace_id；无请求上下文时为空串。"""
    return _trace_id_var.get()


def set_trace_id(value: str) -> None:
    """写入当前请求的 trace_id。"""
    _trace_id_var.set(value)


def get_user_id() -> str:
    """当前请求的 user_id；未认证或认证关闭时为空串。"""
    return _user_id_var.get()


def get_tenant_id() -> str:
    """当前请求的 tenant_id；未认证或认证关闭时为空串。"""
    return _tenant_id_var.get()


def set_user(user_id: str | None, tenant_id: str | None) -> None:
    """认证成功后写入当前请求的 user_id 与 tenant_id。"""
    _user_id_var.set(user_id or "")
    _tenant_id_var.set(tenant_id or "")


def clear() -> None:
    """请求结束时清空全部上下文，避免串扰下一请求。"""
    for var in (_trace_id_var, _user_id_var, _tenant_id_var):
        var.set("")
