"""Independent cache acceptance: isolated SQLModel, real vector store and local HTTP.

Synthetic fixtures prove isolation/call avoidance, never paid cost or answer quality.
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.config import settings
from app.core.security import Role
from app.llm.base import ChatMessage, ChatRole, LLMOptions
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.models.rag import Document, DocumentChunk
from app.models.user import Tenant, User
from app.rag.document_parsers.base import ParsedDocument
from app.rag.embeddings.base import EmbeddingInputPolicy, EmbeddingProvider
from app.rag.service import RAGService
from app.rag.vectorstore.local import LocalVectorStore
from app.services.chat_service import ChatService


class SyntheticEmbedding(EmbeddingProvider):
    model = "acceptance-synthetic-v1"
    dim = 3
    input_policy = EmbeddingInputPolicy(None, counting_method="offline-unlimited")

    async def embed(self, texts):
        return [[1.0, 0.5, 0.25] for _ in texts]


class CountingLocalStore(LocalVectorStore):
    def __init__(self, session):
        super().__init__(session)
        self.calls = 0

    async def hybrid_search(self, *args, **kwargs):
        self.calls += 1
        return await super().hybrid_search(*args, **kwargs)


@pytest.fixture
def cache_env(monkeypatch, tmp_path):
    # Model fields arrive with implementation; object setattr supports red baseline.
    for name, value in {
        "RETRIEVAL_CACHE_ENABLED": True,
        "LLM_RESPONSE_CACHE_ENABLED": True,
        "CACHE_TTL_SECONDS": 60.0,
        "CACHE_MAX_ENTRIES": 256,
        "CACHE_MAX_BYTES": 8 * 1024 * 1024,
        "CACHE_MAX_ENTRY_BYTES": 256 * 1024,
    }.items():
        if hasattr(settings, name):
            monkeypatch.setattr(settings, name, value)
        else:
            monkeypatch.setattr(settings.__class__, name, value, raising=False)
    for name, value in {
        "RAG_VECTOR_STORE": "local", "RAG_BACKEND": "native",
        "RAG_ENABLED": True, "RAG_KB_SCOPE": "tenant",
        "RAG_MIN_SIMILARITY": 0.0, "LLM_TEMPERATURE": 0.0,
        "AGENT_ORCHESTRATION": "self", "EVOLUTION_ENABLED": False,
        "AGENT_QUALITY_GATE_ENABLED": False, "LLM_CAPABILITY_GUARD_ENABLED": False,
    }.items():
        monkeypatch.setattr(settings, name, value)
    engine = create_engine(f"sqlite:///{tmp_path / 'cache.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([Tenant(id="a", name="A"), Tenant(id="b", name="B")])
        session.add_all([
            User(id="alice", tenant_id="a", username="alice", hashed_password="", role=Role.SYSTEM_ADMIN.value),
            User(id="bob", tenant_id="a", username="bob", hashed_password=""),
            User(id="eve", tenant_id="b", username="eve", hashed_password=""),
        ])
        session.commit()
    yield engine
    engine.dispose()


def rag_for(session, user_id="alice"):
    user = session.get(User, user_id)
    store = CountingLocalStore(session)
    return RAGService(session, user.tenant_id, SyntheticEmbedding(), store, reader=user), store, user


@pytest.mark.asyncio
async def test_cache_acceptance_real_local_query_avoids_second_vector_call(cache_env):
    with Session(cache_env) as session:
        rag, store, user = rag_for(session)
        doc = await rag.ingest_text("热点价格 199 元。", "价格", "synthetic", user.id)
        first = await rag.search("热点价格")
        second = await rag.search("热点价格")
        assert first and first[0].document_id == doc.id
        assert second == first
        assert store.calls == 1, "same authorized query must skip actual second vector call"


@pytest.mark.asyncio
async def test_cache_acceptance_lifecycle_publish_reparse_delete(cache_env):
    with Session(cache_env) as session:
        rag, store, user = rag_for(session)
        old = await rag.ingest_text("价格旧版 199 元。", "old", "synthetic", user.id)
        new = await rag.ingest_text("价格新版 299 元。", "new", "synthetic", user.id,
                                    is_current=False, version_state="draft", version_group_id=old.version_group_id)
        assert {h.document_id for h in await rag.search("价格")} == {old.id}
        assert rag.publish_document(new.id, user) is not None
        assert {h.document_id for h in await rag.search("价格")} == {new.id}
        await rag.reindex_document_in_place(
            new, ParsedDocument(text="价格重解析 399 元。", title="new", source="synthetic",
                                extension="txt", content_type="text/plain"), content_hash="acceptance-v2"
        )
        assert "399" in (await rag.search("价格"))[0].content
        assert await rag.delete_document(new.id, user)
        assert await rag.search("价格") == []
        assert store.calls == 4


@pytest.mark.asyncio
async def test_cache_acceptance_permissions_mutate_in_new_session(cache_env, monkeypatch):
    monkeypatch.setattr(settings, "RAG_KB_SCOPE", "uploader")
    with Session(cache_env) as session:
        rag, store, user = rag_for(session)
        doc = await rag.ingest_text("上传者专属机密。", "private", "synthetic", "bob")
        assert (await rag.search("机密"))[0].document_id == doc.id
        with Session(cache_env) as writer:
            actor = writer.get(User, "alice")
            actor.role = Role.MEMBER.value
            writer.add(actor)
            writer.commit()
        with pytest.raises(PermissionError):
            await rag.search("机密")
        with Session(cache_env) as fresh:
            reduced, reduced_store, _ = rag_for(fresh)
            assert await reduced.search("机密") == []
            assert reduced_store.calls == 1
        assert store.calls == 1


@pytest.fixture
def local_llm():
    requests = []
    control = {"model": "acceptance-model", "finish": "stop", "done": True, "status": 200,
               "echo_content": False}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 -- standard library handler method
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(payload)
            self.send_response(control["status"])
            self.send_header("Content-Type", "text/event-stream" if payload["stream"] else "application/json")
            self.send_header("Connection", "close")
            self.end_headers()
            if control["status"] != 200:
                return
            content = "独立验收回答，无需修正。"
            response_model = control["model"]
            if control["echo_content"]:
                content = f"{payload['messages'][-1]['content']}|{payload['temperature']}|{payload['model']}"
                response_model = payload["model"]
            if payload["stream"]:
                item = {"model": response_model, "choices": [{"delta": {"content": content}, "finish_reason": None}]}
                ending = {"model": response_model, "choices": [{"delta": {}, "finish_reason": control["finish"]}]}
                data = f"data: {json.dumps(item)}\n\ndata: {json.dumps(ending)}\n\n"
                if control["done"]:
                    data += "data: [DONE]\n\n"
                self.wfile.write(data.encode())
            else:
                self.wfile.write(json.dumps({
                    "model": response_model,
                    "choices": [{"message": {"content": content}, "finish_reason": control["finish"]}],
                }).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/v1", requests, control
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.mark.asyncio
async def test_cache_acceptance_default_chatservice_actual_http_count(cache_env, local_llm, monkeypatch):
    from app.rag.embeddings.factory import set_embedding_override

    url, requests, _ = local_llm
    set_embedding_override(SyntheticEmbedding())
    try:
        with Session(cache_env) as session:
            _, _, alice = rag_for(session)
            provider = OpenAICompatibleProvider(url, "synthetic-local-test", "acceptance-model")
            conv, answer = await ChatService(provider).chat(session, alice, "请解释这条复杂业务规则")
            count = len(requests)
            assert count >= 5, "acceptance exercises default five-stage pipeline"
            conv2, answer2 = await ChatService(provider).chat(session, alice, "请解释这条复杂业务规则")
            assert conv.id != conv2.id and answer2 == answer
            assert len(requests) == count, "same authorized default ChatService flow must reuse provider responses"
            eve = session.get(User, "eve")
            await ChatService(provider).chat(session, eve, "请解释这条复杂业务规则")
            assert len(requests) > count
    finally:
        set_embedding_override(None)


def authorization_scope(session, user_id="alice"):
    from app.core.response_cache import bind_cache_scope
    from app.services.cache_scope import AuthorizedCacheScope

    user = session.get(User, user_id)
    scope = AuthorizedCacheScope(session, user, user.tenant_id)
    return bind_cache_scope(scope.snapshot)


@pytest.mark.asyncio
@pytest.mark.parametrize("temperature", [float("nan"), float("inf"), -float("inf"), 1e308, -1.0, 0.7])
async def test_cache_acceptance_unsafe_numeric_llm_not_cached(cache_env, local_llm, temperature):
    url, requests, _ = local_llm
    provider = OpenAICompatibleProvider(url, "synthetic-local-test", "acceptance-model")
    messages = [ChatMessage(role=ChatRole.USER, content="数字边界")]
    options = LLMOptions(temperature=temperature)
    with Session(cache_env) as session, authorization_scope(session):
        for _ in range(2):
            try:
                await provider.chat(messages, options)
            except (ValueError, OverflowError):
                # HTTP JSON refuses NaN/Infinity; no cache should mask the failure.
                pass
    assert len(requests) in (0, 2), "invalid numeric requests must never be converted to warm-cache success"


@pytest.mark.asyncio
@pytest.mark.parametrize("control_key,value", [("model", "wrong-model"), ("finish", "length")])
async def test_cache_acceptance_response_identity_completion_rejected(cache_env, local_llm, control_key, value):
    url, requests, control = local_llm
    control[control_key] = value
    provider = OpenAICompatibleProvider(url, "synthetic-local-test", "acceptance-model")
    messages = [ChatMessage(role=ChatRole.USER, content="拒绝错误响应身份")]
    with Session(cache_env) as session, authorization_scope(session):
        await provider.chat(messages, LLMOptions(temperature=0))
        await provider.chat(messages, LLMOptions(temperature=0))
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_cache_acceptance_stream_done_and_cancel_boundaries(cache_env, local_llm):
    import httpx

    url, requests, control = local_llm
    provider = OpenAICompatibleProvider(url, "synthetic-local-test", "acceptance-model")
    messages = [ChatMessage(role=ChatRole.USER, content="stream lifecycle")]
    options = LLMOptions(temperature=0)
    with Session(cache_env) as session, authorization_scope(session):
        stream = provider.stream_chat(messages, options)
        assert await anext(stream)
        await stream.aclose()
        assert "".join([p async for p in provider.stream_chat(messages, options)])
        assert len(requests) == 2, "cancelled generator must not warm cache"
        assert "".join([p async for p in provider.stream_chat(messages, options)])
        assert len(requests) == 2, "completed stream should warm cache"
        control["done"] = False
        other = [ChatMessage(role=ChatRole.USER, content="missing DONE")]
        for _ in range(2):
            assert "".join([p async for p in provider.stream_chat(other, options)])
        assert len(requests) == 4, "EOF without protocol DONE must not cache"
        control["status"] = 503
        failed = [ChatMessage(role=ChatRole.USER, content="stream failure")]
        for _ in range(2):
            with pytest.raises(httpx.HTTPStatusError):
                _ = [p async for p in provider.stream_chat(failed, options)]
        assert len(requests) == 6, "failed streams must retry backend rather than use cache"


@pytest.mark.asyncio
async def test_cache_acceptance_fallback_binds_actual_provider(cache_env, local_llm):
    import httpx

    from app.llm.base import LLMProvider
    from app.llm.routing import FallbackChain

    class Unavailable(LLMProvider):
        model = "primary-offline"

        async def chat(self, messages, options=None):
            raise httpx.ConnectError("synthetic unavailable")

        async def stream_chat(self, messages, options=None):
            raise httpx.ConnectError("synthetic unavailable")
            yield ""  # pragma: no cover

    url, requests, _ = local_llm
    fallback = OpenAICompatibleProvider(url, "synthetic-local-test", "acceptance-model")
    chain = FallbackChain("chat", [("primary", Unavailable()), ("fallback", fallback)])
    messages = [ChatMessage(role=ChatRole.USER, content="actual fallback identity")]
    with Session(cache_env) as session, authorization_scope(session):
        first = await chain.chat(messages, LLMOptions(temperature=0))
        assert chain.model == "acceptance-model"
        assert await chain.chat(messages, LLMOptions(temperature=0)) == first
        assert len(requests) == 1


@pytest.mark.asyncio
async def test_cache_acceptance_index_identity_mutation_not_masked(cache_env):
    from app.rag.index_identity import IndexIdentityError

    with Session(cache_env) as session:
        rag, store, user = rag_for(session)
        await rag.ingest_text("索引身份专属内容。", "index", "synthetic", user.id)
        assert await rag.search("索引身份")
        rag._embedding.model = "different-model-same-dim"
        try:
            with pytest.raises(IndexIdentityError):
                await rag.search("索引身份")
        finally:
            rag._embedding.model = "acceptance-synthetic-v1"
        assert store.calls == 1, "identity mismatch is rejected before returning warm data"


@pytest.mark.asyncio
async def test_cache_acceptance_retrieval_copy_config_and_uploader(cache_env, monkeypatch):
    with Session(cache_env) as session:
        rag, store, user = rag_for(session, "bob")
        doc = await rag.ingest_text("归属变更价格 199 元。", "ownership", "synthetic", user.id)
        first = await rag.search("价格")
        first[0].content = "caller modification must not poison cache"
        assert "199" in (await rag.search("价格"))[0].content
        assert store.calls == 1
        monkeypatch.setattr(settings, "RAG_HYBRID_RRF_K", settings.RAG_HYBRID_RRF_K + 1)
        assert await rag.search("价格")
        assert store.calls == 2, "changed retrieval config must invalidate previous key"
        monkeypatch.setattr(settings, "RAG_KB_SCOPE", "uploader")
        with pytest.raises(PermissionError):
            await rag.search("价格")
        own_rag, own_store, _ = rag_for(session, "bob")
        assert await own_rag.search("价格")
        with Session(cache_env) as writer:
            changed = writer.get(Document, doc.id)
            changed.user_id = "alice"
            writer.add(changed)
            writer.commit()
        assert await own_rag.search("价格") == []
        assert own_store.calls == 2, "committed uploader change must invalidate hot result"


@pytest.mark.asyncio
async def test_cache_acceptance_cross_tenant_and_database_binding(cache_env, tmp_path):
    with Session(cache_env) as session:
        rag, store, user = rag_for(session)
        await rag.ingest_text("租户 A 机密。", "tenant-a", "synthetic", user.id)
        assert "租户 A" in (await rag.search("机密"))[0].content
        other, other_store, _ = rag_for(session, "eve")
        assert await other.search("机密") == []
        assert other_store.calls == 1
        assert store.calls == 1
    other_engine = create_engine(f"sqlite:///{tmp_path / 'other.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(other_engine)
    try:
        with Session(other_engine) as session:
            session.add(Tenant(id="a", name="A"))
            session.add(User(id="alice", tenant_id="a", username="alice", hashed_password="",
                             role=Role.SYSTEM_ADMIN.value))
            session.commit()
            isolated, isolated_store, _ = rag_for(session)
            assert await isolated.search("机密") == []
            assert isolated_store.calls == 1
    finally:
        other_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("vector", [
    [float("nan"), 0, 1], [float("inf"), 0, 1], [1e308, 1e308, 1e308], [0, 0, 0],
])
async def test_cache_acceptance_numeric_vector_change_never_hidden(cache_env, vector):
    with Session(cache_env) as session:
        rag, store, user = rag_for(session)
        await rag.ingest_text("数值污染旧结果。", "numbers", "synthetic", user.id)
        assert await rag.search("数值")
        with Session(cache_env) as writer:
            row = writer.exec(select(DocumentChunk)).first()
            row.embedding = json.dumps(vector)
            writer.add(row)
            writer.commit()
        try:
            await rag.search("数值")
        except PermissionError:
            # A fresh non-finite backend result is refused instead of serving cached data.
            pass
        assert store.calls == 2, "a cached result must not mask changed persisted vectors"


@pytest.mark.asyncio
async def test_cache_acceptance_http_failure_retry_context_model_endpoint_keys(cache_env, local_llm):
    import httpx

    url, requests, control = local_llm
    provider = OpenAICompatibleProvider(url, "synthetic-local-test", "acceptance-model")
    messages = [ChatMessage(role=ChatRole.USER, content="same prompt")]
    options = LLMOptions(temperature=0)
    with Session(cache_env) as session, authorization_scope(session):
        control["status"] = 503
        for _ in range(2):
            with pytest.raises(httpx.HTTPStatusError):
                await provider.chat(messages, options)
        assert len(requests) == 2
        control["status"] = 200
        await provider.chat(messages, options)
        await provider.chat(messages, options)
        assert len(requests) == 3
        await provider.chat([ChatMessage(role=ChatRole.SYSTEM, content="new policy"), *messages], options)
        assert len(requests) == 4
        provider.model = "changed-request-model"
        control["model"] = provider.model
        await provider.chat(messages, options)
        assert len(requests) == 5
        provider.api_key = "synthetic-local-other-principal"
        await provider.chat(messages, options)
        assert len(requests) == 6
        provider.base_url += "/another-deployment"
        await provider.chat(messages, options)
        assert len(requests) == 7


@pytest.mark.asyncio
async def test_cache_acceptance_llm_reauthorizes_before_warm_hit(cache_env, local_llm):
    url, requests, _ = local_llm
    provider = OpenAICompatibleProvider(url, "synthetic-local-test", "acceptance-model")
    messages = [ChatMessage(role=ChatRole.USER, content="revocation")]
    with Session(cache_env) as session, authorization_scope(session):
        await provider.chat(messages, LLMOptions(temperature=0))
        with Session(cache_env) as writer:
            user = writer.get(User, "alice")
            user.is_active = False
            writer.add(user)
            writer.commit()
        with pytest.raises(PermissionError):
            await provider.chat(messages, LLMOptions(temperature=0))
        assert len(requests) == 1, "revoked identity must not hit cache or issue replacement request"


@pytest.mark.asyncio
async def test_cache_acceptance_chatservice_stream_cancel_then_reenter(cache_env, local_llm, monkeypatch):
    import app.core.database as database

    url, requests, _ = local_llm
    # The existing cancellation persistence explicitly opens the module database.
    monkeypatch.setattr(database, "engine", cache_env)
    monkeypatch.setattr(settings, "RAG_ENABLED", False)
    monkeypatch.setattr(settings, "AGENT_ORCHESTRATION", "langgraph")
    provider = OpenAICompatibleProvider(url, "synthetic-local-test", "acceptance-model")
    with Session(cache_env) as session:
        user = session.get(User, "alice")
        service = ChatService(provider)
        stream = service.chat_stream(session, user, "你好")
        async for event in stream:
            if event.type == "token":
                break
        await stream.aclose()
        async for _ in service.chat_stream(session, user, "你好"):
            pass
        assert len(requests) == 2, "cancelled ChatService request cannot cache unconsumed stream"
        async for _ in service.chat_stream(session, user, "你好"):
            pass
        assert len(requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("name,value", [
    ("CACHE_TTL_SECONDS", float("nan")), ("CACHE_TTL_SECONDS", float("inf")),
    ("CACHE_TTL_SECONDS", -1), ("CACHE_TTL_SECONDS", 1e308),
    ("CACHE_MAX_ENTRIES", 0), ("CACHE_MAX_ENTRIES", True),
    ("CACHE_MAX_BYTES", -1), ("CACHE_MAX_ENTRY_BYTES", 9 * 1024 * 1024),
])
async def test_cache_acceptance_bad_limits_bypass_actual_vector(cache_env, monkeypatch, name, value):
    monkeypatch.setattr(settings, name, value)
    with Session(cache_env) as session:
        rag, store, user = rag_for(session)
        await rag.ingest_text("非法配置价格。", "bad-limit", "synthetic", user.id)
        assert await rag.search("价格")
        assert await rag.search("价格")
        assert store.calls == 2


@pytest.mark.asyncio
async def test_cache_acceptance_ttl_expiry_and_disable(cache_env, monkeypatch):
    monkeypatch.setattr(settings, "CACHE_TTL_SECONDS", 0.1)
    with Session(cache_env) as session:
        rag, store, user = rag_for(session)
        await rag.ingest_text("缓存到期价格。", "ttl", "synthetic", user.id)
        assert await rag.search("价格")
        await asyncio.sleep(0.2)
        assert await rag.search("价格")
        assert store.calls == 2
        monkeypatch.setattr(settings, "RETRIEVAL_CACHE_ENABLED", False)
        assert await rag.search("价格")
        assert store.calls == 3


@pytest.mark.asyncio
async def test_cache_acceptance_failed_reparse_preserves_actual_old_read(cache_env, monkeypatch):
    with Session(cache_env) as session:
        rag, store, user = rag_for(session)
        doc = await rag.ingest_text("旧读路径价格 199 元。", "rollback", "synthetic", user.id)
        old_hits = await rag.search("价格")
        original_embed = rag._embedding.embed

        async def fail_embed(texts):
            raise TimeoutError("synthetic reparse outage")

        monkeypatch.setattr(rag._embedding, "embed", fail_embed)
        with pytest.raises(TimeoutError):
            await rag.reindex_document_in_place(
                doc, ParsedDocument(text="失败候选 299 元。", title="rollback", source="synthetic",
                                    extension="txt", content_type="text/plain"), content_hash="candidate-failed"
            )
        monkeypatch.setattr(rag._embedding, "embed", original_embed)
        monkeypatch.setattr(settings, "RETRIEVAL_CACHE_ENABLED", False)
        with Session(cache_env) as fresh:
            recovered, _, _ = rag_for(fresh)
            actual = await recovered.search("价格")
            assert [h.id for h in actual] == [h.id for h in old_hits]
            assert "199" in actual[0].content and "299" not in actual[0].content


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_cache_acceptance_mutable_request_during_transport_not_wrong_key(
    cache_env, local_llm, monkeypatch, streaming
):
    import httpx

    url, requests, control = local_llm
    control["echo_content"] = True
    provider = OpenAICompatibleProvider(url, "synthetic-local-test", "acceptance-model")
    messages = [ChatMessage(role=ChatRole.USER, content="original-context")]
    options = LLMOptions(temperature=0, max_tokens=30)
    enter = httpx.AsyncClient.__aenter__
    mutated = False

    async def mutate_after_enter(client):
        nonlocal mutated
        result = await enter(client)
        if not mutated:
            mutated = True
            messages[0].content = "mutated-context"
            options.temperature = 0.7
            options.max_tokens = 7
            provider.model = "mutated-model"
            provider.base_url = url + "/mutated-deployment"
            provider.api_key = "synthetic-mutated-key"
        return result

    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", mutate_after_enter)

    async def invoke():
        if streaming:
            return "".join([part async for part in provider.stream_chat(messages, options)])
        return await provider.chat(messages, options)

    with Session(cache_env) as session, authorization_scope(session):
        first = await invoke()
        actual = requests[0]
        assert first == f"{actual['messages'][-1]['content']}|{actual['temperature']}|{actual['model']}"
        messages[0].content = "original-context"
        options.temperature = 0.0
        options.max_tokens = 30
        provider.model = "acceptance-model"
        provider.base_url = url
        provider.api_key = "synthetic-local-test"
        second = await invoke()
        assert second == "original-context|0.0|acceptance-model", (
            "response from mutated actual HTTP payload cannot be replayed under original context/model key"
        )

