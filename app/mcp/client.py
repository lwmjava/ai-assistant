"""MCP 客户端：管理单个 MCP 服务器的连接、工具列举与调用。

传输层在 ``connect`` 时按 ``transport`` 字段惰性选择：
- ``stdio``：通过子进程与服务器通信（``mcp.client.stdio``）；
- ``http`` / ``streamable_http``：MCP streamable HTTP（``mcp.client.streamable_http``）；
- ``sse``：旧式 SSE 端点（``mcp.client.sse``）。

``mcp`` 为可选依赖：未安装时模块可导入，但 ``connect`` 会抛出
``MCPNotAvailableError`` 给出清晰指引。
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

from app.audit import AuditAction, get_audit_logger, summarize_arguments
from app.core import log_context
from app.core.config import settings
from app.mcp.config import MCPServerConfig

logger = logging.getLogger(__name__)


class MCPNotAvailableError(RuntimeError):
    """未安装 ``mcp`` 包时由 :func:`_require_mcp` 抛出。"""


def _positive_timeout(value: Any) -> float | None:
    """只接受正数超时；工具声明不能把等待时间拉过配置上限。"""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    if number <= 0:
        return None
    return number


def _declared_field(tool: Any, key: str) -> Any:
    meta = getattr(tool, "meta", None)
    if isinstance(meta, dict) and key in meta:
        return meta.get(key)
    annotations = getattr(tool, "annotations", None)
    if isinstance(annotations, dict) and key in annotations:
        return annotations.get(key)
    return getattr(annotations, key, None)


def _declared_tool_timeout(tool: Any) -> float | None:
    return _positive_timeout(_declared_field(tool, "timeout_seconds"))


def _declared_tool_risk(tool: Any, fallback: str | None) -> str | None:
    for item in (_declared_field(tool, "risk"), fallback):
        if isinstance(item, str) and item.strip():
            return item.strip().upper()
    return None


def _declared_tool_roles(tool: Any, fallback: list[str] | None) -> list[str] | None:
    raw = _declared_field(tool, "allowed_roles")
    if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        return list(raw)
    if fallback is not None:
        return list(fallback)
    return None


def _is_disconnect(exc: BaseException) -> bool:
    if isinstance(exc, ConnectionError | BrokenPipeError | EOFError):
        return True
    name = type(exc).__name__
    return "Closed" in name or "Connection" in name or "Disconnect" in name


def _require_mcp():
    """确保 ``mcp`` 已安装，否则抛出可读错误。"""
    try:
        import mcp  # noqa: F401
    except ImportError as exc:  # pragma: no cover - 依赖缺失提示
        raise MCPNotAvailableError(
            "未安装 mcp 包，无法连接 MCP 服务器。请执行 "
            "`pip install \"mcp>=1.2.0\"` 并在启用 MCP_ENABLED 前确认依赖就绪。"
        ) from exc
    return mcp


def _render_content(content) -> str:
    """将 MCP 返回的 content 列表拼接为可读文本（优先文本块）。"""
    parts: list[str] = []
    for item in content or []:
        ctype = getattr(item, "type", None)
        if ctype == "text":
            text = getattr(item, "text", "") or ""
            if text:
                parts.append(text)
        else:
            parts.append(f"[{ctype or 'content'}]")
    return "\n".join(parts).strip()


async def record_mcp_audit(
    server: str,
    tool: str,
    arguments: dict | None,
    result: str,
    latency_ms: float,
) -> None:
    """记录一次 MCP 工具调用审计；审计异常不阻断调用结果。"""
    try:
        await get_audit_logger().log(
            action=AuditAction.MCP_CALL,
            user_id=log_context.get_user_id(),
            tenant_id=log_context.get_tenant_id(),
            resource_type="mcp_tool",
            resource_id=f"mcp__{server}__{tool}",
            details={
                "server": server,
                "tool": tool,
                "parameter_summary": summarize_arguments(arguments),
                "result": result,
                "latency_ms": round(latency_ms, 2),
            },
        )
    except Exception:  # noqa: BLE001 — 审计故障不中断工具调用
        logger.exception("MCP 调用审计写入失败：server=%s tool=%s", server, tool)


@dataclass
class MCPServerTool:
    """MCP 服务器暴露的单个工具的轻量描述。"""

    server: str
    name: str
    description: str
    input_schema: dict
    timeout_seconds: float | None = None
    risk: str | None = "L0"
    allowed_roles: list[str] | None = None
    authenticated: bool = False
    tenant_scoped: bool = False


class MCPClient:
    """单个 MCP 服务器的客户端封装。"""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self._session: Any = None
        self._stack: AsyncExitStack | None = None

    @classmethod
    def from_session(cls, name: str, session) -> MCPClient:
        """基于已初始化的 ``ClientSession`` 构造（主要用于测试 / 内存传输）。"""
        obj = cls(MCPServerConfig(name=name, transport="stdio"))
        obj._session = session
        return obj

    @property
    def connected(self) -> bool:
        return self._session is not None

    def _call_timeout(self, declared: float | None = None) -> float:
        ceiling = float(settings.MCP_CALL_TIMEOUT_SECONDS)
        chosen = ceiling
        server_limit = _positive_timeout(self.config.timeout_seconds)
        if server_limit is not None:
            chosen = min(chosen, server_limit)
        declared_limit = _positive_timeout(declared)
        if declared_limit is not None:
            chosen = min(chosen, declared_limit)
        return chosen

    def _connect_timeout(self) -> float:
        return float(settings.MCP_CONNECT_TIMEOUT_SECONDS)

    async def connect(self) -> None:
        """按配置建立传输连接并初始化会话。超时后释放已打开的传输。"""
        if self._session is not None:
            return
        try:
            async with asyncio.timeout(self._connect_timeout()):
                await self._open_session()
        except TimeoutError:
            await self.disconnect()
            raise TimeoutError(f"MCP 服务器 {self.config.name} 连接超时") from None

    async def _open_session(self) -> None:
        """按配置建立传输连接并初始化会话。"""
        if self._session is not None:
            return
        _require_mcp()
        from mcp import ClientSession
        from mcp.client import streamable_http
        from mcp.client.sse import sse_client
        from mcp.client.stdio import stdio_client

        # streamable-http 客户端在 mcp 1.x 名为 streamablehttp_client，2.x 改为
        # streamable_http_client；两者都尝试以兼容不同主版本。
        streamable_http_client = getattr(streamable_http, "streamable_http_client", None)
        if streamable_http_client is None:
            streamable_http_client = getattr(streamable_http, "streamablehttp_client")

        self._stack = AsyncExitStack()
        transport = (self.config.transport or "stdio").lower()

        if transport == "stdio":
            if not self.config.command:
                raise ValueError(f"MCP 服务器 {self.config.name} 使用 stdio 但未配置 command")
            from mcp import StdioServerParameters

            params = StdioServerParameters(
                command=self.config.command,
                args=self.config.args or [],
                env=self.config.resolved_env(),
            )
            read, write = await self._stack.enter_async_context(stdio_client(params))
        elif transport in ("http", "streamable_http", "streamable-http"):
            url = self.config.url
            if not url:
                raise ValueError(f"MCP 服务器 {self.config.name} 使用 http 但未配置 url")
            read, write, _ = await self._stack.enter_async_context(
                streamable_http_client(url, headers=self.config.resolved_headers())
            )
        elif transport == "sse":
            url = self.config.url
            if not url:
                raise ValueError(f"MCP 服务器 {self.config.name} 使用 sse 但未配置 url")
            read, write = await self._stack.enter_async_context(
                sse_client(url, headers=self.config.resolved_headers())
            )
        else:
            raise ValueError(f"不支持的 MCP 传输类型：{transport}")

        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        logger.info("MCP 服务器已连接：%s（%s）", self.config.name, transport)

    async def disconnect(self) -> None:
        """关闭会话与传输（若存在）。"""
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
        self._session = None

    async def list_tools(self) -> list[MCPServerTool]:
        """列举服务器暴露的工具。"""
        if self._session is None:
            raise RuntimeError("MCP 客户端尚未连接，请先调用 connect()。")
        try:
            async with asyncio.timeout(self._call_timeout()):
                result = await self._session.list_tools()
        except TimeoutError:
            await self.disconnect()
            raise TimeoutError(f"MCP 服务器 {self.config.name} 列举工具超时") from None
        tools: list[MCPServerTool] = []
        for t in getattr(result, "tools", []) or []:
            tools.append(
                MCPServerTool(
                    server=self.config.name,
                    name=getattr(t, "name", ""),
                    description=getattr(t, "description", "") or "",
                    input_schema=getattr(t, "inputSchema", None) or {},
                    timeout_seconds=_declared_tool_timeout(t),
                    risk=_declared_tool_risk(t, self.config.default_risk),
                    allowed_roles=_declared_tool_roles(t, self.config.allowed_roles),
                    authenticated=bool(self.config.authenticated),
                    tenant_scoped=bool(self.config.tenant_scoped),
                )
            )
        return tools

    async def heartbeat(self) -> bool:
        """探测会话是否仍可用。失败时只重连这一台服务器。"""
        try:
            if self._session is None:
                await self.connect()
                return self.connected
            async with asyncio.timeout(self._connect_timeout()):
                await self._ping()
            return True
        except Exception:
            logger.warning("MCP 服务器心跳失败 %s：%s", self.config.name, "unavailable")
            await self.disconnect()
            try:
                await self.connect()
                return self.connected
            except Exception:
                logger.warning("MCP 服务器心跳后重连失败 %s", self.config.name)
                return False

    async def _ping(self) -> None:
        if self._session is None:
            raise ConnectionError(f"MCP 服务器 {self.config.name} 未连接")
        ping = getattr(self._session, "send_ping", None)
        if ping is not None:
            await ping()
            return
        await self._session.list_tools()

    async def call_tool(
        self,
        name: str,
        arguments: dict | None = None,
        timeout_seconds: float | None = None,
    ) -> str:
        """调用工具并返回可读结果文本。

        当服务器返回 ``isError`` 时，结果以 ``[MCP 工具错误]`` 前缀标记，
        便于模型识别失败而非将其当作正常观测。
        """
        if self._session is None:
            raise RuntimeError("MCP 客户端尚未连接，请先调用 connect()。")
        timeout = self._call_timeout(timeout_seconds)
        started = time.monotonic()
        attempts = 0
        outcome = "failure"
        try:
            while True:
                attempts += 1
                if self._session is None:
                    await self.connect()
                try:
                    async with asyncio.timeout(timeout):
                        result = await self._session.call_tool(name, arguments or {})
                except TimeoutError:
                    outcome = "timeout"
                    await self.disconnect()
                    raise TimeoutError(f"MCP 工具 {name} 调用超时") from None
                except Exception as exc:
                    if attempts > int(settings.MCP_RECONNECT_ATTEMPTS) or not _is_disconnect(exc):
                        raise
                    await self.disconnect()
                    continue
                is_error = getattr(result, "isError", False)
                text = _render_content(getattr(result, "content", []))
                if is_error:
                    return f"[MCP 工具错误] {name}：{text}"
                outcome = "success"
                return text
        finally:
            await record_mcp_audit(
                self.config.name,
                name,
                arguments,
                outcome,
                (time.monotonic() - started) * 1000,
            )
