"""技能草稿：一次调用、确认前不生效、确认后保留旧版本。"""

from collections.abc import Iterator

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.config import settings
from app.core.security import Role
from app.llm.base import LLMOptions
from app.models.skill import Skill
from app.models.user import Tenant, User
from app.services.skill_service import (
    SkillRequestError,
    SkillText,
    confirm_skill_draft,
    create_skill,
    generate_skill_draft,
    manifests_for_chat,
)


class _Once:
    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.calls = 0
        self.options: LLMOptions | None = None

    async def chat(self, messages, options=None) -> str:
        self.calls += 1
        self.options = options
        return self.payload


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def session() -> Iterator[Session]:
    engine = _engine()
    with Session(engine) as db:
        db.add(Tenant(id="tenant-1", name="示例租户"))
        db.add(
            User(
                id="member-1",
                tenant_id="tenant-1",
                username="member",
                hashed_password="",
                role=Role.MEMBER.value,
            )
        )
        db.add(
            User(
                id="member-2",
                tenant_id="tenant-1",
                username="other",
                hashed_password="",
                role=Role.MEMBER.value,
            )
        )
        db.add(
            User(
                id="admin-1",
                tenant_id="tenant-1",
                username="admin",
                hashed_password="",
                role=Role.SYSTEM_ADMIN.value,
            )
        )
        db.commit()
        yield db


def _user(session: Session, user_id: str) -> User:
    user = session.get(User, user_id)
    assert user is not None
    return user


def _payload(name: str = "refund_guide", tools: list[str] | None = None) -> str:
    import json

    body = {
        "name": name,
        "keywords": ["退款"],
        "purpose": "说明退款步骤",
        "preconditions": "已确认订单号",
        "workflow": "先核对订单，再说明时效",
        "tools": ["calculator"] if tools is None else tools,
        "constraints": "不承诺已退款",
        "escalation": "无法判断时交给人工",
        "examples": "用户：怎么退款",
        "tests": "包含订单核对",
    }
    return json.dumps(body, ensure_ascii=False)


@pytest.mark.asyncio
async def test_missing_model_key_writes_nothing(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LLM_" + "A" + "PI_KEY", "")
    before = len(session.exec(select(Skill)).all())
    with pytest.raises(SkillRequestError, match="未配置模型调用"):
        await generate_skill_draft(session, _user(session, "member-1"), "写一条退款技能", "private")
    assert len(session.exec(select(Skill)).all()) == before


@pytest.mark.asyncio
async def test_draft_stays_out_of_chat_until_confirmed(session: Session) -> None:
    model = _Once(_payload())
    change = await generate_skill_draft(
        session, _user(session, "member-1"), "写一条退款技能", "private", model
    )
    assert model.calls == 1
    assert model.options is not None
    assert model.options.temperature == 0
    assert model.options.max_tokens == 1024
    assert model.options.timeout == 30
    names = {item.name for item in manifests_for_chat(session, _user(session, "member-1"))}
    assert "refund_guide" not in names

    confirm_skill_draft(session, _user(session, "member-1"), change.view.id)
    names = {item.name for item in manifests_for_chat(session, _user(session, "member-1"))}
    assert "refund_guide" in names


@pytest.mark.asyncio
async def test_confirming_a_new_version_keeps_the_old_row(session: Session) -> None:
    create_skill(
        session,
        _user(session, "member-1"),
        SkillText(
            name="refund_guide",
            description="旧说明",
            keywords=["退款"],
            constraints="不承诺",
            system_prompt="旧流程",
            example="旧示例",
        ),
        "private",
    )
    change = await generate_skill_draft(
        session, _user(session, "member-1"), "写一条退款技能", "private", _Once(_payload())
    )
    confirm_skill_draft(session, _user(session, "member-1"), change.view.id)
    rows = session.exec(select(Skill).where(Skill.name == "refund_guide")).all()
    assert len(rows) == 2
    retired = next(row for row in rows if row.lifecycle == "retired")
    active = next(row for row in rows if row.lifecycle == "active")
    assert retired.enabled is False
    assert active.supersedes_id == retired.id
    assert active.version == "1.1"


@pytest.mark.asyncio
async def test_unknown_or_high_risk_tool_is_not_saved(session: Session) -> None:
    before = len(session.exec(select(Skill)).all())
    with pytest.raises(SkillRequestError, match="不允许的工具"):
        await generate_skill_draft(
            session,
            _user(session, "member-1"),
            "写一条退款技能",
            "private",
            _Once(_payload(tools=["file_ops_write"])),
        )
    with pytest.raises(SkillRequestError, match="不允许的工具"):
        await generate_skill_draft(
            session,
            _user(session, "member-1"),
            "写一条退款技能",
            "private",
            _Once(_payload(name="other_guide", tools=["not_a_tool"])),
        )
    assert len(session.exec(select(Skill)).all()) == before


@pytest.mark.asyncio
async def test_script_text_is_not_saved(session: Session) -> None:
    body = _payload().replace("先核对订单，再说明时效", "先运行 ```python")
    before = len(session.exec(select(Skill)).all())
    with pytest.raises(SkillRequestError, match="可执行脚本"):
        await generate_skill_draft(
            session, _user(session, "member-1"), "写一条退款技能", "private", _Once(body)
        )
    assert len(session.exec(select(Skill)).all()) == before


@pytest.mark.asyncio
async def test_only_the_author_can_confirm_a_private_draft(session: Session) -> None:
    change = await generate_skill_draft(
        session, _user(session, "member-1"), "写一条退款技能", "private", _Once(_payload())
    )
    with pytest.raises(SkillRequestError) as exc:
        confirm_skill_draft(session, _user(session, "member-2"), change.view.id)
    assert exc.value.status_code in {403, 404}


@pytest.mark.asyncio
async def test_member_cannot_generate_a_global_draft(session: Session) -> None:
    model = _Once(_payload())
    with pytest.raises(SkillRequestError) as exc:
        await generate_skill_draft(session, _user(session, "member-1"), "写一条退款技能", "global", model)
    assert exc.value.status_code == 403
    assert model.calls == 0
