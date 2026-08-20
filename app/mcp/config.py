"""MCP 服务器配置。

服务器清单通过环境变量 ``MCP_SERVERS`` 以 JSON 数组提供，例如：

.. code-block:: json

   [
     {"name": "crm", "transport": "stdio", "command": "python",
      "args": ["-m", "my_crm_mcp_server"], "env": {"API_KEY": "x"}},
     {"name": "hr", "transport": "http", "url": "http://localhost:9000/mcp"},
     {"name": "legacy", "transport": "sse", "url": "http://host:8000/sse"}
   ]

``transport`` 取值：
- ``stdio``：本地子进程，需 ``command``（+ 可选 ``args`` / ``env``）；
- ``http`` / ``streamable_http``：MCP streamable HTTP 端点，需 ``url``；
- ``sse``：旧式 SSE 端点，需 ``url``。
"""

from __future__ import annotations

import json
import logging

from pydantic import BaseModel, Field

from app.core.config import settings

logger = logging.getLogger(__name__)


class MCPServerConfig(BaseModel):
    """单个 MCP 服务器的连接配置。"""

    name: str
    enabled: bool = True
    transport: str = "stdio"  # stdio | http | sse
    # stdio 传输
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    # http / sse 传输
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)

    def model_summary(self) -> dict:
        """供管理接口使用的精简摘要（不含密钥类字段）。"""
        return {
            "name": self.name,
            "enabled": self.enabled,
            "transport": self.transport,
            "command": self.command,
            "url": self.url,
        }


def list_configured_servers() -> list[MCPServerConfig]:
    """解析 ``settings.MCP_SERVERS`` 为服务器配置列表。

    解析失败时返回空列表（不阻断应用启动），并在日志告警。
    """
    raw = (settings.MCP_SERVERS or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("MCP_SERVERS 解析失败（应为 JSON 数组）：%s", exc)
        return []
    if not isinstance(data, list):
        logger.warning("MCP_SERVERS 应为 JSON 数组，已忽略。")
        return []

    servers: list[MCPServerConfig] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            servers.append(MCPServerConfig(**item))
        except Exception as exc:  # noqa: BLE001 — 单条配置错误不应影响其它服务器
            logger.warning("跳过无效的 MCP 服务器配置 %s：%s", item.get("name"), exc)
    return servers
