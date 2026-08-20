"""MCP 客户端：管理单个 MCP 服务器的连接、工具列举与调用。

传输层在 ``connect`` 时按 ``transport`` 字段惰性选择：
- ``stdio``：通过子进程与服务器通信（``mcp.client.stdio``）；
- ``http`` / ``streamable_http``：MCP streamable HTTP（``mcp.client.streamable_http``）；
- ``sse``：旧式 SSE 端点（``mcp.client.sse``）。

``mcp`` 为可选依赖：未安装时模块可导入，但 ``connect`` 会抛出
``MCPNotAvailableError`` 给出清晰指引。
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass

from app.mcp.config import MCPServerConfig

logger = logging.getLogger(__name__)


class MCPNotAvailableError(RuntimeError):
    """未安装 ``mcp`` 包时由 :func:`_require_mcp` 抛出。"""


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


@dataclass
class MCPServerTool:
    """MCP 服务器暴露的单个工具的轻量描述。"""

    server: str
    name: str
    description: str
    input_schema: dict


class MCPClient:
    """单个 MCP 服务器的客户端封装。"""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self._session = None
        self._stack: AsyncExitStack | None = None

    @classmethod
    def from_session(cls, name: str, session) -> "MCPClient":
        """基于已初始化的 ``ClientSession`` 构造（主要用于测试 / 内存传输）。"""
        obj = cls(MCPServerConfig(name=name, transport="stdio"))
        obj._session = session
        return obj

    @property
    def connected(self) -> bool:
        return self._session is not None

    async def connect(self) -> None:
        """按配置建立传输连接并初始化会话。"""
        if self._session is not None:
            return
        _require_mcp()
        from mcp import ClientSession
        from mcp.client.sse import sse_client
        from mcp.client.stdio import stdio_client
        # streamable-http 客户端在 mcp 1.x 名为 streamablehttp_client，2.x 改为
        # streamable_http_client；两者都尝试以兼容不同主版本。
        try:
            from mcp.client.streamable_http import streamable_http_client
        except ImportError:  # pragma: no cover - 旧版本回退
            from mcp.client.streamable_http import streamablehttp_client as streamable_http_client

        self._stack = AsyncExitStack()
        transport = (self.config.transport or "stdio").lower()

        if transport == "stdio":
            if not self.config.command:
                raise ValueError(f"MCP 服务器 {self.config.name} 使用 stdio 但未配置 command")
            from mcp import StdioServerParameters

            params = StdioServerParameters(
                command=self.config.command,
                args=self.config.args or [],
                env=self.config.env or None,
            )
            read, write = await self._stack.enter_async_context(stdio_client(params))
        elif transport in ("http", "streamable_http", "streamable-http"):
            url = self.config.url
            if not url:
                raise ValueError(f"MCP 服务器 {self.config.name} 使用 http 但未配置 url")
            read, write, _ = await self._stack.enter_async_context(
                streamablehttp_client(url, headers=self.config.headers or None)
            )
        elif transport == "sse":
            url = self.config.url
            if not url:
                raise ValueError(f"MCP 服务器 {self.config.name} 使用 sse 但未配置 url")
            read, write = await self._stack.enter_async_context(
                sse_client(url, headers=self.config.headers or None)
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
        result = await self._session.list_tools()
        tools: list[MCPServerTool] = []
        for t in getattr(result, "tools", []) or []:
            tools.append(
                MCPServerTool(
                    server=self.config.name,
                    name=getattr(t, "name", ""),
                    description=getattr(t, "description", "") or "",
                    input_schema=getattr(t, "inputSchema", None) or {},
                )
            )
        return tools

    async def call_tool(self, name: str, arguments: dict | None = None) -> str:
        """调用工具并返回可读结果文本。

        当服务器返回 ``isError`` 时，结果以 ``[MCP 工具错误]`` 前缀标记，
        便于模型识别失败而非将其当作正常观测。
        """
        if self._session is None:
            raise RuntimeError("MCP 客户端尚未连接，请先调用 connect()。")
        result = await self._session.call_tool(name, arguments or {})
        is_error = getattr(result, "isError", False)
        text = _render_content(getattr(result, "content", []))
        if is_error:
            return f"[MCP 工具错误] {name}：{text}"
        return text
