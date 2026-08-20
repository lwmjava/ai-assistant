"""MCP 客户端层：将企业系统的 MCP 服务器工具接入 Agent 工具箱。

设计要点：
- 基于官方 ``mcp`` SDK；``mcp`` 为**可选依赖**——未安装时本包可正常导入，
  仅在真正连接 MCP 服务器时才给出清晰错误（与 LLM 的 Mock 降级思路一致）；
- 支持 ``stdio`` / ``http``(streamable-http) / ``sse`` 三种传输；
- MCP 服务器暴露的工具被映射为项目既有 ``Tool`` 抽象，命名空间为
  ``mcp__{server}__{tool}``，Agent 在「行动」阶段即可直接调用企业系统能力，
  实现 README 中宣称的「AI ↔ 企业系统」打通。
"""

from app.mcp.adapter import mcp_tool_to_tool
from app.mcp.client import MCPClient, MCPNotAvailableError, MCPServerTool
from app.mcp.config import MCPServerConfig, list_configured_servers
from app.mcp.manager import MCPToolManager, get_mcp_manager

__all__ = [
    "MCPServerConfig",
    "list_configured_servers",
    "MCPClient",
    "MCPServerTool",
    "MCPNotAvailableError",
    "mcp_tool_to_tool",
    "MCPToolManager",
    "get_mcp_manager",
]
