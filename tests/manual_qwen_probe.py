"""Explicit, bounded Qwen live acceptance; never collected as a pytest test."""

import argparse
import asyncio
import json
import logging
import os
import secrets
import socket
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print("Qwen only; at most 8 HTTPS requests, 256 output tokens/request; see approved live probe plan.")
        return 0

    from dotenv import dotenv_values
    from pydantic_settings.sources import DotEnvSettingsSource

    root = Path(__file__).resolve().parents[1]
    values = dotenv_values(root / ".env", interpolate=False)
    key = values.get("DASHSCOPE_API_KEY") or values.get("QWEN_API_KEY") or values.get("EMBEDDING_API_KEY") or ""
    if not key.strip():
        print(json.dumps({"status": "blocked", "reason": "approved_qwen_credential_missing"}))
        return 2
    values.clear()
    DotEnvSettingsSource._read_env_files = lambda self: {}
    temporary = root / "data" / "pytest-tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    database = Path(tempfile.mkdtemp(prefix="qwen-probe-", dir=temporary)) / "probe.db"
    os.environ.update(
        ENV="development", DATABASE_URL=f"sqlite:///{database.as_posix()}",
        JWT_SECRET_KEY=secrets.token_urlsafe(48), LLM_PROVIDER="openai",
        LLM_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1", LLM_DEFAULT_MODEL="qwen-plus",
        LLM_API_KEY=key, LLM_FALLBACK_API_KEY="", LLM_INTENT_API_KEY="", EMBEDDING_API_KEY="",
        LLM_CHAT_FALLBACK_CHAIN="chat", LLM_INTENT_FALLBACK_CHAIN="chat", LLM_MAX_TOKENS="256",
        LLM_TIMEOUT="30", LLM_CAPABILITY_GUARD_ENABLED="true",
        LLM_CAPABILITY_DECLARED="dashscope.aliyuncs.com/compatible-mode/v1!qwen-plus=1000000:32768",
        LLM_CAPABILITY_DECLARED_SOURCE="https://help.aliyun.com/zh/model-studio/qwen-plus",
        INITIAL_ADMIN_USERNAME="", INITIAL_ADMIN_PASSWORD="", INITIAL_SYSTEM_PASSWORD="",
        INITIAL_CULTURE_PASSWORD="",
    )
    logging.disable(logging.CRITICAL)
    permitted_addresses = {"127.0.0.1", "::1"}
    original_lookup, original_connect = socket.getaddrinfo, socket.socket.connect

    def bounded_lookup(host, *lookup_args, **lookup_kwargs):
        hostname = host.decode("ascii") if isinstance(host, bytes) else host
        if hostname not in {"dashscope.aliyuncs.com", "localhost", "127.0.0.1", "::1"}:
            raise RuntimeError("live_probe_dns_boundary")
        result = original_lookup(host, *lookup_args, **lookup_kwargs)
        if hostname == "dashscope.aliyuncs.com":
            permitted_addresses.update(row[4][0] for row in result)
        return result

    def bounded_connect(sock, address):
        if sock.family != getattr(socket, "AF_UNIX", None):
            if address[0] in {"127.0.0.1", "::1"}:
                return original_connect(sock, address)
            if address[0] not in permitted_addresses or address[1] != 443:
                raise RuntimeError("live_probe_socket_boundary")
        return original_connect(sock, address)

    socket.getaddrinfo, socket.socket.connect = bounded_lookup, bounded_connect
    import httpx
    from sqlmodel import Session, col, select

    from app.agents.tools.base import ToolRegistry
    from app.core.database import engine, init_db
    from app.models.conversation import Message
    from app.models.user import Tenant, User
    from app.rag.embeddings.factory import set_embedding_override
    from app.rag.embeddings.mock import MockEmbeddingProvider
    from app.services.chat_service import ChatService

    calls = []
    original_send = httpx.AsyncClient.send

    async def bounded_send(client, request, *send_args, **send_kwargs):
        url = request.url
        if (request.method != "POST" or url.scheme != "https" or url.host != "dashscope.aliyuncs.com"
                or url.path != "/compatible-mode/v1/chat/completions" or url.query or len(calls) >= 8):
            raise RuntimeError("live_probe_outbound_boundary")
        body = request.content
        payload = json.loads(body)
        if (len(body) > 32768 or payload.get("model") != "qwen-plus"
                or not 0 < payload.get("max_tokens", 0) <= 256):
            raise RuntimeError("live_probe_payload_boundary")
        record = {"request": len(calls) + 1, "model": "qwen-plus"}
        calls.append(record)
        try:
            response = await original_send(client, request, *send_args, **send_kwargs)
        except Exception as exc:
            record["transport_error_type"] = type(exc).__name__
            if isinstance(exc, RuntimeError) and str(exc) in {
                "live_probe_dns_boundary", "live_probe_socket_boundary",
            }:
                record["boundary"] = str(exc)
            raise
        record["http_status"] = response.status_code
        if response.status_code == 200 and not payload.get("stream"):
            data = response.json()
            usage = data.get("usage", {})
            record["usage"] = {name: usage[name] for name in ("prompt_tokens", "completion_tokens", "total_tokens")
                               if isinstance(usage.get(name), int)}
        return response

    httpx.AsyncClient.send = bounded_send
    init_db()
    identity = "probe-" + secrets.token_hex(8)
    with Session(engine) as session:
        user = User(id=identity, tenant_id=identity, username=identity, hashed_password="", role="member")
        session.add(Tenant(id=identity, name=identity))
        session.add(user)
        session.commit()
        session.refresh(user)
        set_embedding_override(MockEmbeddingProvider(dim=64))
        try:
            service = ChatService()

            async def no_external_tools():
                return ToolRegistry()

            service._build_tools = no_external_tools
            conversation, reply = asyncio.run(service.chat(
                session, user, "这是一条合成连通验收消息。请只回复：验收成功。不要调用工具。"))
            conversation_id = conversation.id
        except Exception as exc:
            print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "requests": calls}))
            return 1
        finally:
            set_embedding_override(None)
    with Session(engine) as fresh:
        messages = fresh.exec(select(Message).where(col(Message.conversation_id) == conversation_id)).all()
        complete = any(message.role == "assistant" and message.status == "complete" and message.model == "qwen-plus"
                       and message.content == reply and bool(reply.strip()) for message in messages)
    complete = complete and "验收成功" in reply and bool(calls) and all(
        record.get("http_status") == 200 for record in calls)
    print(json.dumps({"status": "passed" if complete else "failed", "provider": "qwen",
                      "model": "qwen-plus", "complete_persisted_reply": complete,
                      "reply_length": len(reply), "requests": calls}, ensure_ascii=False))
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
