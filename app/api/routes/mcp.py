"""MCP 管理接口：查看已配置的 MCP 服务器与可用工具（打通 AI ↔ 企业系统）。

端点：
- ``GET /mcp/servers``：列出 ``MCP_SERVERS`` 中配置的服务器（启用状态 / 传输）；
- ``GET /mcp/tools``：列出已连接服务器暴露的工具（名称、所属服务器、参数 Schema）。

两者均需 ``agents:read`` 权限。``/mcp/tools`` 在未启用或连接失败时为 503。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_user, require_permission
from app.core.config import settings
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["mcp"])


@router.get("/servers")
async def list_servers(
    current_user: User = Depends(require_permission("agents", "read")),
) -> list[dict]:
    """列出已配置的 MCP 服务器（不含密钥类字段）。"""
    from app.mcp.config import list_configured_servers

    return [s.model_summary() for s in list_configured_servers()]


@router.get("/tools")
async def list_mcp_tools(
    current_user: User = Depends(require_permission("agents", "read")),
) -> list[dict]:
    """列出已连接 MCP 服务器暴露的工具。"""
    if not settings.MCP_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MCP 未启用（MCP_ENABLED=false）。",
        )
    from app.mcp.manager import get_mcp_manager

    mgr = await get_mcp_manager()
    if mgr is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MCP 已启用但未连接任何服务器，请检查 MCP_SERVERS 配置与可达性。",
        )
    tools = await mgr.collect_tools()
    return [
        {
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters,
        }
        for t in tools
    ]
