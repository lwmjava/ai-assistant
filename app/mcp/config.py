"""MCP 服务器配置。

服务器清单通过环境变量 ``MCP_SERVERS`` 以 JSON 数组提供。``env`` 和
``headers`` 的值只能是进程环境变量名，不能写凭据本身：

.. code-block:: json

   [
     {"name": "crm", "transport": "stdio", "command": "python",
      "args": ["-m", "my_crm_mcp_server"], "env": {"ACCOUNT": "CRM_ACCOUNT"}},
     {"name": "hr", "transport": "http", "url": "http://localhost:9000/mcp",
      "headers": {"Authorization": "HR_AUTHORIZATION"}},
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
import os
import re
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, Field, field_validator

from app.core.config import settings

logger = logging.getLogger(__name__)

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_HEADER_NAME = re.compile(r"^[A-Za-z0-9-]+$")
_SECRET_QUERY = re.compile(r"(token|secret|password|authorization|api_key|access_key)", re.IGNORECASE)


class MCPCredentialError(ValueError):
    """凭据引用无法解析。消息里只有变量名。"""


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
    timeout_seconds: float | None = None
    default_risk: str | None = "L0"
    allowed_roles: list[str] | None = None
    authenticated: bool = False
    tenant_scoped: bool = False
    expected_version: str | None = None
    tool_versions: dict[str, str] = Field(default_factory=dict)

    @field_validator("env")
    @classmethod
    def _env_references(cls, value: dict[str, str]) -> dict[str, str]:
        for key, ref in value.items():
            if not _ENV_NAME.fullmatch(key) or not _ENV_NAME.fullmatch(ref):
                raise ValueError("credential must reference an environment variable")
        return value

    @field_validator("headers")
    @classmethod
    def _header_references(cls, value: dict[str, str]) -> dict[str, str]:
        for key, ref in value.items():
            if not _HEADER_NAME.fullmatch(key) or not _ENV_NAME.fullmatch(ref):
                raise ValueError("credential must reference an environment variable")
        return value

    @field_validator("url")
    @classmethod
    def _url_without_credentials(cls, value: str | None) -> str | None:
        if not value:
            return value
        parts = urlsplit(value)
        if parts.username or parts.password:
            raise ValueError("url must not contain credentials")
        for name, _item in parse_qsl(parts.query, keep_blank_values=True):
            if _SECRET_QUERY.search(name):
                raise ValueError("url must not contain credentials")
        return value

    def resolved_env(self) -> dict[str, str] | None:
        """把环境变量名解析成子进程环境。不把值放进配置或日志。"""
        return _resolve_references(self.name, self.env)

    def resolved_headers(self) -> dict[str, str] | None:
        """把请求头引用解析成实际请求头。不把值放进配置或日志。"""
        return _resolve_references(self.name, self.headers)

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
    except json.JSONDecodeError:
        logger.warning("MCP_SERVERS 解析失败（应为 JSON 数组）")
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
        except Exception:  # noqa: BLE001 — 单条配置错误不应影响其它服务器，也不能把凭据写入日志
            logger.warning("跳过 MCP 服务器 %s：凭据只能引用环境变量名", item.get("name"))
    return servers


def _resolve_references(server_name: str, references: dict[str, str]) -> dict[str, str] | None:
    if not references:
        return None
    resolved: dict[str, str] = {}
    for key, ref in references.items():
        value = os.environ.get(ref, "")
        if not value:
            raise MCPCredentialError(f"MCP 服务器 {server_name} 缺少环境变量 {ref}")
        resolved[key] = value
    return resolved
