"""Real ChatService, isolated SQL retrieval/index and calculator; synthetic model, not quality evidence."""

import base64
import json
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlmodel import Session, SQLModel, col, create_engine, select

import app.rag.service as rag_module
from app.agents.tools.builtin import calculator
from app.agents.tools.executor import ToolExecutor
from app.audit.models import AuditLog
from app.core import log_context
from app.core.config import settings
from app.llm.mock import MockLLMProvider
from app.models.conversation import Message
from app.models.user import Tenant, User
from app.rag.embeddings.mock import MockEmbeddingProvider
from app.rag.service import RAGService
from app.services.agent_execution_guard import ResumeRejectedError
from app.services.chat_attachments import AttachmentInput
from app.services.chat_service import ChatService, RegenerationFailedError

POLICY = "知识库差旅报销金额计算标准：住宿每人每日300元；餐补每人每日80元。"
QUESTION = "根据知识库报销标准计算差旅金额，1人3天"


class EvidenceModel(MockLLMProvider):
    """Return known fixture facts only after seeing real, budget-selected retrieval."""

    def __init__(self):
        super().__init__()
        self.retriever = None
        self.calls = []
        self.before_extract = None
        self.invalid = False

    async def chat(self, messages, options=None):
        self.calls.append(messages)
        assert self.retriever is not None
        hits = self.retriever.last_selected
        assert hits, "Extraction must wait for budget-selected evidence."
        hit = next(hit for hit in hits if "每人每日300元" in hit.content)
        body = str(messages[-1].content)
        assert "每人每日300元" in body and hit.id in body
        if self.before_extract:
            self.before_extract()
        if self.invalid:
            return '{"schema_version":1,"facts":[],"expression":"9999"}'
        facts = []
        for name, quote, value in [("lodging", "每人每日300元", 300), ("meal", "每人每日80元", 80)]:
            start = hit.content.index(quote)
            facts.append(dict(id=name, source="chunk", chunk_id=hit.id, start=start,
                              end=start + len(quote), quote=quote, value=value, unit="CNY/person/day"))
        for name, quote, value, unit in [("people", "1人", 1, "person"), ("days", "3天", 3, "day")]:
            start = QUESTION.index(quote)
            facts.append(dict(id=name, source="user", chunk_id=None, start=start,
                              end=start + len(quote), quote=quote, value=value, unit=unit))
        return json.dumps(dict(schema_version=1, facts=facts, expression="(lodging+meal)*people*days"))

    async def stream_chat(self, messages, options=None):
        raise AssertionError("Verified composition must not ask a model to invent a final answer.")
        yield ""  # pragma: no cover


@pytest.fixture
def composition(tmp_path, monkeypatch):
    for name, value in {
        "RAG_ENABLED": True, "RAG_BACKEND": "native", "RAG_VECTOR_STORE": "local",
        "MEMORY_ENABLED": False, "SKILL_ENABLED": False, "MCP_ENABLED": False,
        "EVOLUTION_ENABLED": False, "SECURITY_RATE_LIMIT": False,
        "AGENT_QUALITY_GATE_ENABLED": False, "AGENT_STATE_PERSIST_ENABLED": False,
        "LLM_RESPONSE_CACHE_ENABLED": False, "RETRIEVAL_CACHE_ENABLED": False,
    }.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(rag_module, "get_embedding_provider", lambda: MockEmbeddingProvider(dim=64))
    engine = create_engine(f"sqlite:///{tmp_path / 'composition.db'}")
    SQLModel.metadata.create_all(engine)
    actor = User(id=uuid.uuid4().hex, tenant_id=uuid.uuid4().hex,
                 username=uuid.uuid4().hex, hashed_password="")
    with Session(engine) as db:
        db.add(Tenant(id=actor.tenant_id, name=actor.tenant_id))
        db.add(actor)
        db.commit()
        db.refresh(actor)
        db.expunge(actor)
    monkeypatch.setattr("app.audit.logger.engine", engine)
    monkeypatch.setattr("app.core.database.engine", engine)
    log_context.set_user(actor.id, actor.tenant_id)
    yield engine, actor
    log_context.clear()
    engine.dispose()


async def ingest(engine, actor):
    with Session(engine) as db:
        service = RAGService(db, actor.tenant_id, embedding_provider=MockEmbeddingProvider(dim=64))
        return await service.ingest_text(POLICY, "synthetic-expense-policy", "synthetic-expense-policy", actor.id)


def wired_model(monkeypatch):
    model = EvidenceModel()
    service = ChatService(model)
    build = service._build_retriever

    def capture(db, actor):
        model.retriever = build(db, actor)
        return model.retriever

    monkeypatch.setattr(service, "_build_retriever", capture)
    return service, model


async def invoke(engine, actor, service, streaming, question=QUESTION):
    events = []
    with Session(engine) as db:
        if streaming:
            events = [event async for event in service.chat_stream(db, actor, question)]
            conversation_id = next(event.data for event in events if event.type == "conversation")
        else:
            conversation, _ = await service.chat(db, actor, question)
            conversation_id = conversation.id
    with Session(engine) as fresh:
        messages = fresh.exec(select(Message).where(col(Message.conversation_id) == conversation_id)).all()
        answer = next(message for message in messages if message.role == "assistant")
        audit = fresh.exec(select(AuditLog).where(col(AuditLog.tenant_id) == actor.tenant_id)).all()
        return answer, events, list(audit), list(messages)


def allowed_tools(audit):
    return [row for row in audit if row.action == "tool_call" and json.loads(row.details)["allowed"]]


@pytest.mark.parametrize("orchestration", ["self", "langgraph"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_real_retrieval_precedes_real_calculator_and_persisted_sources(
    composition, monkeypatch, orchestration, streaming,
):
    engine, actor = composition
    monkeypatch.setattr(settings, "AGENT_ORCHESTRATION", orchestration)
    document = await ingest(engine, actor)
    service, model = wired_model(monkeypatch)
    executed = []
    original = ToolExecutor.execute

    async def recording(executor, call):
        assert model.retriever.last_selected and len(model.calls) == 1
        assert call.name == "calculator"
        result = await original(executor, call)
        executed.append((call, result))
        return result

    monkeypatch.setattr(ToolExecutor, "execute", recording)
    answer, events, audit, _ = await invoke(engine, actor, service, streaming)
    assert len(executed) == len(model.calls) == 1
    assert executed[0][1].status == "ok"
    assert Decimal(await calculator(executed[0][0].arguments)) == Decimal(1140)
    assert "1140.00" in answer.content and "9999" not in answer.content
    sources = json.loads(answer.sources)
    assert sources and all(source["document_id"] == document.id for source in sources)
    assert allowed_tools(audit)
    assert answer.status == "complete"
    if streaming:
        assert "".join(event.data for event in events if event.type == "token") == answer.content
        assert len([event for event in events if event.type == "tool.call"]) == 1
        assert len([event for event in events if event.type == "done"]) == 1


@pytest.mark.parametrize("failure", ["no_hit", "unavailable", "below_threshold", "disabled", "invalid"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_missing_or_unusable_policy_never_executes_tool(composition, monkeypatch, failure, streaming):
    engine, actor = composition
    monkeypatch.setattr(settings, "AGENT_ORCHESTRATION", "langgraph")
    if failure != "no_hit":
        await ingest(engine, actor)
    if failure == "unavailable":
        def broken():
            raise RuntimeError("PRIVATE_PROVIDER_FAILURE")
        monkeypatch.setattr(rag_module, "get_embedding_provider", broken)
    if failure == "below_threshold":
        monkeypatch.setattr(settings, "RAG_MIN_SIMILARITY", 1.0)
    if failure == "disabled":
        monkeypatch.setattr(settings, "RAG_ENABLED", False)
    service, model = wired_model(monkeypatch)
    model.invalid = failure == "invalid"

    async def forbidden(*args, **kwargs):
        raise AssertionError("Missing verified policy must prevent all tool execution.")

    monkeypatch.setattr(ToolExecutor, "execute", forbidden)
    answer, _, audit, _ = await invoke(engine, actor, service, streaming)
    assert "1140" not in answer.content and "9999" not in answer.content
    assert "PRIVATE_PROVIDER_FAILURE" not in answer.content
    assert not allowed_tools(audit)
    assert len(model.calls) == int(failure == "invalid")


@pytest.mark.parametrize("streaming", [False, True])
async def test_current_actor_revocation_after_extraction_prevents_execution(composition, monkeypatch, streaming):
    engine, actor = composition
    await ingest(engine, actor)
    service, model = wired_model(monkeypatch)

    def revoke():
        with Session(engine) as fresh:
            current = fresh.get(User, actor.id)
            current.token_version += 1
            fresh.add(current)
            fresh.commit()

    model.before_extract = revoke
    answer, _, audit, _ = await invoke(engine, actor, service, streaming)
    assert "1140" not in answer.content
    assert not allowed_tools(audit)


async def test_other_tenant_policy_does_not_supply_any_parameters(composition, monkeypatch):
    engine, actor = composition
    other = User(id=uuid.uuid4().hex, tenant_id=uuid.uuid4().hex,
                 username=uuid.uuid4().hex, hashed_password="")
    with Session(engine) as db:
        db.add(Tenant(id=other.tenant_id, name=other.tenant_id))
        db.add(other)
        db.commit()
        db.refresh(other)
        db.expunge(other)
    await ingest(engine, other)
    service, model = wired_model(monkeypatch)
    answer, _, audit, _ = await invoke(engine, actor, service, False)
    assert not model.calls
    assert not model.retriever.last_selected
    assert json.loads(answer.sources or "[]") == []
    assert "300" not in answer.content and "80" not in answer.content
    assert not allowed_tools(audit)


async def test_regeneration_keeps_original_on_failed_parameter_validation(composition, monkeypatch):
    engine, actor = composition
    await ingest(engine, actor)
    service, model = wired_model(monkeypatch)
    answer, _, _, messages = await invoke(engine, actor, service, False)
    # Stable fixture order; preserve the known separate equal-timestamp ordering issue.
    with Session(engine) as db:
        original = db.get(Message, answer.id)
        question = next(message for message in messages if message.role == "user")
        question.created_at -= timedelta(seconds=2)
        original.created_at = question.created_at + timedelta(seconds=1)
        db.add(question)
        db.add(original)
        db.commit()
    model.invalid = True
    with Session(engine) as db:
        with pytest.raises(RegenerationFailedError):
            await service.regenerate(db, actor, answer.conversation_id, answer.id)
    with Session(engine) as fresh:
        assert fresh.get(Message, answer.id).content == answer.content
        assert len(fresh.exec(select(Message).where(col(Message.role) == "assistant")).all()) == 1


async def test_combination_does_not_create_a_replayable_v1_checkpoint(composition, monkeypatch):
    engine, actor = composition
    await ingest(engine, actor)
    service, _ = wired_model(monkeypatch)
    answer, _, _, messages = await invoke(engine, actor, service, False)
    user_message = next(message for message in messages if message.role == "user")
    with Session(engine) as db:
        with pytest.raises(ResumeRejectedError):
            await service.chat(db, actor, QUESTION, answer.conversation_id, resume_message_id=user_message.id)


async def test_database_revocation_after_visible_call_has_denial_audit(composition, monkeypatch):
    engine, actor = composition
    await ingest(engine, actor)
    service, _ = wired_model(monkeypatch)
    saw_call = False
    events = []
    with Session(engine) as db:
        iterator = service.chat_stream(db, actor, QUESTION)
        try:
            async for event in iterator:
                events.append(event)
                if event.type == "tool.call":
                    saw_call = True
                    with Session(engine) as fresh:
                        current = fresh.get(User, actor.id)
                        current.token_version += 1
                        fresh.add(current)
                        fresh.commit()
        finally:
            await iterator.aclose()
    assert saw_call
    assert "1140" not in "".join(event.data for event in events if event.type == "token")
    with Session(engine) as fresh:
        audit = fresh.exec(select(AuditLog).where(col(AuditLog.action) == "tool_call")).all()
        assert len(audit) == 1
        assert json.loads(audit[0].details)["allowed"] is False
        assert json.loads(audit[0].details)["reason"] == "authority_changed"


@pytest.mark.parametrize("streaming", [False, True])
async def test_attachment_cannot_supply_missing_user_parameters(composition, monkeypatch, streaming):
    engine, actor = composition
    await ingest(engine, actor)
    service, model = wired_model(monkeypatch)
    # This malicious synthetic extraction returns the normal 1-person/3-day
    # payload even though those facts only exist in the attachment, not the goal.
    question = "根据知识库报销标准计算差旅金额"
    attachment = AttachmentInput(name="trip.md", content_base64=base64.b64encode("1人3天".encode()).decode())
    with Session(engine) as db:
        if streaming:
            events = [e async for e in service.chat_stream(db, actor, question, attachments=[attachment])]
            reply = next(e.data for e in events if e.type == "done")
        else:
            _, reply = await service.chat(db, actor, question, attachments=[attachment])
    assert len(model.calls) == 1
    assert json.loads(model.calls[0][-1].content)["user_text"] == question
    assert "1140" not in reply
    with Session(engine) as fresh:
        assert not allowed_tools(fresh.exec(select(AuditLog)).all())


async def test_incompatible_embedding_identity_cannot_be_used_for_calculation(composition, monkeypatch):
    engine, actor = composition
    await ingest(engine, actor)
    other = MockEmbeddingProvider(dim=64)
    other.model = "different-synthetic-model"
    monkeypatch.setattr(rag_module, "get_embedding_provider", lambda: other)
    service, model = wired_model(monkeypatch)
    answer, _, audit, _ = await invoke(engine, actor, service, False)
    assert not model.calls
    assert not allowed_tools(audit)
    assert "1140" not in answer.content and json.loads(answer.sources or "[]") == []


async def test_debug_trace_records_actual_model_and_tool_without_business_payload(composition, monkeypatch):
    from app.debug.trace import AgentTrace

    engine, actor = composition
    await ingest(engine, actor)
    service, _ = wired_model(monkeypatch)
    trace = AgentTrace(debug_mode=True)
    monkeypatch.setattr(service, "_create_trace", lambda: trace)
    answer, _, _, _ = await invoke(engine, actor, service, False)
    assert "1140.00" in answer.content
    model_events = [event for event in trace.events if event.type == "llm_call"]
    tool_events = [event for event in trace.events if event.type == "tool_call"]
    assert len(model_events) == len(tool_events) == 1
    assert tool_events[0].name == "calculator" and tool_events[0].data["status"] == "ok"
    serialized = json.dumps([event.to_dict() for event in trace.events], ensure_ascii=False)
    assert "每人每日300元" not in serialized and "lodging" not in serialized


@pytest.mark.parametrize("orchestration", ["self", "langgraph"])
async def test_outer_service_close_at_call_stops_before_execution(composition, monkeypatch, orchestration):
    engine, actor = composition
    monkeypatch.setattr(settings, "AGENT_ORCHESTRATION", orchestration)
    await ingest(engine, actor)
    service, _ = wired_model(monkeypatch)
    with Session(engine) as db:
        iterator = service.chat_stream(db, actor, QUESTION)
        try:
            async for event in iterator:
                if event.type == "tool.call":
                    break
            else:
                pytest.fail("Expected an authorized proposed calculator call.")
        finally:
            await iterator.aclose()
    with Session(engine) as fresh:
        answers = fresh.exec(select(Message).where(col(Message.role) == "assistant")).all()
        assert answers == []  # No output: cancellation must not create an empty assistant message.
        assert not allowed_tools(fresh.exec(select(AuditLog)).all())


async def test_new_service_requests_never_reuse_old_tool_execution(composition, monkeypatch):
    engine, actor = composition
    monkeypatch.setattr(settings, "LLM_RESPONSE_CACHE_ENABLED", True)
    await ingest(engine, actor)
    service, model = wired_model(monkeypatch)
    first, _, _, _ = await invoke(engine, actor, service, False)
    second, _, _, _ = await invoke(engine, actor, service, False)
    assert "1140.00" in first.content and "1140.00" in second.content
    assert first.conversation_id != second.conversation_id
    assert len(model.calls) == 2  # Extraction deliberately bypasses caching.
    with Session(engine) as fresh:
        assert len(allowed_tools(fresh.exec(select(AuditLog)).all())) == 2


async def test_successful_regeneration_preserves_both_rounds_and_sources(composition, monkeypatch):
    engine, actor = composition
    await ingest(engine, actor)
    service, model = wired_model(monkeypatch)
    answer, _, _, messages = await invoke(engine, actor, service, False)
    with Session(engine) as db:
        question = next(message for message in messages if message.role == "user")
        question.created_at -= timedelta(seconds=2)
        original = db.get(Message, answer.id)
        original.created_at = question.created_at + timedelta(seconds=1)
        db.add(question)
        db.add(original)
        db.commit()
        _, result = await service.regenerate(db, actor, answer.conversation_id, answer.id)
        assert "1140.00" in result
    with Session(engine) as fresh:
        answers = fresh.exec(select(Message).where(col(Message.role) == "assistant")).all()
        assert len(answers) == 2 and len(model.calls) == 2
        assert all("1140.00" in message.content and json.loads(message.sources) for message in answers)
        assert len(allowed_tools(fresh.exec(select(AuditLog)).all())) == 2
