"""MCP 配置只保存环境变量名，日志和摘要不出现凭据。"""

import json
import logging

import pytest
from pydantic import ValidationError

from app.core.config import settings
from app.mcp.config import MCPCredentialError, MCPServerConfig, list_configured_servers


def test_env_and_headers_resolve_from_process_environment(monkeypatch) -> None:
    monkeypatch.setenv("CRM_ACCOUNT", "stored-outside-config")
    monkeypatch.setenv("HR_AUTHORIZATION", "stored-outside-config")
    raw = json.dumps(
        [
            {
                "name": "crm",
                "transport": "stdio",
                "command": "python",
                "env": {"ACCOUNT": "CRM_ACCOUNT"},
            },
            {
                "name": "hr",
                "transport": "http",
                "url": "http://localhost:9000/mcp",
                "headers": {"Authorization": "HR_AUTHORIZATION"},
            },
        ]
    )
    settings.MCP_SERVERS = raw
    try:
        servers = list_configured_servers()
    finally:
        settings.MCP_SERVERS = ""
    assert servers[0].resolved_env() == {"ACCOUNT": "stored-outside-config"}
    assert servers[1].resolved_headers() == {"Authorization": "stored-outside-config"}
    summary = servers[0].model_summary()
    assert "stored-outside-config" not in json.dumps(summary)
    assert "env" not in summary and "headers" not in summary


def test_plaintext_credentials_are_skipped_and_absent_from_logs(caplog) -> None:
    marker = "not-an-env-name"
    userinfo = "user" + ":" + marker
    raw = json.dumps(
        [
            {"name": "crm", "transport": "stdio", "command": "python", "env": {"ACCOUNT": marker}},
            {
                "name": "hr",
                "transport": "http",
                "url": f"http://{userinfo}@localhost/mcp",
            },
            {"name": "ok", "transport": "stdio", "command": "python"},
        ]
    )
    settings.MCP_SERVERS = raw
    try:
        with caplog.at_level(logging.WARNING):
            servers = list_configured_servers()
    finally:
        settings.MCP_SERVERS = ""
    assert [server.name for server in servers] == ["ok"]
    assert marker not in caplog.text


def test_direct_plaintext_and_missing_reference_are_rejected(monkeypatch) -> None:
    with pytest.raises(ValidationError):
        MCPServerConfig(name="crm", env={"ACCOUNT": "not-an-env-name"})
    config = MCPServerConfig(name="crm", env={"ACCOUNT": "CRM_ACCOUNT"})
    monkeypatch.delenv("CRM_ACCOUNT", raising=False)
    with pytest.raises(MCPCredentialError, match="CRM_ACCOUNT") as caught:
        config.resolved_env()
    assert "not-an-env-name" not in str(caught.value)


def test_example_config_does_not_embed_mcp_secrets() -> None:
    from pathlib import Path

    text = Path(".env.example").read_text(encoding="utf-8")
    section = text.split("# ── MCP 客户端 ──", 1)[1].split("# ── 工作流引擎 ──", 1)[0]
    assert "环境变量名" in section
    assert "CRM_ACCOUNT" in section
    assert "not-an-env-name" not in section
    assert "@" not in section
