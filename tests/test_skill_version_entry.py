"""Independent Skill version acceptance through files, HTTP, Chat and Executor.

The capturing provider proves prompt transfer, not real model answer quality.
"""

import asyncio
import copy
import logging
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from app.agents.skills.base import SkillManifest, SkillMode, SkillTrigger
from app.agents.skills.manager import SkillManager
from app.agents.tools.base import Tool, ToolCall, ToolRegistry
from app.agents.tools.contract import RiskLevel, ToolContract, ToolPermission
from app.agents.tools.executor import ToolExecutor
from app.audit.models import AuditLog
from app.core.config import settings
from app.core.database import engine
from app.core.security import Role, hash_password
from app.llm.base import ChatMessage, LLMOptions
from app.llm.factory import set_llm_provider_override
from app.llm.mock import MockLLMProvider
from app.main import app
from app.models.skill import Skill
from app.models.user import Tenant, User
from app.services.chat_service import ChatService


class CapturingProvider(MockLLMProvider):
    def __init__(self):
        super().__init__()
        self.calls: list[list[ChatMessage]] = []

    async def chat(self, messages: list[ChatMessage], options: LLMOptions | None = None) -> str:
        self.calls.append(copy.deepcopy(messages))
        return await super().chat(messages, options)


def _write(directory: Path, prompt: str = "ENTRY_ORIGINAL", version: str = "1.0") -> None:
    (directory / "entry.yaml").write_text(yaml.safe_dump({
        "name": "entry", "version": version, "system_prompt": prompt,
        "trigger": {"type": "keyword", "keywords": ["入口词"]},
    }, allow_unicode=True), encoding="utf-8")


def test_skill_file_update_changes_executed_version_and_keeps_stale_snapshot(tmp_path):
    _write(tmp_path)
    manager = SkillManager()
    manager.load([tmp_path])
    old_match = manager.match("入口词")[0]
    old = manager.activate([old_match])
    assert "ENTRY_ORIGINAL" in manager.inject_prompt(old, "BASE")
    assert old.versions[0]["version"] == "1.0"
    _write(tmp_path, "ENTRY_UPDATED")
    manager.reload()
    updated = manager.activate(manager.match("入口词"))
    assert "ENTRY_UPDATED" in manager.inject_prompt(updated, "BASE")
    assert "ENTRY_ORIGINAL" not in updated.prompt_injection
    assert updated.versions[0]["effective_version"] != old.versions[0]["effective_version"]
    stale = manager.activate([old_match])
    assert stale.versions == old.versions
    assert "ENTRY_ORIGINAL" in stale.prompt_injection
    assert manager.match("天气正常") == []


def test_skill_failed_reload_preserves_actual_files_and_history(tmp_path):
    _write(tmp_path)
    manager = SkillManager()
    manager.load([tmp_path])
    before = manager.activate(manager.match("入口词"))
    history = manager.history()
    (tmp_path / "broken.yaml").write_text("name: [invalid\n", encoding="utf-8")
    with pytest.raises(ValueError):
        manager.reload()
    after = manager.activate(manager.match("入口词"))
    assert after.versions == before.versions
    assert after.prompt_injection == before.prompt_injection
    assert manager.history() == history
    (tmp_path / "broken.yaml").unlink()
    _write(tmp_path, "AFTER_FAILURE")
    manager.reload()
    assert "AFTER_FAILURE" in manager.activate(manager.match("入口词")).prompt_injection


def test_skill_registration_and_public_nested_copies_cannot_change_execution():
    manager = SkillManager()
    original = SkillManifest("isolated", system_prompt="ISOLATED_ORIGINAL",
                             trigger=SkillTrigger(keywords=["入口词"]))
    manager.register(original)
    before = manager.activate(manager.match("入口词"))
    original.system_prompt = "CALLER_TAMPER"
    original.trigger.keywords.clear()
    fetched = manager.get("isolated")
    assert fetched is not None
    fetched.system_prompt = "GET_TAMPER"
    fetched.trigger.keywords.clear()
    listed = manager.list_all()[0]
    listed.system_prompt = "LIST_TAMPER"
    listed.trigger.keywords.clear()
    matches = manager.match("入口词")
    matches[0].skill.system_prompt = "MATCH_TAMPER"
    matches[0].skill.trigger.keywords.clear()
    after = manager.activate(manager.match("入口词"))
    assert after.versions == before.versions
    assert after.prompt_injection == before.prompt_injection
    history = manager.history()
    history.clear()
    assert manager.history()


@pytest.fixture
def actors(monkeypatch):
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(settings, "RAG_ENABLED", False)
    monkeypatch.setattr(settings, "EVOLUTION_ENABLED", False)
    monkeypatch.setattr(settings, "SKILL_ENABLED", True)
    client = TestClient(app)
    ids = {}
    with Session(engine) as session:
        for tenant_name, usernames in [("version-alpha", ["version-ada", "version-bob", "version-root"]),
                                       ("version-beta", ["version-ben"])]:
            tenant = Tenant(name=tenant_name)
            session.add(tenant)
            session.commit()
            session.refresh(tenant)
            for username in usernames:
                user = User(tenant_id=tenant.id, username=username,
                            hashed_password=hash_password("Test@Pass123!"),
                            role=Role.SYSTEM_ADMIN.value if username == "version-root" else Role.MEMBER.value)
                session.add(user)
                session.commit()
                session.refresh(user)
                ids[username] = user.id
    headers = {}
    for username in ids:
        login = client.post("/api/v1/auth/login", json={"username": username, "password": "Test@Pass123!"})
        assert login.status_code == 200
        headers[username] = {"Authorization": f"Bearer {login.json()['access_token']}"}
    provider = CapturingProvider()
    set_llm_provider_override(provider)
    return client, ids, headers, provider


def _body(prompt: str):
    return {"name": "same-skill", "description": "版本回归", "keywords": ["回归口令"],
            "constraints": "只处理回归口令", "system_prompt": prompt, "example": "回归口令"}


def _systems(provider: CapturingProvider) -> str:
    return "\n".join(message.content for call in provider.calls for message in call
                     if message.role.value == "system")


def test_skill_http_update_commits_new_content_next_chat_logs_new_execution_version(actors, caplog):
    client, ids, headers, provider = actors
    auth = headers["version-ada"]
    created = client.post("/api/v1/skills", headers=auth, json=_body("HTTP_FIRST_BODY"))
    assert created.status_code == 201, created.text
    skill_id = created.json()["id"]
    created_detail = client.get(f"/api/v1/skills/{skill_id}", headers=auth)
    assert created_detail.status_code == 200
    with Session(engine) as session:
        user = session.get(User, ids["version-ada"])
        assert user is not None
        old = ChatService(provider)._match_skills(session, user, "回归口令")
        assert old is not None
        old_version = old.versions[0]["effective_version"]
    with caplog.at_level(logging.INFO, logger="app.agents.skills.manager"):
        first = client.post("/api/v1/chat", headers=auth, json={"message": "回归口令"})
    assert first.status_code == 200, first.text
    assert first.json()["skill_names"] == ["same-skill"]
    assert "HTTP_FIRST_BODY" in _systems(provider)
    assert old_version in caplog.text
    patched = client.patch(f"/api/v1/skills/{skill_id}", headers=auth, json=_body("HTTP_SECOND_BODY"))
    assert patched.status_code == 200, patched.text
    patched_detail = client.get(f"/api/v1/skills/{skill_id}", headers=auth)
    assert patched_detail.status_code == 200
    assert patched_detail.json()["version"] == created_detail.json()["version"] == "1.0"
    with Session(engine) as session:
        row = session.get(Skill, skill_id)
        assert row is not None and row.system_prompt == "HTTP_SECOND_BODY"
        user = session.get(User, ids["version-ada"])
        assert user is not None
        new = ChatService(provider)._match_skills(session, user, "回归口令")
        assert new is not None and new.origin == "private"
        new_version = new.versions[0]["effective_version"]
        assert new_version != old_version
        assert new.versions[0]["origin"] == "private"
        updates = session.exec(select(AuditLog).where(AuditLog.action == "skill_update")).all()
        assert len(updates) == 1
        assert updates[0].resource_id == skill_id and updates[0].user_id == ids["version-ada"]
        assert "HTTP_SECOND_BODY" not in str(updates[0].details)
    provider.calls.clear()
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="app.agents.skills.manager"):
        second = client.post("/api/v1/chat", headers=auth, json={"message": "回归口令"})
    assert second.status_code == 200, second.text
    assert "HTTP_SECOND_BODY" in _systems(provider)
    assert "HTTP_FIRST_BODY" not in _systems(provider)
    assert new_version in caplog.text
    assert "HTTP_SECOND_BODY" not in caplog.text


def test_skill_same_name_tenant_and_owner_execution_versions_never_mix(actors):
    client, ids, headers, provider = actors
    expected = {"version-ada": "OWNER_ADA_BODY", "version-bob": "OWNER_BOB_BODY",
                "version-ben": "TENANT_BEN_BODY"}
    versions = set()
    for username, prompt in expected.items():
        created = client.post("/api/v1/skills", headers=headers[username], json=_body(prompt))
        assert created.status_code == 201, created.text
    for username, prompt in expected.items():
        with Session(engine) as session:
            user = session.get(User, ids[username])
            assert user is not None
            ctx = ChatService(provider)._match_skills(session, user, "回归口令")
            assert ctx is not None
            versions.add(ctx.versions[0]["effective_version"])
        provider.calls.clear()
        response = client.post("/api/v1/chat", headers=headers[username], json={"message": "回归口令"})
        assert response.status_code == 200, response.text
        assert response.json()["skill_names"] == ["same-skill"]
        systems = _systems(provider)
        assert prompt in systems and "不可信" in systems
        assert all(other not in systems for other in expected.values() if other != prompt)
    assert len(versions) == 3


def test_skill_global_private_same_name_selects_only_authorized_final_version(actors):
    client, ids, headers, provider = actors
    global_skill = client.post("/api/v1/skills", headers=headers["version-root"],
                               json=_body("GLOBAL_ONLY_BODY") | {"scope": "global"})
    assert global_skill.status_code == 201, global_skill.text
    private_skill = client.post("/api/v1/skills", headers=headers["version-ada"],
                                json=_body("PRIVATE_ONLY_BODY"))
    assert private_skill.status_code == 201, private_skill.text
    # Unauthorized PATCH must preserve committed content and following execution.
    denied = client.patch(f"/api/v1/skills/{private_skill.json()['id']}",
                          headers=headers["version-ben"], json=_body("DENIED_TAMPER_BODY"))
    assert denied.status_code == 404, denied.text
    denied_global = client.patch(f"/api/v1/skills/{global_skill.json()['id']}",
                                 headers=headers["version-bob"], json=_body("DENIED_GLOBAL_BODY"))
    assert denied_global.status_code == 403, denied_global.text
    expected = [("version-ada", "private", "PRIVATE_ONLY_BODY", "GLOBAL_ONLY_BODY"),
                ("version-bob", "global", "GLOBAL_ONLY_BODY", "PRIVATE_ONLY_BODY"),
                ("version-ben", "global", "GLOBAL_ONLY_BODY", "PRIVATE_ONLY_BODY")]
    selected = {}
    for username, origin, present, absent in expected:
        with Session(engine) as session:
            user = session.get(User, ids[username])
            assert user is not None
            ctx = ChatService(provider)._match_skills(session, user, "回归口令")
            assert ctx is not None and ctx.origin == origin
            assert len(ctx.versions) == 1 and ctx.versions[0]["origin"] == origin
            selected[username] = ctx.versions[0]["effective_version"]
        provider.calls.clear()
        response = client.post("/api/v1/chat", headers=headers[username], json={"message": "回归口令"})
        assert response.status_code == 200, response.text
        systems = _systems(provider)
        assert present in systems and absent not in systems
        assert "DENIED_TAMPER_BODY" not in systems and "DENIED_GLOBAL_BODY" not in systems
    assert selected["version-bob"] == selected["version-ben"] != selected["version-ada"]
    with Session(engine) as session:
        private = session.get(Skill, private_skill.json()["id"])
        global_row = session.get(Skill, global_skill.json()["id"])
        assert private is not None and private.system_prompt == "PRIVATE_ONLY_BODY"
        assert global_row is not None and global_row.system_prompt == "GLOBAL_ONLY_BODY"


@pytest.mark.parametrize("message,name,clauses", [
    ("请审查代码", "code_review", ["SQL 注入", "严重", "总体评分", "没有提供代码"]),
    ("请翻译成英文：你好", "translator", ["忠实原文", "保留原文的语气", "术语", "先给出翻译结果"]),
])
def test_skill_builtin_real_chat_transfers_existing_constraints(actors, caplog, message, name, clauses):
    client, _, headers, provider = actors
    manager = SkillManager()
    manager.load()
    manifest = manager.get(name)
    assert manifest is not None
    with caplog.at_level(logging.INFO, logger="app.agents.skills.manager"):
        response = client.post("/api/v1/chat", headers=headers["version-ada"], json={"message": message})
    assert response.status_code == 200, response.text
    assert response.json()["skill_names"] == [name]
    systems = _systems(provider)
    assert manifest.system_prompt in systems
    assert all(clause in systems for clause in clauses)
    assert manifest.effective_version in caplog.text


def test_skill_builtin_unmatched_chat_does_not_inject_constraints(actors):
    client, _, headers, provider = actors
    response = client.post("/api/v1/chat", headers=headers["version-ada"], json={"message": "今天晴天"})
    assert response.status_code == 200, response.text
    assert response.json()["skill_names"] == []
    systems = _systems(provider)
    assert "资深代码审查专家" not in systems
    assert "专业翻译专家" not in systems


def test_skill_tool_activation_cannot_authorize_l2_executor_side_effect():
    calls = []
    registry = ToolRegistry([Tool(
        "entry_write", "restricted write", {"type": "object"},
        lambda args: calls.append(args) or "written",
        contract=ToolContract(version="1.0", permission=ToolPermission(authenticated=True),
                              risk=RiskLevel.L2, side_effect="write", timeout_seconds=1),
    )])
    manager = SkillManager()
    manager.register(SkillManifest("tool_entry", mode=SkillMode.TOOL, tools=["entry_write"],
                                   trigger=SkillTrigger(keywords=["入口词"])))
    ctx = manager.activate(manager.match("入口词"))
    assert ctx.tool_names == ["entry_write"]
    assert ctx.versions[0]["name"] == "tool_entry"
    assert manager.collect_tools(ctx, registry)[0].name == "entry_write"
    result = asyncio.run(ToolExecutor(registry).execute(ToolCall("entry_write", {})))
    assert result.status == "denied" and result.attempts == 0
    assert calls == []
