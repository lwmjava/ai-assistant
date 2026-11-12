"""Skill effect evaluation should persist records and retire poorly performing skills with rollback."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.config import Settings, settings
from app.evolution.records import EvolutionReflectRun
from app.evolution.skill_eval import (
    SkillRetirementError,
    evaluate_skills,
    list_skill_evaluations,
    list_skill_retirements,
    rollback_skill_retirement,
)
from app.evolution.store import ensure_evolution_tables
from app.models.conversation import Conversation, Message
from app.models.skill import Skill
from app.models.user import Tenant, User
from app.services.skill_service import manifests_for_chat


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    ensure_evolution_tables(engine)
    return engine


def _add_skill(
    session: Session,
    *,
    name: str = "billing_guide",
    lifecycle: str = "active",
    enabled: bool = True,
) -> Skill:
    row = Skill(
        id="skill-billing",
        name=name,
        name_key=f"private:tenant-1:user-1:{name}",
        owner_id="user-1",
        tenant_id="tenant-1",
        scope="private",
        enabled=enabled,
        lifecycle=lifecycle,
        description="Explain public prices",
        constraints="Only public prices",
        system_prompt="Answer with the current public price list",
        example="How much is the standard plan",
        keywords='["plan"]',
    )
    session.add(row)
    return row


def _seed_user(session: Session) -> User:
    session.add(Tenant(id="tenant-1", name="t1"))
    user = User(
        id="user-1",
        tenant_id="tenant-1",
        username="u1",
        hashed_password="x",
        role="member",
    )
    session.add(user)
    return user


def _seed_conversation(
    session: Session,
    *,
    conv_id: str,
    skill_name: str | None,
    created_at: datetime,
) -> Conversation:
    conv = Conversation(id=conv_id, tenant_id="tenant-1", user_id="user-1", created_at=created_at)
    session.add(conv)
    session.add(
        Message(
            conversation_id=conv.id,
            role="user",
            content="How much is the standard plan?",
            created_at=created_at,
        )
    )
    names = f'["{skill_name}"]' if skill_name else None
    session.add(
        Message(
            conversation_id=conv.id,
            role="assistant",
            content="It is 199 now.",
            skill_names=names,
            created_at=created_at + timedelta(seconds=1),
        )
    )
    return conv


def _add_reflect(
    session: Session,
    *,
    conv_id: str,
    started_at: datetime,
    quality_score: float,
    severity: str,
    category: str = "skill",
    affected_skill: str | None = "billing_guide",
    run_id: str | None = None,
) -> EvolutionReflectRun:
    improvement: dict[str, Any] = {
        "severity": severity,
        "category": category,
        "summary": "Price answer is wrong",
        "detail": "Cited a stale price",
        "suggestion": "Check the current price list",
        "affected_skill": affected_skill,
    }
    payload = {
        "conversation_id": conv_id,
        "summary": "Skill-related issue",
        "improvements": [improvement],
        "action_items": [],
        "quality_score": quality_score,
        "revision_count": 0,
        "error": None,
    }
    import json

    row = EvolutionReflectRun(
        id=run_id or conv_id.replace("conv", "reflect"),
        conversation_id=conv_id,
        tenant_id="tenant-1",
        status="succeeded",
        summary="Skill-related issue",
        quality_score=quality_score,
        revision_count=0,
        improvement_count=1,
        action_item_count=0,
        critical_count=1 if severity == "critical" else 0,
        llm_call_count=1,
        budget_max_llm_calls=1,
        result_json=json.dumps(payload, ensure_ascii=False),
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=2),
        duration_ms=2000,
    )
    session.add(row)
    return row


def test_production_defaults_keep_evolution_disabled() -> None:
    """Production defaults keep evolution off, so retirement is not auto-applied."""
    assert Settings.model_fields["EVOLUTION_ENABLED"].default is False
    assert Settings.model_fields["EVOLUTION_DISTILL_ENABLED"].default is False


def test_low_quality_is_recorded_but_not_retired_when_switch_off(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine()
    monkeypatch.setattr(settings, "EVOLUTION_ENABLED", False)
    now = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
    with Session(engine) as session:
        user = _seed_user(session)
        skill = _add_skill(session)
        for index in range(3):
            conv_id = f"conv-{index}"
            started = now - timedelta(hours=1, minutes=index)
            _seed_conversation(session, conv_id=conv_id, skill_name=skill.name, created_at=started)
            _add_reflect(
                session,
                conv_id=conv_id,
                started_at=started + timedelta(minutes=2),
                quality_score=0.2,
                severity="critical",
            )
        session.commit()
        rows = evaluate_skills(session=session, now=now, apply_retirements=None)
        session.commit()
        assert len(rows) == 1
        row = rows[0]
        assert row.decision == "retire"
        assert row.applied is False
        assert row.activation_count == 3
        assert row.reflect_count == 3
        session.refresh(skill)
        assert skill.enabled is True
        assert skill.lifecycle == "active"
        assert session.exec(select(Skill)).all()[0].id == skill.id
        assert list_skill_retirements(session) == []
        stored = list_skill_evaluations(session, skill_name="billing_guide")
        assert len(stored) == 1
        names = {item.name for item in manifests_for_chat(session, user)}
        assert "billing_guide" in names
    engine.dispose()


def test_low_quality_skill_is_retired_and_can_be_rolled_back() -> None:
    engine = _engine()
    now = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
    with Session(engine) as session:
        user = _seed_user(session)
        skill = _add_skill(session)
        for index in range(3):
            conv_id = f"conv-{index}"
            started = now - timedelta(hours=1, minutes=index)
            _seed_conversation(session, conv_id=conv_id, skill_name=skill.name, created_at=started)
            _add_reflect(
                session,
                conv_id=conv_id,
                started_at=started + timedelta(minutes=2),
                quality_score=0.2,
                severity="critical",
            )
        session.commit()
        rows = evaluate_skills(session=session, now=now, apply_retirements=True)
        session.commit()
        assert len(rows) == 1
        row = rows[0]
        assert row.decision == "retire"
        assert row.applied is True
        session.refresh(skill)
        assert skill.enabled is False
        assert skill.lifecycle == "retired"
        remaining = session.exec(select(Skill)).all()
        assert len(remaining) == 1
        assert remaining[0].id == skill.id
        names = {item.name for item in manifests_for_chat(session, user)}
        assert "billing_guide" not in names
        retirements = list_skill_retirements(session, skill_id=skill.id, status="retired")
        assert len(retirements) == 1
        rolled = rollback_skill_retirement(session, retirements[0].id)
        assert rolled.status == "rolled_back"
        session.refresh(skill)
        assert skill.enabled is True
        assert skill.lifecycle == "active"
        names = {item.name for item in manifests_for_chat(session, user)}
        assert "billing_guide" in names
        again = rollback_skill_retirement(session, retirements[0].id)
        assert again.status == "rolled_back"
        with pytest.raises(SkillRetirementError) as exc:
            rollback_skill_retirement(session, "missing-id")
        assert exc.value.status_code == 404
    engine.dispose()


def test_insufficient_samples_keep_skill_enabled() -> None:
    engine = _engine()
    now = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
    with Session(engine) as session:
        _seed_user(session)
        skill = _add_skill(session)
        started = now - timedelta(hours=1)
        _seed_conversation(session, conv_id="conv-1", skill_name=skill.name, created_at=started)
        _add_reflect(
            session,
            conv_id="conv-1",
            started_at=started + timedelta(minutes=2),
            quality_score=0.1,
            severity="critical",
        )
        session.commit()
        rows = evaluate_skills(session=session, now=now, apply_retirements=True)
        session.commit()
        assert len(rows) == 1
        assert rows[0].decision == "insufficient_data"
        assert rows[0].applied is False
        session.refresh(skill)
        assert skill.enabled is True
        assert skill.lifecycle == "active"
        assert list_skill_retirements(session) == []
    engine.dispose()


def test_high_quality_skill_is_kept() -> None:
    engine = _engine()
    now = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
    with Session(engine) as session:
        _seed_user(session)
        skill = _add_skill(session)
        for index in range(3):
            conv_id = f"conv-{index}"
            started = now - timedelta(hours=1, minutes=index)
            _seed_conversation(session, conv_id=conv_id, skill_name=skill.name, created_at=started)
            _add_reflect(
                session,
                conv_id=conv_id,
                started_at=started + timedelta(minutes=2),
                quality_score=0.9,
                severity="low",
                category="clarity",
                affected_skill=None,
            )
        session.commit()
        rows = evaluate_skills(session=session, now=now, apply_retirements=True)
        session.commit()
        assert len(rows) == 1
        assert rows[0].decision == "keep"
        assert rows[0].applied is False
        session.refresh(skill)
        assert skill.enabled is True
        assert skill.lifecycle == "active"
    engine.dispose()


def test_unresolved_skill_name_is_not_deleted() -> None:
    engine = _engine()
    now = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
    with Session(engine) as session:
        _seed_user(session)
        for index in range(3):
            conv_id = f"conv-{index}"
            started = now - timedelta(hours=1, minutes=index)
            _seed_conversation(session, conv_id=conv_id, skill_name="translator", created_at=started)
            _add_reflect(
                session,
                conv_id=conv_id,
                started_at=started + timedelta(minutes=2),
                quality_score=0.1,
                severity="critical",
                affected_skill="translator",
            )
        session.commit()
        rows = evaluate_skills(session=session, now=now, apply_retirements=True)
        session.commit()
        assert len(rows) == 1
        assert rows[0].decision == "retire"
        assert rows[0].applied is False
        assert rows[0].skill_id is None
        assert list_skill_retirements(session) == []
        assert session.exec(select(Skill)).all() == []
    engine.dispose()
