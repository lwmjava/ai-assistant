"""测试用 MCP stdio 服务器：暴露一个 ``echo`` 工具。

由 ``tests/test_mcp.py`` 以子进程方式启动（``transport=stdio``），
用于验证真实的 stdio 传输链路（连接 / 列举 / 调用）。
仅依赖 ``mcp``，不依赖项目代码。

适配 mcp 2.x 的低级 ``Server`` API：通过 ``add_request_handler`` 注册
``tools/list`` 与 ``tools/call`` 处理器，并以对应的 Params 类型校验入参、
返回对应的 Result 类型。
"""

import asyncio

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
)

server = Server("echo-fixture")


async def handle_list_tools(_ctx, _request: PaginatedRequestParams) -> ListToolsResult:
    return ListToolsResult(
        tools=[
            Tool(
                name="echo",
                description="回显传入的消息，用于测试。",
                inputSchema={
                    "type": "object",
                    "properties": {"msg": {"type": "string", "description": "要回显的内容"}},
                    "required": ["msg"],
                },
            )
        ]
    )


async def handle_call_tool(_ctx, request: CallToolRequestParams) -> CallToolResult:
    name = request.name
    arguments = request.arguments or {}
    if name != "echo":
        raise ValueError(f"未知工具：{name}")
    return CallToolResult(
        content=[TextContent(type="text", text=f"echo:{arguments.get('msg')}")],
        is_error=False,
    )


server.add_request_handler("tools/list", PaginatedRequestParams, handle_list_tools)
server.add_request_handler("tools/call", CallToolRequestParams, handle_call_tool)


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
