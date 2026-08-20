"""MCP 层测试。

分为两类：
1. 无需 ``mcp`` 包（适配器映射 / 配置解析 / 管线端到端调用 MCP 工具）：
   用 ``FakeMCPClient`` 顶替真实服务器，验证「AI ↔ 企业系统」的接入逻辑；
2. 真实服务器集成（需要 ``mcp`` 包）：以 stdio 子进程启动测试用 MCP 服务器，
   验证真实传输链路的连接 / 列举 / 调用，以及经适配器到 Agent 工具的闭环。
"""

import asyncio
import json

import pytest

from app.agents.pipeline import AgentPipeline, AgentState
from app.agents.tools.base import ToolRegistry
from app.core.config import settings
from app.mcp.adapter import mcp_tool_to_tool
from app.mcp.client import MCPServerTool
from app.mcp.config import list_configured_servers
from app.mcp.manager import MCPToolManager


class FakeMCPClient:
    """无需真实 MCP 服务器的替身，仅实现 ``call_tool``。"""

    def __init__(self, mapping: dict | None = None) -> None:
        self._mapping = mapping or {}

    async def call_tool(self, name: str, arguments: dict) -> str:
        if name in self._mapping:
            return self._mapping[name]
        return f"called {name} with {arguments}"


def _spec(name: str, server: str = "crm", description: str = "desc", schema: dict | None = None):
    return MCPServerTool(
        server=server,
        name=name,
        description=description,
        input_schema=schema or {"type": "object", "properties": {}},
    )


# ── 1. 适配器映射（无需 mcp 包）────────────────────────


def test_mcp_tool_to_tool_mapping() -> None:
    client = FakeMCPClient({"get_order": "订单 #42"})
    tool = mcp_tool_to_tool(client, _spec("get_order"))
    assert tool.name == "mcp__crm__get_order"
    assert tool.description == "[MCP:crm] desc"
    out = asyncio.run(tool.execute({"id": 1}))
    assert out == "订单 #42"


def test_mcp_tool_registered_and_described() -> None:
    client = FakeMCPClient()
    tool = mcp_tool_to_tool(client, _spec("get_order"))
    reg = ToolRegistry([tool])
    assert reg.get("mcp__crm__get_order") is tool
    assert "mcp__crm__get_order" in reg.describe()


def test_pipeline_invokes_mcp_tool() -> None:
    """AI 在「行动」阶段调用 MCP 映射工具，观测结果回灌草稿。"""
    client = FakeMCPClient({"get_order": "订单详情：状态=已发货"})
    tool = mcp_tool_to_tool(client, _spec("get_order"))
    reg = ToolRegistry([tool])

    class ToolCallingLLM:
        model = "fake"
        _used = False

        async def chat(self, messages, options=None):
            content = messages[-1].content
            if "## 可用外部工具" in content and not self._used:
                self._used = True
                return '<tool_call>{"name": "mcp__crm__get_order", "arguments": {"id": 1}}</tool_call>'
            return "已获取订单信息。"

        async def stream_chat(self, messages, options=None):
            yield "已获取订单信息。"

    pipeline = AgentPipeline(ToolCallingLLM(), tools=reg)
    state = AgentState(user_input="查一下订单 1")
    result = asyncio.run(pipeline.run(state))

    assert len(result.tool_results) == 1
    assert "已发货" in result.tool_results[0]
    assert result.error is None


def test_config_parsing() -> None:
    raw = json.dumps(
        [
            {"name": "crm", "transport": "stdio", "command": "python", "args": ["-m", "srv"]},
            {"name": "hr", "transport": "http", "url": "http://localhost:9000/mcp", "enabled": False},
        ]
    )
    settings.MCP_SERVERS = raw
    try:
        servers = list_configured_servers()
    finally:
        settings.MCP_SERVERS = ""  # 还原，避免污染其它测试
    names = [s.name for s in servers]
    assert "crm" in names and "hr" in names
    assert servers[1].enabled is False
    assert servers[0].command == "python"


# ── 2. 真实服务器集成（需要 mcp 包）────────────────────


mcp = pytest.importorskip("mcp")

import sys  # noqa: E402
import importlib.metadata as _md  # noqa: E402
from pathlib import Path  # noqa: E402

from app.mcp.client import MCPClient  # noqa: E402

_FIXTURE = str(Path(__file__).parent / "fixtures" / "echo_mcp_server.py")

# 夹具服务器依赖 mcp 2.x 的低层 Server API；1.x 下跳过这两例真实服务器集成测试。
_MCP_VERSION = tuple(int(x) for x in _md.version("mcp").split(".")[:2])
_REQUIRES_MCP_V2 = _MCP_VERSION < (2, 0)


@pytest.mark.asyncio
@pytest.mark.skipif(_REQUIRES_MCP_V2, reason="fixture 服务器使用 mcp 2.x 低层 API")
async def test_real_stdio_server_list_and_call() -> None:
    """通过真实 stdio 传输连接测试服务器，验证列举与调用。"""
    from app.mcp.config import MCPServerConfig

    client = MCPClient(
        MCPServerConfig(name="echo", transport="stdio", command=sys.executable, args=[_FIXTURE])
    )
    try:
        await client.connect()
        tools = await client.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "echo"
        out = await client.call_tool("echo", {"msg": "hi"})
        assert "echo:hi" in out
    finally:
        await client.disconnect()


@pytest.mark.asyncio
@pytest.mark.skipif(_REQUIRES_MCP_V2, reason="fixture 服务器使用 mcp 2.x 低层 API")
async def test_manager_collect_tools_from_real_server() -> None:
    """经 MCPToolManager 聚合真实服务器工具，并验证适配器可闭环调用。"""
    from app.mcp.config import MCPServerConfig

    client = MCPClient(
        MCPServerConfig(name="echo", transport="stdio", command=sys.executable, args=[_FIXTURE])
    )
    mgr = MCPToolManager()
    try:
        await client.connect()
        mgr._clients["echo"] = client
        tools = await mgr.collect_tools()
        assert tools[0].name == "mcp__echo__echo"
        out = await tools[0].execute({"msg": "闭环"})
        assert "echo:闭环" in out
    finally:
        await mgr.disconnect_all()


@pytest.mark.asyncio
async def test_connect_raises_without_mcp(monkeypatch) -> None:
    """在未安装 mcp 的环境（模拟）下 connect 应给出可读的 MCPNotAvailableError。"""
    from app.mcp.client import MCPNotAvailableError
    from app.mcp.config import MCPServerConfig
    import app.mcp.client as client_mod

    def _boom():
        raise MCPNotAvailableError("mcp 未安装（模拟）")

    monkeypatch.setattr(client_mod, "_require_mcp", _boom)
    client = MCPClient(MCPServerConfig(name="x", transport="stdio", command="python"))
    with pytest.raises(MCPNotAvailableError):
        await client.connect()
