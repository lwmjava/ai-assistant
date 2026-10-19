"""从真实对话入口验证技能内容、绑定工具及安全终态。"""

import json
import uuid

import pytest
from sqlmodel import Session, SQLModel, col, create_engine, select

from app.agents.prompts import SYSTEM_ACT, SYSTEM_PREFLOW
from app.agents.skills.base import SkillManifest, SkillMode, SkillTrigger, TriggerType
from app.agents.supervisor import _DRAFT_SYSTEM, _RESEARCH_SYSTEM, _SUPERVISOR_SYSTEM
from app.core.config import settings
from app.llm.mock import MockLLMProvider
from app.models.conversation import Message
from app.models.user import Tenant, User
from app.services.chat_service import ChatService


class SkillModel(MockLLMProvider):
    def __init__(self, *, tool=False):
        super().__init__()
        self.systems = []
        self.tool = tool
        self.actions = 0
        self.supervisor_calls = 0

    async def chat(self, messages, options=None):
        system = str(messages[0].content)
        self.systems.append(system)
        if system.startswith(_SUPERVISOR_SYSTEM):
            self.supervisor_calls += 1
            return "research" if self.supervisor_calls == 1 else "draft"
        if system == SYSTEM_PREFLOW:
            return "NO"
        if system.startswith(SYSTEM_ACT) and self.tool:
            self.actions += 1
            if self.actions == 1:
                return '<tool_call>{"name":"calculator","arguments":{"expression":"1+1"}}</tool_call>'
        return "隔离技能回答"


@pytest.fixture
def skill_entry(tmp_path, monkeypatch):
    for key, value in {
        "RAG_ENABLED": False, "MEMORY_ENABLED": False, "SKILL_ENABLED": True,
        "MCP_ENABLED": False, "AGENT_QUALITY_GATE_ENABLED": False,
        "SECURITY_RATE_LIMIT": False, "LLM_RESPONSE_CACHE_ENABLED": False,
        "AGENT_STATE_PERSIST_ENABLED": False,
    }.items():
        monkeypatch.setattr(settings, key, value)
    engine = create_engine(f"sqlite:///{tmp_path / 'skills.db'}")
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


def manifest(*, tool_names=None):
    return SkillManifest(
        name="bridge_probe", version="1.0", description="隔离技能",
        trigger=SkillTrigger(type=TriggerType.ALWAYS),
        mode=SkillMode.TOOL if tool_names is not None else SkillMode.PROMPT_INJECTION,
        system_prompt="BRIDGE_BODY", tools=tool_names or [], origin="builtin",
    )


async def run_entry(entry, model, message, streaming):
    engine, actor = entry
    service = ChatService(model)
    with Session(engine) as session:
        if streaming:
            events = [event async for event in service.chat_stream(session, actor, message)]
            message_id = session.info["agent_message_id"]
            conversation_id = session.get(Message, message_id).conversation_id
        else:
            conversation, _ = await service.chat(session, actor, message)
            conversation_id = conversation.id
            events = []
    with Session(engine) as fresh:
        messages = list(fresh.exec(select(Message).where(col(Message.conversation_id) == conversation_id)))
        answers = [item for item in messages if item.role == "assistant"]
        assert len(answers) == 1
        return answers[0], events


@pytest.mark.parametrize("streaming", [False, True])
async def test_supervisor_workers_consume_prompt_but_control_does_not(skill_entry, monkeypatch, streaming):
    import app.services.skill_service as skills

    selected = manifest()
    monkeypatch.setattr(skills, "manifests_for_chat", lambda session, user: [selected])
    monkeypatch.setattr(settings, "AGENT_ORCHESTRATION", "langgraph")
    model = SkillModel()
    answer, _ = await run_entry(skill_entry, model, "先调研多轮需求", streaming)
    for base in (_RESEARCH_SYSTEM, _DRAFT_SYSTEM):
        calls = [text for text in model.systems if text.startswith(base)]
        assert calls and all("BRIDGE_BODY" in text for text in calls)
    assert all("BRIDGE_BODY" not in text for text in model.systems if text.startswith(_SUPERVISOR_SYSTEM))
    assert "bridge_probe" in json.loads(answer.skill_names)


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("orchestration", ["self", "langgraph"])
async def test_bound_tools_do_not_allow_model_to_use_another_registered_tool(
    skill_entry, monkeypatch, streaming, orchestration,
):
    import app.services.skill_service as skills
    from app.agents.tools.base import ToolRegistry

    selected = manifest(tool_names=["get_current_datetime"])
    monkeypatch.setattr(skills, "manifests_for_chat", lambda session, user: [selected])
    monkeypatch.setattr(settings, "AGENT_ORCHESTRATION", orchestration)
    original = ToolRegistry.get
    calls = []

    def observed(registry, name):
        tool = original(registry, name)
        if name == "calculator" and tool is not None:
            original_func = tool.func

            async def tracked(arguments):
                calls.append(name)
                return await original_func(arguments)

            from dataclasses import replace

            return replace(tool, func=tracked)
        return tool

    monkeypatch.setattr(ToolRegistry, "get", observed)
    await run_entry(skill_entry, SkillModel(tool=True), "算一下1+1", streaming)
    assert calls == []


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("names", [[], ["absent_probe"]])
async def test_missing_binding_has_explicit_user_result(skill_entry, monkeypatch, streaming, names):
    import app.services.skill_service as skills

    monkeypatch.setattr(skills, "manifests_for_chat", lambda session, user: [manifest(tool_names=names)])
    monkeypatch.setattr(settings, "AGENT_ORCHESTRATION", "langgraph")
    answer, _ = await run_entry(skill_entry, SkillModel(), "算一下1+1", streaming)
    assert "技能" in answer.content and ("未注册" in answer.content or "绑定" in answer.content)


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("orchestration,message", [
    ("self", "你好"), ("langgraph", "你好"),
    ("langgraph", "根据知识库回答"), ("langgraph", "算一下1+1"),
])
async def test_supported_prompt_paths_consume_actual_version(
    skill_entry, monkeypatch, caplog, streaming, orchestration, message,
):
    import app.services.skill_service as skills

    selected = manifest()
    monkeypatch.setattr(skills, "manifests_for_chat", lambda session, user: [selected])
    monkeypatch.setattr(settings, "AGENT_ORCHESTRATION", orchestration)
    caplog.set_level("INFO", logger="app.agents.skills.base")
    model = SkillModel()
    answer, _ = await run_entry(skill_entry, model, message, streaming)
    assert any("BRIDGE_BODY" in system for system in model.systems)
    metadata = [record.getMessage() for record in caplog.records if "skill_runtime" in record.getMessage()]
    assert any("status=injected" in text and selected.effective_version in text for text in metadata)
    assert all("BRIDGE_BODY" not in text for text in metadata)
    assert json.loads(answer.skill_names) == ["bridge_probe"]


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("orchestration", ["self", "langgraph"])
async def test_bound_calculator_really_executes_once_with_exact_version(
    skill_entry, monkeypatch, caplog, streaming, orchestration,
):
    import app.services.skill_service as skills

    selected = manifest(tool_names=["calculator"])
    monkeypatch.setattr(skills, "manifests_for_chat", lambda session, user: [selected])
    monkeypatch.setattr(settings, "AGENT_ORCHESTRATION", orchestration)
    caplog.set_level("INFO", logger="app.agents.skills.base")
    model = SkillModel(tool=True)
    answer, _ = await run_entry(skill_entry, model, "算一下1+1", streaming)
    successful = [record.getMessage() for record in caplog.records
                  if "skill_runtime" in record.getMessage() and "status=ok" in record.getMessage()]
    assert len(successful) == 1
    assert "tool=calculator" in successful[0] and selected.effective_version in successful[0]
    if orchestration == "langgraph":
        assert "path=fast.tools" in successful[0]
    assert model.actions == 2 and answer.status == "complete"


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("message", ["你好", "根据知识库回答", "先调研多轮需求"])
async def test_unsupported_tool_mode_is_explicit_and_never_reports_execution(
    skill_entry, monkeypatch, caplog, streaming, message,
):
    import app.services.skill_service as skills

    monkeypatch.setattr(skills, "manifests_for_chat", lambda session, user: [manifest(tool_names=["calculator"])])
    monkeypatch.setattr(settings, "AGENT_ORCHESTRATION", "langgraph")
    caplog.set_level("INFO", logger="app.agents.skills.base")
    answer, _ = await run_entry(skill_entry, SkillModel(), message, streaming)
    assert "【技能提示】" in answer.content and "不支持" in answer.content
    records = [record.getMessage() for record in caplog.records if "skill_runtime" in record.getMessage()]
    assert any("status=unsupported" in text for text in records)
    assert not any("status=ok" in text or "status=executed" in text for text in records)


@pytest.mark.parametrize("confidence", [float("nan"), float("inf"), -float("inf"), -1.0, 2.0, True])
def test_invalid_confidence_never_activates(confidence):
    from app.agents.skills.base import SkillMatch
    from app.agents.skills.manager import SkillManager

    with pytest.raises(ValueError, match="confidence"):
        SkillManager().activate([SkillMatch(manifest(), confidence=confidence)])


@pytest.mark.parametrize("streaming", [False, True])
async def test_untrusted_tool_name_is_not_written_to_skill_metadata(skill_entry, monkeypatch, caplog, streaming):
    import app.services.skill_service as skills

    marker = "SYNTHETIC_PRIVATE_MARKER"

    class UntrustedNameModel(SkillModel):
        async def chat(self, messages, options=None):
            if str(messages[0].content).startswith(SYSTEM_ACT):
                return "<tool_call>" + json.dumps({
                    "name": marker + "\nforged-log-line", "arguments": {},
                }) + "</tool_call>"
            return await super().chat(messages, options)

    monkeypatch.setattr(skills, "manifests_for_chat", lambda session, user: [manifest(tool_names=["calculator"])])
    monkeypatch.setattr(settings, "AGENT_ORCHESTRATION", "self")
    caplog.set_level("INFO", logger="app.agents.skills.base")
    await run_entry(skill_entry, UntrustedNameModel(), "算一下1+1", streaming)
    assert not any(marker in record.getMessage() for record in caplog.records
                   if record.name == "app.agents.skills.base")
