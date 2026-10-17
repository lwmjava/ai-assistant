"""Bounded manual live acceptance; only explicit execution can send requests."""

import argparse
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import socket
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "data/llm001-two-vendor-attempts-20261017.json"
VENDORS = {
    "qwen": ("DASHSCOPE_API_KEY", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    "deepseek": ("Deepseek_Key", "https://api.deepseek.com", "deepseek-flash"),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vendor", choices=VENDORS)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print("No requests sent; --execute permits at most two real requests for this vendor.")
        return 0
    key_name, endpoint, model = VENDORS[args.vendor]
    key = os.environ.get(key_name, "")
    if not key.strip():
        print(json.dumps({"status": "blocked", "reason": "process_credential_missing"}))
        return 2
    sys.path.insert(0, str(ROOT))
    from pydantic_settings.sources import DotEnvSettingsSource
    DotEnvSettingsSource._read_env_files = lambda self: {}
    LEDGER.parent.mkdir(exist_ok=True)
    ledger = json.loads(LEDGER.read_text()) if LEDGER.exists() else {"vendors": {}, "attempts": []}
    if args.vendor in ledger["vendors"]:
        print(json.dumps({"status": "blocked", "reason": "vendor_already_attempted"}))
        return 2
    ledger["vendors"][args.vendor] = "started"
    LEDGER.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    temp = Path(tempfile.mkdtemp(prefix="llm001-live-", dir=ROOT / "data"))
    os.environ.update({
        "ENV": "development", "DATABASE_URL": "sqlite:///" + (temp / "case.db").as_posix(),
        "JWT_SECRET_KEY": secrets.token_urlsafe(48), "LLM_PROVIDER": "openai",
        "LLM_BASE_URL": endpoint, "LLM_DEFAULT_MODEL": model, "LLM_API_KEY": key,
        "LLM_INTENT_API_KEY": "", "LLM_FALLBACK_API_KEY": "", "LLM_RERANK_API_KEY": "",
        "EMBEDDING_API_KEY": "", "LLM_CHAT_FALLBACK_CHAIN": "chat", "LLM_INTENT_FALLBACK_CHAIN": "chat",
        "LLM_MAX_TOKENS": "128", "LLM_TIMEOUT": "30", "LLM_CAPABILITY_GUARD_ENABLED": "true",
        "LLM_CAPABILITY_DECLARED": "dashscope.aliyuncs.com/compatible-mode/v1!qwen-plus=1000000:32768",
        "LLM_CAPABILITY_DECLARED_SOURCE": "https://help.aliyun.com/zh/model-studio/qwen-plus",
        "AGENT_ORCHESTRATION": "langgraph", "SKILL_ENABLED": "true", "RAG_ENABLED": "true",
        "RAG_BACKEND": "native", "RAG_VECTOR_STORE": "local", "SECURITY_RATE_LIMIT": "false",
        "INITIAL_ADMIN_USERNAME": "", "INITIAL_ADMIN_PASSWORD": "",
        "INITIAL_SYSTEM_PASSWORD": "", "INITIAL_CULTURE_PASSWORD": "", "EVOLUTION_ENABLED": "false",
    })
    for name in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]:
        os.environ.pop(name, None)
    logging.disable(logging.CRITICAL)
    from urllib.parse import urlsplit
    hostname = urlsplit(endpoint).hostname
    addresses = {"127.0.0.1", "::1"}
    lookup, connect = socket.getaddrinfo, socket.socket.connect

    def bounded_lookup(host, *a, **kw):
        name = host.decode("ascii") if isinstance(host, bytes) else host
        if name not in {hostname, "localhost", "127.0.0.1", "::1"}:
            raise RuntimeError("dns_boundary")
        result = lookup(host, *a, **kw)
        if name == hostname:
            addresses.update(row[4][0] for row in result)
        return result

    def bounded_connect(sock, address):
        if sock.family != getattr(socket, "AF_UNIX", None):
            if address[0] not in addresses or (address[0] not in {"127.0.0.1", "::1"} and address[1] != 443):
                raise RuntimeError("socket_boundary")
        return connect(sock, address)

    socket.getaddrinfo, socket.socket.connect = bounded_lookup, bounded_connect
    import httpx
    from sqlmodel import Session, col, select
    from app.core.database import engine, init_db
    from app.models.conversation import Message
    from app.models.user import Tenant, User
    from app.rag.embeddings.factory import set_embedding_override
    from app.rag.embeddings.mock import MockEmbeddingProvider
    from app.services.chat_service import ChatService

    original_send = httpx.AsyncClient.send
    records = []

    def save():
        LEDGER.write_text(json.dumps(ledger, indent=2), encoding="utf-8")

    def chunk_metadata(data, record):
        if isinstance(data.get("model"), str):
            record["response_model"] = data["model"][:80]
        usage = data.get("usage")
        if isinstance(usage, dict):
            record["usage"] = {k: v for k, v in usage.items() if isinstance(v, int)}

    class ObservedStream(httpx.AsyncByteStream):
        def __init__(self, stream, record):
            self.stream, self.record, self.pending = stream, record, b""

        async def __aiter__(self):
            async for piece in self.stream:
                self.pending += piece
                while b"\n" in self.pending:
                    line, self.pending = self.pending.split(b"\n", 1)
                    if line.startswith(b"data:") and line[5:].strip() != b"[DONE]":
                        try:
                            chunk_metadata(json.loads(line[5:]), self.record)
                        except (ValueError, TypeError):
                            pass
                if len(self.pending) > 262144:
                    raise RuntimeError("response_line_boundary")
                yield piece

        async def aclose(self):
            await self.stream.aclose()
            save()

    async def bounded_send(client, request, *a, **kw):
        payload = json.loads(request.content)
        if (request.method != "POST" or str(request.url) != endpoint + "/chat/completions"
                or payload.get("model") != model or not 0 < payload.get("max_tokens", 0) <= 128
                or len(request.content) > 32768 or len(records) >= 2 or len(ledger["attempts"]) >= 4):
            raise RuntimeError("request_boundary")
        record = {"vendor": args.vendor, "request_model": model, "stream": bool(payload.get("stream")),
                  "request_bytes": len(request.content), "max_output_tokens": payload["max_tokens"],
                  "conservative_cost_bound_cny": (32768 * 3 + 128 * 12) / 1000000}
        records.append(record)
        ledger["attempts"].append(record)
        save()
        try:
            response = await original_send(client, request, *a, **kw)
            record["http_status"] = response.status_code
            if payload.get("stream"):
                response.stream = ObservedStream(response.stream, record)
            elif response.status_code == 200:
                chunk_metadata(response.json(), record)
            save()
            return response
        except BaseException as exc:
            record["transport_error_type"] = type(exc).__name__
            save()
            raise

    httpx.AsyncClient.send = bounded_send
    init_db()
    identity = "synthetic-" + secrets.token_hex(8)
    result = {"vendor": args.vendor, "model": model, "endpoint": endpoint, "status": "failed",
              "credential_source": key_name, "requests": records,
              "isolation": "new synthetic SQLite; original ChatService/factory; simple route; MockEmbedding only"}
    try:
        with Session(engine) as session:
            user = User(id=identity, tenant_id=identity, username=identity, hashed_password="", role="member")
            session.add(Tenant(id=identity, name=identity))
            session.add(user)
            session.commit()
            session.refresh(user)
            set_embedding_override(MockEmbeddingProvider(dim=64))
            conversation, reply = asyncio.run(ChatService().chat(session, user, "你好，请只回复：你好，验收成功。"))
            conversation_id = conversation.id
        with Session(engine) as session:
            messages = session.exec(select(Message).where(col(Message.conversation_id) == conversation_id)).all()
            persisted = any(m.role == "assistant" and m.status == "complete" and m.content == reply
                            and m.model == model for m in messages)
            user_saved = any(m.role == "user" for m in messages)
        result.update({"persisted_assistant": persisted, "persisted_user": user_saved,
                       "reply_contains_hello": "你好" in reply, "reply_length": len(reply),
                       "reply_sha256": hashlib.sha256(reply.encode()).hexdigest()})
        if persisted and user_saved and "你好" in reply and records and all(r.get("http_status") == 200 for r in records):
            result["status"] = "passed"
    except Exception as exc:
        result["error_type"] = type(exc).__name__
    finally:
        set_embedding_override(None)
        ledger["vendors"][args.vendor] = result["status"]
        save()
    output = ROOT / f"docs/reviews/llm001_{args.vendor}_live_20261017.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
