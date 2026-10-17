"""Loopback-only UI fixture with a temporary database and offline model providers.

Run from the repository using the project interpreter. The login identity is
ui-fixture; its form text is ignored and is not an authentication credential.
This fixture does not validate password authentication or model quality.
"""

import asyncio
import ipaddress
import os
import secrets
import socket
import tempfile
from pathlib import Path


def main(provider_factory=None, *, port: int = 18765) -> None:
    from pydantic_settings.sources import DotEnvSettingsSource

    DotEnvSettingsSource._read_env_files = lambda self: {}
    root = Path(__file__).resolve().parents[1]
    temporary = root / "data" / "pytest-tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    database = Path(tempfile.mkdtemp(prefix="sse-ui-", dir=temporary)) / "ui.db"
    os.environ.update(
        ENV="development", DATABASE_URL=f"sqlite:///{database.as_posix()}",
        JWT_SECRET_KEY=secrets.token_urlsafe(48), SERVE_FRONTEND="true", AUTH_ENABLED="true",
        LLM_API_KEY="", LLM_INTENT_API_KEY="", LLM_FALLBACK_API_KEY="", EMBEDDING_API_KEY="",
        INITIAL_ADMIN_USERNAME="", INITIAL_ADMIN_PASSWORD="", INITIAL_SYSTEM_PASSWORD="",
        INITIAL_CULTURE_PASSWORD="", WORKFLOW_ENABLED="false", EVOLUTION_DISTILL_ENABLED="false",
    )
    original_connect = socket.socket.connect
    original_lookup = socket.getaddrinfo

    def local(host):
        if host is None or host == "localhost":
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    def connect(sock, address):
        if sock.family != getattr(socket, "AF_UNIX", None) and not local(address[0]):
            raise AssertionError("UI fixture blocks external networking")
        return original_connect(sock, address)

    def lookup(host, *args, **kwargs):
        if not local(host):
            raise AssertionError("UI fixture blocks external DNS")
        return original_lookup(host, *args, **kwargs)

    socket.socket.connect = connect
    socket.getaddrinfo = lookup

    import uvicorn
    from sqlmodel import Session

    import app.api.routes.auth as auth_route
    from app.core.database import engine, init_db
    from app.core.security import hash_password
    from app.llm.factory import set_llm_provider_override
    from app.llm.mock import MockLLMProvider
    from app.main import app
    from app.models.user import Tenant, User
    from app.rag.embeddings.factory import set_embedding_override
    from app.rag.embeddings.mock import MockEmbeddingProvider

    init_db()
    with Session(engine) as session:
        session.add(Tenant(id="ui-fixture-tenant", name="隔离界面验证"))
        session.add(User(id="ui-fixture-user", tenant_id="ui-fixture-tenant", username="ui-fixture",
                         role="system_admin", hashed_password=hash_password(secrets.token_urlsafe(32)),
                         must_change_password=False))
        session.commit()

    def fixture_authenticate(session, username, _placeholder, *, report_disabled=False):
        return session.get(User, "ui-fixture-user") if username == "ui-fixture" else None

    class SlowFixture(MockLLMProvider):
        async def stream_chat(self, messages, options=None):
            for character in "隔离界面验证回复：流式事件到达页面，使用模拟模型验证协议，不代表真实模型质量。":
                await asyncio.sleep(0.8)
                yield character

    auth_route.authenticate = fixture_authenticate
    set_llm_provider_override(provider_factory() if provider_factory is not None else SlowFixture())
    set_embedding_override(MockEmbeddingProvider(dim=64))
    print(f"ISOLATED_UI_URL=http://127.0.0.1:{port}", flush=True)
    print("Temporary identity fixture: ui-fixture. Do not enter real credentials.", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
