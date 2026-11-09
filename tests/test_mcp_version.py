"""MCP 工具版本与兼容性声明：版本不匹配时返回明确错误，不做自动迁移。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agents.tools.base import ToolCall, ToolRegistry
from app.agents.tools.executor import ToolExecutor
from app.mcp import (
    MCPClient,
    MCPServerConfig,
    MCPServerTool,
    MCPToolManager,
    MCPVersionMismatchError,
    mcp_tool_to_tool,
)


class _ClientStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(
        self, name: str, arguments: dict, timeout_seconds: float | None = None
    ) -> str:
        self.calls.append((name, arguments))
        return "ok"


@pytest.mark.asyncio
async def test_matching_declared_version_populates_contract_and_executes() -> None:
    remote = SimpleNamespace(
        name="get_order",
        description="query order",
        inputSchema={"type": "object", "properties": {"id": {"type": "integer"}}},
        meta={"version": "1.2.0"},
    )

    class SessionStub:
        async def list_tools(self):
            return SimpleNamespace(tools=[remote])

        async def call_tool(self, name: str, arguments: dict):
            return SimpleNamespace(
                isError=False,
                content=[SimpleNamespace(type="text", text=f"order:{arguments.get('id')}")],
            )

    client = MCPClient(
        MCPServerConfig(
            name="crm",
            transport="stdio",
            command="python",
            expected_version="1.x",
        )
    )
    client._session = SessionStub()

    specs = await client.list_tools()
    assert len(specs) == 1
    assert specs[0].version == "1.2.0"

    tool = mcp_tool_to_tool(client, specs[0])
    assert tool.contract is not None
    assert tool.contract.version == "1.2.0"
    out = await tool.execute({"id": 7})
    assert out == "order:7"


@pytest.mark.asyncio
async def test_mismatched_server_tool_version_raises_explicit_error() -> None:
    remote = SimpleNamespace(
        name="get_order",
        description="query order v2",
        inputSchema={"type": "object"},
        meta={"version": "2.0.0"},
    )

    class MismatchedServerSession:
        async def list_tools(self):
            return SimpleNamespace(tools=[remote])

    client = MCPClient(
        MCPServerConfig(
            name="crm",
            transport="stdio",
            command="python",
            tool_versions={"get_order": "1.0.0"},
        )
    )
    client._session = MismatchedServerSession()

    with pytest.raises(MCPVersionMismatchError, match="版本不兼容.*2.0.0.*1.0.0"):
        await client.list_tools()

    mgr = MCPToolManager()
    mgr._clients["crm"] = client
    with pytest.raises(MCPVersionMismatchError, match="不做版本自动迁移"):
        await mgr.collect_tools()


@pytest.mark.asyncio
async def test_incompatible_spec_version_returns_explicit_error_without_calling_server() -> None:
    stub = _ClientStub()
    tool = mcp_tool_to_tool(
        stub,
        MCPServerTool(
            server="crm",
            name="get_order",
            description="query order",
            input_schema={"type": "object", "additionalProperties": True},
            version="2.1.0",
            expected_version="1.0.0",
        ),
    )
    registry = ToolRegistry([tool])
    result = await ToolExecutor(registry).execute(ToolCall(tool.name, {"id": 1}))
    assert result.status == "error"
    assert "版本不兼容" in result.observation
    assert "2.1.0" in result.observation
    assert "1.0.0" in result.observation
    assert stub.calls == []
