"""将 MCP 服务器工具适配为项目既有 ``Tool`` 抽象。

映射规则：
- 工具名加命名空间前缀 ``mcp__{server}__{tool}``，避免与内置工具或其它服务器的
  同名工具冲突；
- 描述附加 ``[MCP:{server}]`` 前缀，便于模型区分来源；
- ``func`` 为异步闭包，转发调用到对应 MCP 客户端（``call_tool``）。
"""

from __future__ import annotations

from app.agents.tools.base import Tool
from app.mcp.client import MCPClient, MCPServerTool


def mcp_tool_to_tool(client: MCPClient, spec: MCPServerTool) -> Tool:
    """将一个 MCP 工具包装为项目 ``Tool``。"""
    server = spec.server

    async def _invoke(arguments: dict) -> str:
        return await client.call_tool(spec.name, arguments)

    return Tool(
        name=f"mcp__{server}__{spec.name}",
        description=f"[MCP:{server}] {spec.description}".strip(),
        parameters=spec.input_schema or {"type": "object", "properties": {}},
        func=_invoke,
    )
