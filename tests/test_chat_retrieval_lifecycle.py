"""真实 ChatService 与临时 SQLite 验证检索依赖生命周期；合成模型不证明质量。"""

import json
import uuid
from datetime import timedelta

import pytest
from sqlmodel import Session, SQLModel, col, create_engine, select

import app.rag.service as rag_module
from app.agents.prompts import SYSTEM_ACT, SYSTEM_PREFLOW
from app.core.config import settings
from app.llm.mock import MockLLMProvider
from app.models.conversation import Message
from app.models.user import Tenant, User
from app.rag.embeddings.mock import MockEmbeddingProvider
from app.rag.retrieval_status import RetrievalStatus
from app.services.chat_service import ChatService


class LifecycleModel(MockLLMProvider):
    def __init__(self, *, full=False, tool=False):
        super().__init__()
        self.full = full
        self.tool = tool
        self.actions = 0
        self.systems = []
        self.inputs = []

    async def chat(self, messages, options=None):
        system = messages[0].content
        self.systems.append(system)
        self.inputs.append(str(messages[-1].content))
        if system == SYSTEM_PREFLOW:
            return "YES" if self.full else "NO"
        if system.startswith(SYSTEM_ACT) and self.tool:
            self.actions += 1
            if self.actions == 1:
                return '<tool_call>{"name":"calculator","arguments":{"expression":"1+1"}}</tool_call>'
            return "计算结果为 2"
        from app.agents.prompts import SYSTEM_PLAN

        if system == SYSTEM_PLAN:
            return await super().chat(messages, options)
        return "隔离生命周期回答"


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    for key, value in {
        "RAG_ENABLED": True, "RAG_BACKEND": "native", "RAG_VECTOR_STORE": "local",
        "MEMORY_ENABLED": False, "SKILL_ENABLED": False, "MCP_ENABLED": False,
        "AGENT_QUALITY_GATE_ENABLED": False, "SECURITY_RATE_LIMIT": False,
        "LLM_RESPONSE_CACHE_ENABLED": False, "RETRIEVAL_CACHE_ENABLED": False,
    }.items():
        if hasattr(settings, key):
            monkeypatch.setattr(settings, key, value)
    monkeypatch.setattr(rag_module, "get_embedding_provider", lambda: MockEmbeddingProvider(dim=64))
    engine = create_engine(f"sqlite:///{tmp_path / 'lifecycle.db'}")
    SQLModel.metadata.create_all(engine)
    identity = uuid.uuid4().hex
    actor = User(id=identity, tenant_id=identity, username=identity, hashed_password="")
    with Session(engine) as session:
        session.add(Tenant(id=identity, name=identity))
        session.add(actor)
        session.commit()
        session.refresh(actor)
        session.expunge(actor)
    yield engine, actor
    engine.dispose()


async def invoke(engine, actor, model, message, streaming):
    service = ChatService(model)
    events = []
    with Session(engine) as session:
        if streaming:
            events = [event async for event in service.chat_stream(session, actor, message)]
            result = session.info.get("agent_message_id")
            assert result
            user_message = session.get(Message, result)
            conversation_id = user_message.conversation_id
        else:
            conv, _reply = await service.chat(session, actor, message)
            conversation_id = conv.id
    with Session(engine) as fresh:
        messages = list(fresh.exec(select(Message).where(col(Message.conversation_id) == conversation_id)).all())
        answers = [item for item in messages if item.role == "assistant"]
        assert len(messages) == 2 and len(answers) == 1
        return answers[0], events


@pytest.mark.parametrize("factory", ["get_embedding_provider", "get_vector_store", "get_rag_backend"])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("route", ["self_direct", "fast_simple", "fast_tools"])
async def test_nonretrieval_service_never_builds_dependencies(lifecycle, monkeypatch, factory, streaming, route):
    engine, actor = lifecycle
    monkeypatch.setattr(settings, "AGENT_ORCHESTRATION", "self" if route == "self_direct" else "langgraph")
    calls = []

    def broken(*args, **kwargs):
        calls.append(factory)
        raise RuntimeError("private-dependency-detail")

    monkeypatch.setattr(rag_module, factory, broken)
    model = LifecycleModel(tool=route == "fast_tools")
    answer, events = await invoke(engine, actor, model, "算一下 1+1" if model.tool else "你好", streaming)
    assert calls == []
    assert answer.content and answer.status == "complete" and json.loads(answer.sources or "[]") == []
    assert settings.RAG_UNAVAILABLE_REPLY not in answer.content
    if model.tool:
        assert model.actions == 2
        assert any("2" in text and "calculator" in text for text in model.inputs)
    if streaming:
        assert not any(event.type == "error" for event in events)


@pytest.mark.parametrize("factory", ["get_embedding_provider", "get_vector_store", "get_rag_backend"])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("route", ["self_full", "fast_rag", "supervisor"])
async def test_needed_dependency_failure_is_disclosed_and_persisted(lifecycle, monkeypatch, factory, streaming, route):
    engine, actor = lifecycle
    monkeypatch.setattr(settings, "AGENT_ORCHESTRATION", "self" if route == "self_full" else "langgraph")
    calls = []

    def broken(*args, **kwargs):
        calls.append(factory)
        raise RuntimeError("private-dependency-detail")

    monkeypatch.setattr(rag_module, factory, broken)
    message = "先调研多轮任务" if route == "supervisor" else "根据知识库回答年假"
    answer, _events = await invoke(engine, actor, LifecycleModel(full=True), message, streaming)
    assert calls == [factory]
    assert answer.content.startswith(settings.RAG_UNAVAILABLE_REPLY)
    assert "private-dependency-detail" not in answer.content
    assert json.loads(answer.sources or "[]") == []


async def test_metadata_does_not_initialize_and_success_is_request_local(lifecycle, monkeypatch):
    engine, actor = lifecycle
    calls = []
    original = rag_module.get_embedding_provider

    def counted():
        calls.append("embedding")
        return original()

    monkeypatch.setattr(rag_module, "get_embedding_provider", counted)
    with Session(engine) as session:
        first = ChatService(LifecycleModel())._build_retriever(session, actor)
        assert first.last_hits == [] and first.last_selected is None
        first.last_selected = []
        assert calls == []
        await first.retrieve("年假", "")
        await first.retrieve("年假", "")
        assert calls == ["embedding"]
        assert first.last_outcome.status is RetrievalStatus.NO_HIT
        second = ChatService(LifecycleModel())._build_retriever(session, actor)
        assert calls == ["embedding"]
        await second.retrieve("年假", "")
        assert calls == ["embedding", "embedding"]


async def test_failed_factory_is_bounded_and_does_not_log_private_text(lifecycle, monkeypatch, caplog):
    engine, actor = lifecycle
    calls = []

    def broken():
        calls.append(1)
        raise RuntimeError("private-lazy-secret-marker")

    monkeypatch.setattr(rag_module, "get_embedding_provider", broken)
    with Session(engine) as session:
        hook = ChatService(LifecycleModel())._build_retriever(session, actor)
        assert not calls
        assert await hook.retrieve("年假", "") == ""
        hook.last_selected = []
        assert await hook.retrieve("年假", "") == ""
        assert calls == [1]
        assert hook.last_hits == [] and hook.last_selected == []
        assert hook.last_outcome.status is RetrievalStatus.UNAVAILABLE
        monkeypatch.setattr(rag_module, "get_embedding_provider", lambda: MockEmbeddingProvider(dim=64))
        next_request = ChatService(LifecycleModel())._build_retriever(session, actor)
        assert await next_request.retrieve("年假", "") == ""
        assert next_request.last_outcome.status is RetrievalStatus.NO_HIT
    assert "private-lazy-secret-marker" not in caplog.text


def test_rag_disabled_does_not_touch_factories(lifecycle, monkeypatch):
    engine, actor = lifecycle
    monkeypatch.setattr(settings, "RAG_ENABLED", False)

    def broken():
        raise AssertionError("disabled RAG must not construct dependencies")

    monkeypatch.setattr(rag_module, "get_embedding_provider", broken)
    with Session(engine) as session:
        assert ChatService(LifecycleModel())._build_retriever(session, actor) is None


@pytest.mark.parametrize("streaming", [False, True])
async def test_supervisor_missing_extra_falls_back_without_eager_dependencies(lifecycle, monkeypatch, streaming):
    import builtins

    engine, actor = lifecycle
    monkeypatch.setattr(settings, "AGENT_ORCHESTRATION", "langgraph")
    original_import = builtins.__import__

    def no_supervisor(name, *args, **kwargs):
        if name == "app.agents.supervisor":
            raise ImportError("synthetic missing optional extra")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_supervisor)
    calls = []

    def broken():
        calls.append(1)
        raise RuntimeError("synthetic dependency failure")

    monkeypatch.setattr(rag_module, "get_embedding_provider", broken)
    answer, _events = await invoke(engine, actor, LifecycleModel(full=False), "先调研多轮任务", streaming)
    assert answer.content and calls == []


async def test_cache_enabled_uses_original_identity_and_reuses_constructed_dependencies(lifecycle, monkeypatch):
    engine, actor = lifecycle
    monkeypatch.setattr(settings, "RETRIEVAL_CACHE_ENABLED", True)
    monkeypatch.setattr(settings, "RAG_KB_SCOPE", "uploader")
    calls = []
    original = rag_module.get_embedding_provider

    def counted():
        calls.append(1)
        return original()

    monkeypatch.setattr(rag_module, "get_embedding_provider", counted)
    with Session(engine) as session:
        hook = ChatService(LifecycleModel())._build_retriever(session, actor)
        assert calls == []
        await hook.retrieve("年假", "")
        await hook.retrieve("年假", "")
        assert calls == [1]
        assert hook.last_outcome.status is RetrievalStatus.NO_HIT


def test_invalid_read_scope_is_rejected_before_lazy_factory(lifecycle, monkeypatch):
    import app.services.chat_service as chat_module
    from app.rag.access import ReadScope

    engine, actor = lifecycle
    monkeypatch.setattr(chat_module, "read_scope_for", lambda user: ReadScope("foreign-tenant"))
    calls = []
    monkeypatch.setattr(rag_module, "get_embedding_provider", lambda: calls.append(1))
    with Session(engine) as session:
        with pytest.raises(ValueError, match="主体租户不一致"):
            ChatService(LifecycleModel())._build_retriever(session, actor)
    assert calls == []


@pytest.mark.parametrize("streaming", [False, True])
async def test_cancellation_before_retrieval_constructs_nothing(lifecycle, monkeypatch, streaming):
    import asyncio

    engine, actor = lifecycle
    monkeypatch.setattr(settings, "AGENT_ORCHESTRATION", "self")
    calls = []
    monkeypatch.setattr(rag_module, "get_embedding_provider", lambda: calls.append(1))

    class CancelBeforeRetrieval(LifecycleModel):
        async def chat(self, messages, options=None):
            if messages[0].content == SYSTEM_PREFLOW:
                raise asyncio.CancelledError()
            return await super().chat(messages, options)

    with Session(engine) as session:
        service = ChatService(CancelBeforeRetrieval())
        with pytest.raises(asyncio.CancelledError):
            if streaming:
                _events = [event async for event in service.chat_stream(session, actor, "你好")]
            else:
                await service.chat(session, actor, "你好")
    assert calls == []
    with Session(engine) as fresh:
        answers = list(fresh.exec(select(Message).where(col(Message.role) == "assistant")).all())
        # 既有流式取消会写入 stopped 记录；这不是成功助手回复。
        assert all(item.status == "stopped" and json.loads(item.sources or "[]") == [] for item in answers)
        assert all(settings.RAG_UNAVAILABLE_REPLY not in item.content for item in answers)


async def test_regenerate_direct_reply_stays_independent_of_unused_rag(lifecycle, monkeypatch):
    engine, actor = lifecycle
    monkeypatch.setattr(settings, "AGENT_ORCHESTRATION", "self")
    service = ChatService(LifecycleModel())
    with Session(engine) as session:
        conv, original_text = await service.chat(session, actor, "你好")
        conversation_id = conv.id
    with Session(engine) as fresh:
        original = fresh.exec(select(Message).where(col(Message.role) == "assistant")).one()
        question = fresh.exec(select(Message).where(col(Message.role) == "user")).one()
        original.created_at = question.created_at + timedelta(microseconds=1)
        fresh.add(original)
        fresh.commit()
        assert question.created_at < original.created_at
        original_id = original.id

    def broken():
        raise AssertionError("regeneration direct path must not use retrieval")

    monkeypatch.setattr(rag_module, "get_embedding_provider", broken)
    with Session(engine) as session:
        await service.regenerate(session, actor, conversation_id, original_id)
    with Session(engine) as fresh:
        rows = list(fresh.exec(select(Message)).all())
        answers = [item for item in rows if item.role == "assistant"]
        assert len(rows) == 4 and len(answers) == 2
        assert fresh.get(Message, original_id).content == original_text
        assert all(json.loads(item.sources or "[]") == [] for item in answers)


@pytest.mark.parametrize("streaming", [False, True])
async def test_resume_direct_state_does_not_construct_dependencies(lifecycle, monkeypatch, streaming):
    from app.agents.pipeline import AgentState
    from app.agents.state import StateManager
    from app.models.conversation import Conversation
    from app.services.agent_state_service import AgentStateService

    engine, actor = lifecycle
    monkeypatch.setattr(settings, "AGENT_ORCHESTRATION", "self")
    monkeypatch.setattr(settings, "AGENT_STATE_PERSIST_ENABLED", True)
    with Session(engine) as db:
        conv = Conversation(tenant_id=actor.tenant_id, user_id=actor.id)
        db.add(conv)
        db.flush()
        message = Message(conversation_id=conv.id, role="user", content="你好")
        db.add(message)
        db.commit()
        conversation_id, message_id = conv.id, message.id
        state = AgentState(user_input="你好", needs_full_pipeline=False)
        AgentStateService(db, tenant_id=actor.tenant_id, user_id=actor.id).save_checkpoint(
            conversation_id=conversation_id, message_id=message_id, tenant_id=actor.tenant_id, user_id=actor.id,
            stage="action", round_no=0, tool_budget_exhausted=False,
            state_json=StateManager(state).to_json(), status="running", error=None,
        )

    def broken():
        raise AssertionError("resumed direct path must not use retrieval")

    monkeypatch.setattr(rag_module, "get_embedding_provider", broken)
    with Session(engine) as db:
        service = ChatService(LifecycleModel())
        kwargs = {"conversation_id": conversation_id, "resume_message_id": message_id}
        if streaming:
            _events = [event async for event in service.chat_stream(db, actor, "你好", **kwargs)]
        else:
            await service.chat(db, actor, "你好", **kwargs)
    with Session(engine) as fresh:
        rows = list(fresh.exec(select(Message)).all())
        assert len(rows) == 2
        assert fresh.get(Message, message_id).role == "user"
        assert len([item for item in rows if item.role == "assistant"]) == 1


@pytest.mark.parametrize("similarity", [float("nan"), float("inf"), -float("inf"), 1e308, 0.0])
async def test_numeric_boundary_keeps_original_retrieval_semantics(lifecycle, monkeypatch, similarity):
    from app.rag.retriever import HybridRetriever
    from app.rag.service import RAGService
    from app.rag.vectorstore.base import ChunkResult

    engine, actor = lifecycle

    class NumericBackend:
        name = "synthetic-numeric"

        async def retrieve(self, query, **kwargs):
            assert kwargs["tenant_id"] == actor.tenant_id
            return [ChunkResult("owned-chunk", "数值合成资料", "synthetic", "owned-doc", 1.0,
                                similarity=similarity)]

    monkeypatch.setattr(rag_module, "get_rag_backend", lambda *args, **kwargs: NumericBackend())
    with Session(engine) as session:
        eager: HybridRetriever = RAGService(session, actor.tenant_id, reader=actor).make_retriever()
        lazy = ChatService(LifecycleModel())._build_retriever(session, actor)
        expected = await eager.retrieve("资料", "")
        assert await lazy.retrieve("资料", "") == expected
        assert lazy.last_outcome.status is eager.last_outcome.status
        assert [hit.id for hit in lazy.last_hits] == [hit.id for hit in eager.last_hits]


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("route", ["self_full", "fast_rag", "supervisor"])
async def test_real_empty_and_unregistered_index_contract(lifecycle, monkeypatch, legacy, streaming, route):
    from app.models.rag import Document, DocumentChunk, EmbeddingIndex

    engine, actor = lifecycle
    monkeypatch.setattr(settings, "AGENT_ORCHESTRATION", "self" if route == "self_full" else "langgraph")
    if legacy:
        with Session(engine) as db:
            document = Document(tenant_id=actor.tenant_id, user_id=actor.id, title="PRIVATE_UNREGISTERED")
            db.add(document)
            db.flush()
            db.add(DocumentChunk(tenant_id=actor.tenant_id, document_id=document.id,
                                 content="PRIVATE_UNREGISTERED_BODY", embedding="[1,0]"))
            db.commit()
    model = LifecycleModel(full=True)
    message = "先调研多轮任务" if route == "supervisor" else "根据知识库回答年假"
    answer, _events = await invoke(engine, actor, model, message, streaming)
    notice = settings.RAG_UNAVAILABLE_REPLY if legacy else settings.RAG_NO_HIT_REPLY
    assert answer.content.startswith(notice)
    assert json.loads(answer.sources or "[]") == []
    assert "PRIVATE_UNREGISTERED_BODY" not in answer.content + "\n".join(model.inputs)
    with Session(engine) as fresh:
        assert fresh.exec(select(EmbeddingIndex)).first() is None
        if legacy:
            assert fresh.exec(select(DocumentChunk)).one().index_id is None


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("route", ["self_full", "fast_rag", "supervisor"])
async def test_low_score_remains_terminal_refusal(lifecycle, monkeypatch, streaming, route):
    from app.rag.service import RAGService

    engine, actor = lifecycle
    monkeypatch.setattr(settings, "AGENT_ORCHESTRATION", "self" if route == "self_full" else "langgraph")
    monkeypatch.setattr(settings, "RAG_MIN_SIMILARITY", 1.0)
    with Session(engine) as db:
        rag = RAGService(db, actor.tenant_id, embedding_provider=MockEmbeddingProvider(dim=64))
        await rag.ingest_text("独有年假政策zz", "synthetic-policy", "synthetic-policy", actor.id)
    message = "先调研多轮火星任务" if route == "supervisor" else "根据知识库回答火星探测"
    answer, _events = await invoke(engine, actor, LifecycleModel(full=True), message, streaming)
    assert answer.content == settings.RAG_REFUSE_MESSAGE
    assert json.loads(answer.sources or "[]") == []
