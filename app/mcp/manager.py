"""MCP 工具管理器：聚合多个 MCP 服务器的工具到 Agent 工具箱。

对外提供进程级单例 :func:`get_mcp_manager`：首次调用时按配置连接所有启用的
服务器（带容错，单个失败不影响其它），之后复用连接。未启用 MCP 或没有任何可用
服务器时返回 ``None``，调用方据此回退到仅内置工具。
"""

from __future__ import annotations

import logging

from app.agents.tools.base import Tool, ToolRegistry
from app.core.config import settings
from app.mcp.adapter import mcp_tool_to_tool
from app.mcp.client import MCPClient
from app.mcp.config import list_configured_servers

logger = logging.getLogger(__name__)


class MCPToolManager:
    """管理一组已连接的 MCP 客户端及其工具聚合。"""

    def __init__(self) -> None:
        self._clients: dict[str, MCPClient] = {}

    @property
    def connected_servers(self) -> list[str]:
        return [name for name, client in self._clients.items() if client.connected]

    async def connect_all(self, configs=None) -> None:
        """连接所有启用的服务器（单个失败仅告警，不中断）。"""
        configs = list_configured_servers() if configs is None else configs
        for cfg in configs:
            if not cfg.enabled:
                continue
            try:
                client = MCPClient(cfg)
                await client.connect()
                self._clients[cfg.name] = client
            except Exception as exc:  # noqa: BLE001 — 单点故障不应阻断整体
                logger.warning("MCP 服务器连接失败 %s：%s", cfg.name, exc)

    async def disconnect_all(self) -> None:
        for name, client in list(self._clients.items()):
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                logger.exception("断开 MCP 服务器异常：%s", name)
        self._clients.clear()

    async def collect_tools(self) -> list[Tool]:
        """聚合所有已连接服务器暴露的工具为项目 ``Tool`` 列表。"""
        tools: list[Tool] = []
        for name, client in self._clients.items():
            try:
                specs = await client.list_tools()
            except Exception as exc:  # noqa: BLE001
                logger.warning("列举 MCP 工具失败 %s：%s", name, exc)
                continue
            for spec in specs:
                tools.append(mcp_tool_to_tool(client, spec))
        return tools

    def get_client(self, server: str) -> MCPClient | None:
        return self._clients.get(server)


_manager: "MCPToolManager | None" = None


async def get_mcp_manager() -> "MCPToolManager | None":
    """返回进程级 MCP 管理器单例；未启用或无可连接服务器时返回 None。"""
    global _manager
    if not settings.MCP_ENABLED:
        return None
    if _manager is None:
        configs = [c for c in list_configured_servers() if c.enabled]
        if not configs:
            logger.info("MCP 已启用但未配置任何服务器，仅使用内置工具。")
            return None
        mgr = MCPToolManager()
        await mgr.connect_all(configs)
        if not mgr.connected_servers:
            logger.warning("MCP 已启用但所有服务器均连接失败，仅使用内置工具。")
            _manager = None
            return None
        _manager = mgr
    return _manager


def reset_mcp_manager() -> None:
    """重置单例（主要用于测试隔离）。"""
    global _manager
    _manager = None
