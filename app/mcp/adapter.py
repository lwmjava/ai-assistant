"""将 MCP 服务器工具适配为项目既有 ``Tool`` 抽象。

映射规则：
- 工具名加命名空间前缀 ``mcp__{server}__{tool}``，避免与内置工具或其它服务器的
  同名工具冲突；
- 描述附加 ``[MCP:{server}]`` 前缀，便于模型区分来源；
- 风险等级与内置工具共用 ``L0``–``L3``；未声明风险时不执行，``L2`` 及以上在执行器进入审批。
"""

from __future__ import annotations

from app.agents.tools.base import Tool
from app.agents.tools.contract import RiskLevel, ToolContract, ToolPermission
from app.core.config import settings
from app.mcp.client import MCPClient, MCPServerTool

_RISKS = {item.value: item for item in RiskLevel}
_EFFECTS = {
    RiskLevel.L0: "external_read",
    RiskLevel.L1: "external_reversible_write",
    RiskLevel.L2: "external_write",
    RiskLevel.L3: "external_sensitive_write",
}


def _contract(spec: MCPServerTool) -> ToolContract | None:
    if not spec.risk:
        return None
    risk = _RISKS.get(spec.risk.strip().upper())
    if risk is None:
        return None
    roles = frozenset(spec.allowed_roles) if spec.allowed_roles is not None else None
    needs_identity = risk != RiskLevel.L0
    timeout = float(spec.timeout_seconds or settings.MCP_CALL_TIMEOUT_SECONDS)
    return ToolContract(
        version="1.0.0",
        permission=ToolPermission(
            authenticated=bool(spec.authenticated or needs_identity or roles is not None),
            allowed_roles=roles,
            tenant_scoped=bool(spec.tenant_scoped or needs_identity),
            resource="tools",
            action="read" if risk == RiskLevel.L0 else "write",
        ),
        risk=risk,
        side_effect=_EFFECTS[risk],
        timeout_seconds=timeout,
    )


def mcp_tool_to_tool(client: MCPClient, spec: MCPServerTool) -> Tool:
    """将一个 MCP 工具包装为项目 ``Tool``。"""
    server = spec.server

    async def _invoke(arguments: dict) -> str:
        return await client.call_tool(
            spec.name, arguments, timeout_seconds=spec.timeout_seconds,
        )

    return Tool(
        name=f"mcp__{server}__{spec.name}",
        description=f"[MCP:{server}] {spec.description}".strip(),
        parameters=spec.input_schema or {"type": "object", "properties": {}},
        func=_invoke,
        contract=_contract(spec),
    )
