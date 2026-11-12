"""开关打开时，反思与蒸馏应留下可查询运行记录。"""

from datetime import UTC, datetime

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.agents.pipeline import AgentState
from app.core.config import Settings, settings
from app.evolution.models import DistillResult, SkillSuggestion
from app.evolution.store import (
    distill_and_record,
    ensure_evolution_tables,
    list_distill_runs,
    list_reflect_runs,
    reflect_and_record,
)
from app.llm.base import ChatMessage, LLMOptions, LLMProvider
from app.models.conversation import Conversation, Message
from app.models.user import Tenant, User
from app.services.chat_service import ChatService


class MockLLM(LLMProvider):
    """返回预设响应的模型，测试中不发起真实调用。"""

    def __init__(self, response: str = "") -> None:
        self._response = response
        self.calls = 0

    @property
    def model(self) -> str:
        return "mock"

    async def chat(self, messages: list[ChatMessage], options: LLMOptions | None = None) -> str:
        self.calls += 1
        return self._response

    async def stream_chat(self, messages: list[ChatMessage], options: LLMOptions | None = None):
        self.calls += 1
        for char in self._response:
            yield char


VALID_REFLECT_JSON = """{
  "summary": "对话质量良好，但有一处事实错误",
  "improvements": [
    {
      "severity": "high",
      "category": "accuracy",
      "summary": "事实错误",
      "detail": "回答了错误的数据",
      "suggestion": "核实数据来源"
    }
  ],
  "action_items": []
}"""

VALID_DISTILL_JSON = """{
  "summary": "这批对话发现一个共性问题",
  "insights": [
    {
      "severity": "high",
      "category": "accuracy",
      "summary": "事实核验不足",
      "detail": "多轮回答缺少来源",
      "suggestion": "先核对资料再作答",
      "frequency": 2
    }
  ],
  "skill_suggestions": [
    {
      "skill_name": "source_check",
      "action": "create",
      "description": "核验来源",
      "triggers": ["来源"],
      "prompt_injection": "请先给出依据"
    }
  ]
}"""


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    ensure_evolution_tables(engine)
    return engine


def _seed_conversation(session: Session) -> Conversation:
    session.add(Tenant(id="tenant-1", name="t1"))
    session.add(
        User(
            id="user-1",
            tenant_id="tenant-1",
            username="u1",
            hashed_password="x",
            role="member",
        )
    )
    conv = Conversation(id="conv-1", tenant_id="tenant-1", user_id="user-1")
    session.add(conv)
    session.add(Message(conversation_id=conv.id, role="user", content="标准套餐多少钱？"))
    session.add(Message(conversation_id=conv.id, role="assistant", content="现在是 199 元。"))
    session.commit()
    session.refresh(conv)
    _ = conv.messages
    return conv


def test_production_defaults_keep_evolution_disabled() -> None:
    """生产默认仍关闭总开关和夜间蒸馏，不能把默认关闭当成已完成。"""
    assert Settings.model_fields["EVOLUTION_ENABLED"].default is False
    assert Settings.model_fields["EVOLUTION_DISTILL_ENABLED"].default is False
    assert Settings.model_fields["EVOLUTION_REFLECT_BUDGET_MAX_LLM_CALLS"].default == 1
    assert Settings.model_fields["EVOLUTION_DISTILL_BUDGET_MAX_LLM_CALLS"].default == 1


def test_disabled_conversation_creates_no_reflect_record(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine()
    llm = MockLLM(VALID_REFLECT_JSON)
    monkeypatch.setattr(settings, "EVOLUTION_ENABLED", False)
    monkeypatch.setattr(settings, "EVOLUTION_REFLECT_ENABLED", True)
    monkeypatch.setattr("app.evolution.store.default_engine", engine)
    service = ChatService(llm_provider=llm)
    with Session(engine) as session:
        conv = _seed_conversation(session)
        service._maybe_reflect(conv, AgentState(user_input="标准套餐多少钱？"))
        assert list_reflect_runs(session) == []
    assert llm.calls == 0
    engine.dispose()


def test_enabled_conversation_persists_reflect_run(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine()
    llm = MockLLM(VALID_REFLECT_JSON)
    monkeypatch.setattr(settings, "EVOLUTION_ENABLED", True)
    monkeypatch.setattr(settings, "EVOLUTION_REFLECT_ENABLED", True)
    monkeypatch.setattr(settings, "EVOLUTION_REFLECT_ASYNC", False)
    monkeypatch.setattr("app.evolution.store.default_engine", engine)
    service = ChatService(llm_provider=llm)
    with Session(engine) as session:
        conv = _seed_conversation(session)
        service._maybe_reflect(conv, AgentState(user_input="标准套餐多少钱？", quality_score=0.8, revision=1))
        rows = list_reflect_runs(session, "conv-1")
        assert len(rows) == 1
        row = rows[0]
        assert row.status == "succeeded"
        assert row.tenant_id == "tenant-1"
        assert row.improvement_count == 1
        assert row.llm_call_count == 1
        assert "事实错误" in row.result_json
        assert row.error is None
    assert llm.calls == 1
    engine.dispose()


@pytest.mark.asyncio
async def test_reflect_records_model_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine()
    llm = MockLLM("not valid json at all")
    result = await reflect_and_record(
        "用户：你好\n助手：你好",
        llm=llm,
        conversation_id="conv-err",
        bind=engine,
    )
    assert result.error is not None
    with Session(engine) as session:
        rows = list_reflect_runs(session, "conv-err")
        assert len(rows) == 1
        assert rows[0].status == "failed"
        assert rows[0].error is not None
    engine.dispose()


@pytest.mark.asyncio
async def test_reflect_budget_skips_model_but_writes_record(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine()
    llm = MockLLM(VALID_REFLECT_JSON)
    monkeypatch.setattr(settings, "EVOLUTION_REFLECT_BUDGET_MAX_LLM_CALLS", 0)
    result = await reflect_and_record(
        "用户：你好\n助手：你好",
        llm=llm,
        conversation_id="conv-budget",
        bind=engine,
    )
    assert result.error == "反思预算已用尽"
    assert llm.calls == 0
    with Session(engine) as session:
        row = list_reflect_runs(session, "conv-budget")[0]
        assert row.status == "skipped"
        assert row.budget_capped is True
        assert row.llm_call_count == 0
    engine.dispose()


@pytest.mark.asyncio
async def test_disabled_distill_creates_no_record(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine()
    llm = MockLLM(VALID_DISTILL_JSON)
    monkeypatch.setattr(settings, "EVOLUTION_DISTILL_ENABLED", False)
    row = await distill_and_record(llm=llm, bind=engine, respect_schedule=False, trigger="test")
    assert row is None
    assert llm.calls == 0
    with Session(engine) as session:
        assert list_distill_runs(session) == []
    engine.dispose()


@pytest.mark.asyncio
async def test_configured_distill_persists_run(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine()
    llm = MockLLM(VALID_DISTILL_JSON)
    monkeypatch.setattr(settings, "EVOLUTION_DISTILL_ENABLED", True)
    now = datetime(2026, 1, 1, 3, 0, tzinfo=UTC)
    with Session(engine) as session:
        _seed_conversation(session)
    row = await distill_and_record(
        llm=llm,
        bind=engine,
        now=now,
        respect_schedule=True,
        trigger="scheduler",
    )
    assert row is not None
    assert row.status == "succeeded"
    assert row.conversations_analyzed == 1
    assert row.insight_count == 1
    assert row.skill_suggestion_count == 1
    assert row.llm_call_count == 1
    assert row.finished_at is not None
    assert "source_check" in row.result_json
    assert llm.calls == 1
    # 记录只保存建议内容，不会写入技能表
    from app.models.skill import Skill

    with Session(engine) as session:
        assert session.exec(select(Skill)).all() == []
        stored = list_distill_runs(session)
        assert len(stored) == 1
        assert stored[0].id == row.id
    engine.dispose()


@pytest.mark.asyncio
async def test_distill_outside_window_creates_no_record(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine()
    llm = MockLLM(VALID_DISTILL_JSON)
    monkeypatch.setattr(settings, "EVOLUTION_DISTILL_ENABLED", True)
    now = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    row = await distill_and_record(llm=llm, bind=engine, now=now, respect_schedule=True)
    assert row is None
    assert llm.calls == 0
    with Session(engine) as session:
        assert list_distill_runs(session) == []
    engine.dispose()


@pytest.mark.asyncio
async def test_distill_budget_skips_model_but_writes_record(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine()
    llm = MockLLM(VALID_DISTILL_JSON)
    monkeypatch.setattr(settings, "EVOLUTION_DISTILL_ENABLED", True)
    monkeypatch.setattr(settings, "EVOLUTION_DISTILL_BUDGET_MAX_LLM_CALLS", 0)
    row = await distill_and_record(llm=llm, bind=engine, respect_schedule=False, trigger="test")
    assert row is not None
    assert row.status == "skipped"
    assert row.budget_capped is True
    assert row.llm_call_count == 0
    assert llm.calls == 0
    engine.dispose()


def test_distill_result_to_dict_keeps_suggestions_as_data() -> None:
    result = DistillResult(
        conversations_analyzed=2,
        summary="ok",
        skill_suggestions=[
            SkillSuggestion(skill_name="source_check", action="create", description="核验来源"),
        ],
    )
    payload = result.to_dict()
    assert payload["skill_suggestions"][0]["skill_name"] == "source_check"
    assert payload["error"] is None
