"""开关打开时，反思应留下可查询、待审的技能候选或待办。"""

from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.agents.pipeline import AgentState
from app.core.config import Settings, settings
from app.evolution.candidates import (
    EvolutionCandidateError,
    accept_todo_candidate,
    list_candidates,
    missing_candidate_reason,
    promote_skill_candidate,
    reject_candidate,
)
from app.evolution.models import DistillResult, ReflectResult, SkillSuggestion
from app.evolution.records import EvolutionCandidate
from app.evolution.store import (
    distill_and_record,
    ensure_evolution_tables,
    list_reflect_runs,
    reflect_and_record,
)
from app.llm.base import ChatMessage, LLMOptions, LLMProvider
from app.models.conversation import Conversation, Message
from app.models.skill import Skill
from app.models.user import Tenant, User
from app.services.chat_service import ChatService
from app.services.skill_service import confirm_skill_draft, manifests_for_chat


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


REFLECT_WITH_CANDIDATES = """{
  "summary": "需要补技能并跟进待办",
  "improvements": [
    {
      "severity": "high",
      "category": "skill",
      "summary": "缺少预告价格技能",
      "detail": "没有说明未生效文档的用法",
      "suggestion": "增加查询预告价格的技能步骤",
      "affected_skill": "future_price"
    }
  ],
  "action_items": [
    {
      "description": "核对 2027 年标准套餐预告价",
      "priority": "high",
      "assignee_hint": "客服",
      "deadline_hint": "本周"
    }
  ]
}"""

REFLECT_WITHOUT_CANDIDATES = """{
  "summary": "对话质量良好",
  "improvements": [
    {
      "severity": "low",
      "category": "clarity",
      "summary": "表述可更简短",
      "detail": "有一句略长",
      "suggestion": "下次再收紧措辞"
    }
  ],
  "action_items": []
}"""

DISTILL_JSON = """{
  "summary": "这批对话发现技能缺口",
  "insights": [
    {
      "severity": "high",
      "category": "skill",
      "summary": "来源核验不足",
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


def _seed(session: Session) -> User:
    session.add(Tenant(id="tenant-1", name="t1"))
    session.add(Tenant(id="tenant-2", name="t2"))
    user = User(
        id="user-1",
        tenant_id="tenant-1",
        username="u1",
        hashed_password="x",
        role="member",
    )
    session.add(user)
    session.add(
        User(
            id="user-2",
            tenant_id="tenant-2",
            username="u2",
            hashed_password="x",
            role="member",
        )
    )
    session.add(
        User(
            id="admin-1",
            tenant_id="tenant-1",
            username="admin",
            hashed_password="x",
            role="system_admin",
        )
    )
    conv = Conversation(id="conv-1", tenant_id="tenant-1", user_id="user-1")
    session.add(conv)
    session.add(Message(conversation_id=conv.id, role="user", content="2027 年标准套餐多少钱？"))
    session.add(Message(conversation_id=conv.id, role="assistant", content="现在是 199 元。"))
    session.commit()
    session.refresh(conv)
    _ = conv.messages
    return user


def test_production_defaults_still_disable_evolution() -> None:
    assert Settings.model_fields["EVOLUTION_ENABLED"].default is False
    assert Settings.model_fields["EVOLUTION_DISTILL_ENABLED"].default is False


def test_disabled_conversation_creates_no_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine()
    llm = MockLLM(REFLECT_WITH_CANDIDATES)
    monkeypatch.setattr(settings, "EVOLUTION_ENABLED", False)
    monkeypatch.setattr(settings, "EVOLUTION_REFLECT_ENABLED", True)
    monkeypatch.setattr("app.evolution.store.default_engine", engine)
    service = ChatService(llm_provider=llm)
    with Session(engine) as session:
        user = _seed(session)
        conv = session.get(Conversation, "conv-1")
        assert conv is not None
        service._maybe_reflect(conv, AgentState(user_input="2027 年标准套餐多少钱？"))
        assert list_reflect_runs(session) == []
        assert list_candidates(session, user) == []
        assert session.exec(select(Skill)).all() == []
    assert llm.calls == 0
    engine.dispose()


def test_enabled_conversation_persists_skill_and_todo_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine()
    llm = MockLLM(REFLECT_WITH_CANDIDATES)
    monkeypatch.setattr(settings, "EVOLUTION_ENABLED", True)
    monkeypatch.setattr(settings, "EVOLUTION_REFLECT_ENABLED", True)
    monkeypatch.setattr(settings, "EVOLUTION_REFLECT_ASYNC", False)
    monkeypatch.setattr("app.evolution.store.default_engine", engine)
    service = ChatService(llm_provider=llm)
    with Session(engine) as session:
        user = _seed(session)
        conv = session.get(Conversation, "conv-1")
        assert conv is not None
        service._maybe_reflect(conv, AgentState(user_input="2027 年标准套餐多少钱？"))
        rows = list_candidates(session, user)
        kinds = sorted(row.kind for row in rows)
        assert kinds == ["skill", "todo"]
        assert all(row.status == "pending" for row in rows)
        assert all(row.source_type == "reflect" for row in rows)
        assert {row.title for row in rows} == {"future_price", "核对 2027 年标准套餐预告价"}
        assert session.exec(select(Skill)).all() == []
        other = session.get(User, "user-2")
        assert other is not None
        assert list_candidates(session, other) == []
    assert llm.calls == 1
    engine.dispose()


@pytest.mark.asyncio
async def test_reflect_without_skill_or_todo_explains_skip() -> None:
    engine = _engine()
    llm = MockLLM(REFLECT_WITHOUT_CANDIDATES)
    result = await reflect_and_record(
        "用户：你好\n助手：你好",
        llm=llm,
        conversation_id="conv-plain",
        tenant_id="tenant-1",
        bind=engine,
    )
    assert missing_candidate_reason(result) == "反思未提取到技能建议或待办"
    with Session(engine) as session:
        assert session.exec(select(EvolutionCandidate)).all() == []
    engine.dispose()


@pytest.mark.asyncio
async def test_failed_reflect_does_not_create_candidates() -> None:
    engine = _engine()
    llm = MockLLM("not valid json")
    result = await reflect_and_record(
        "用户：你好\n助手：你好",
        llm=llm,
        conversation_id="conv-err",
        bind=engine,
    )
    assert result.error is not None
    assert "反思未成功" in (missing_candidate_reason(result) or "")
    with Session(engine) as session:
        assert session.exec(select(EvolutionCandidate)).all() == []
    engine.dispose()


@pytest.mark.asyncio
async def test_distill_skill_suggestions_become_pending_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine()
    llm = MockLLM(DISTILL_JSON)
    monkeypatch.setattr(settings, "EVOLUTION_DISTILL_ENABLED", True)
    with Session(engine) as session:
        _seed(session)
    row = await distill_and_record(llm=llm, bind=engine, respect_schedule=False, trigger="test")
    assert row is not None
    assert row.status == "succeeded"
    with Session(engine) as session:
        member = session.get(User, "user-1")
        admin = session.get(User, "admin-1")
        assert member is not None and admin is not None
        assert list_candidates(session, member) == []
        rows = list_candidates(session, admin, kind="skill")
        assert len(rows) == 1
        assert rows[0].title == "source_check"
        assert rows[0].status == "pending"
        assert rows[0].tenant_id is None
        assert session.exec(select(Skill)).all() == []
    engine.dispose()


@pytest.mark.asyncio
async def test_accepting_todo_does_not_change_skills(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine()
    llm = MockLLM(REFLECT_WITH_CANDIDATES)
    with Session(engine) as session:
        _seed(session)
    result = await reflect_and_record(
        "用户：2027 年标准套餐多少钱？\n助手：现在是 199 元。",
        llm=llm,
        conversation_id="conv-1",
        tenant_id="tenant-1",
        bind=engine,
    )
    assert missing_candidate_reason(result) is None
    with Session(engine) as session:
        user = session.get(User, "user-1")
        assert user is not None
        todos = list_candidates(session, user, kind="todo")
        assert len(todos) == 1
        accepted = accept_todo_candidate(session, user, todos[0].id, note="已登记跟进")
        assert accepted.status == "accepted"
        assert accepted.reviewed_by == user.id
        assert session.exec(select(Skill)).all() == []
        with pytest.raises(EvolutionCandidateError) as exc:
            accept_todo_candidate(session, user, accepted.id)
        assert exc.value.status_code == 409
    engine.dispose()


@pytest.mark.asyncio
async def test_promote_creates_inactive_draft_until_confirm() -> None:
    engine = _engine()
    llm = MockLLM(REFLECT_WITH_CANDIDATES)
    with Session(engine) as session:
        user = _seed(session)
    await reflect_and_record(
        "用户：2027 年标准套餐多少钱？\n助手：现在是 199 元。",
        llm=llm,
        conversation_id="conv-1",
        tenant_id="tenant-1",
        bind=engine,
    )
    with Session(engine) as session:
        user = session.get(User, "user-1")
        assert user is not None
        skills = list_candidates(session, user, kind="skill")
        assert len(skills) == 1
        promoted = promote_skill_candidate(session, user, skills[0].id)
        assert promoted.status == "promoted"
        assert promoted.skill_draft_id is not None
        draft = session.get(Skill, promoted.skill_draft_id)
        assert draft is not None
        assert draft.lifecycle == "draft"
        assert draft.enabled is False
        assert draft.name == "future_price"
        assert "evolution-candidate:" in draft.source_text
        assert "future_price" not in {item.name for item in manifests_for_chat(session, user)}
        confirm_skill_draft(session, user, draft.id)
        session.refresh(draft)
        assert draft.lifecycle == "active"
        assert draft.enabled is True
        names = {item.name for item in manifests_for_chat(session, user)}
        assert "future_price" in names
    engine.dispose()


@pytest.mark.asyncio
async def test_reject_candidate_does_not_create_skill() -> None:
    engine = _engine()
    llm = MockLLM(REFLECT_WITH_CANDIDATES)
    with Session(engine) as session:
        _seed(session)
    await reflect_and_record(
        "用户：2027 年标准套餐多少钱？\n助手：现在是 199 元。",
        llm=llm,
        conversation_id="conv-1",
        tenant_id="tenant-1",
        bind=engine,
    )
    with Session(engine) as session:
        user = session.get(User, "user-1")
        other = session.get(User, "user-2")
        assert user is not None and other is not None
        skill = list_candidates(session, user, kind="skill")[0]
        with pytest.raises(EvolutionCandidateError) as hidden:
            reject_candidate(session, other, skill.id)
        assert hidden.value.status_code == 404
        rejected = reject_candidate(session, user, skill.id, note="暂不采纳")
        assert rejected.status == "rejected"
        assert session.exec(select(Skill)).all() == []
    engine.dispose()


def test_missing_reason_for_empty_distill() -> None:
    result = DistillResult(summary="无可提炼建议")
    assert missing_candidate_reason(result) == "蒸馏未提取到技能建议"
    filled = DistillResult(skill_suggestions=[SkillSuggestion(skill_name="n", description="d")])
    assert missing_candidate_reason(filled) is None
    failed = ReflectResult(error="模型超时")
    assert missing_candidate_reason(failed) == "反思未成功，未提取候选：模型超时"
