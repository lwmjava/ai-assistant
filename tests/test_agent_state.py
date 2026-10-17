"""PAGT-006 StateManager + AgentRunState 持久化：快照、过期/损坏降级、跨租户、续跑。

证据级别 E2：真实代码 + 隔离 SQLite 测试库 data/test_ai_assistant.db；
持久化读回用新 Session 断言。
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session, delete, select

from app.agents.pipeline import AgentPipeline, AgentState, ToolRegistry, tool_fingerprint
from app.agents.state import StateManager
from app.agents.tools.base import Tool
from app.core.config import Settings, settings
from app.core.database import engine
from app.models.agent_state import AgentRunState
from app.models.conversation import Conversation
from app.models.user import Tenant, User
from app.services.agent_state_service import AgentStateService


class ScriptLLM:
    """最小脚本 LLM：按 act 提示词返回固定工具调用或草稿。"""

    model = "script-resume"

    def __init__(self, reply) -> None:
        self.reply = reply

    async def chat(self, messages, options=None) -> str:
        return self.reply(messages[0].content, messages[-1].content)

    async def stream_chat(self, messages, options=None):
        yield "x"


def _echo_tool():
    calls = {"n": 0}

    async def echo(arguments):
        calls["n"] += 1
        return f"echo:{arguments.get('q', '')}"

    return Tool(name="echo", description="", parameters={"type": "object"}, func=echo), calls


@pytest.fixture()
def binding():
    with Session(engine) as session:
        tenant = Tenant(name="pagt006-tenant")
        session.add(tenant)
        session.commit()
        session.refresh(tenant)
        user = User(
            tenant_id=tenant.id, username="pagt006-user", hashed_password="x", role="member"
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        conv = Conversation(tenant_id=tenant.id, user_id=user.id, title="t")
        session.add(conv)
        session.commit()
        session.refresh(conv)
        session.exec(delete(AgentRunState))
        session.commit()
        b = {
            "conversation_id": conv.id,
            "message_id": "msg-1",
            "tenant_id": tenant.id,
            "user_id": user.id,
        }
        yield b
        session.exec(delete(AgentRunState))
        session.exec(delete(Conversation).where(Conversation.id == conv.id))
        session.exec(delete(User).where(User.id == user.id))
        session.exec(delete(Tenant).where(Tenant.id == tenant.id))
        session.commit()


# ── 身份与绑定：StateManager 零 DB import、快照契约、无密钥 ───────────


def test_state_manager_module_has_no_db_import() -> None:
    import app.agents.state as st

    src = inspect.getsource(st)
    for line in src.splitlines():
        s = line.strip()
        if s.startswith("import ") or s.startswith("from "):
            assert "sqlmodel" not in s, s
            assert "app.core.database" not in s, s
            assert "app.models" not in s, s


def test_snapshot_serializes_fingerprints_as_sorted_list() -> None:
    state = AgentState(user_input="hi")
    state.executed_tool_fingerprints = {"fp-b", "fp-a"}
    snap = StateManager(state).snapshot()
    assert snap["executed_tool_fingerprints"] == ["fp-a", "fp-b"]


def test_snapshot_contains_no_config_secrets() -> None:
    state = AgentState(user_input="hello")
    state.plan = "do something"
    text = StateManager(state).to_json()
    assert "LLM_API_KEY" not in text
    assert "api_key" not in text.lower()
    assert "password" not in text.lower()


def test_restore_from_corrupt_json_returns_none() -> None:
    state = AgentState(user_input="hi")
    assert StateManager.restore_from_json(state, None) is None
    assert StateManager.restore_from_json(state, "{{{{not json") is None
    assert StateManager.restore_from_json(state, "[1,2,3]") is None


def test_agent_state_config_defaults() -> None:
    assert Settings.model_fields["AGENT_STATE_PERSIST_ENABLED"].default is True
    assert Settings.model_fields["AGENT_STATE_TTL_SECONDS"].default == 604800
    assert Settings.model_fields["AGENT_STATE_CHECKPOINT_TOOL_ROUND"].default is True


# ── 失败路径：无行 / 损坏 / 过期 / 跨租户 ────────────────────────────


def test_load_active_no_row_means_fresh_run(binding) -> None:
    with Session(engine) as session:
        svc = AgentStateService(session, tenant_id=binding["tenant_id"], user_id=binding["user_id"])
        assert svc.load_active(binding["conversation_id"], binding["message_id"]) is None


def test_terminal_done_row_not_resumed(binding) -> None:
    """P1-1 根因：终态 done 行绝不被当作中断续跑；下次访问丢弃该行、按全新 run。"""
    with Session(engine) as session:
        svc = AgentStateService(session, tenant_id=binding["tenant_id"], user_id=binding["user_id"])
        svc.save_checkpoint(
            conversation_id=binding["conversation_id"], message_id=binding["message_id"],
            tenant_id=binding["tenant_id"], user_id=binding["user_id"],
            stage="done", round_no=1, tool_budget_exhausted=False,
            state_json='{"plan":"x","executed_tool_fingerprints":["fp"],"tool_results":["[echo] r"]}',
            status="done", error=None,
        )
        assert svc.load_active(binding["conversation_id"], binding["message_id"]) is None
        # 终态行被 discard，不再残留
        assert session.exec(select(AgentRunState)).all() == []


def test_terminal_error_row_not_resumed(binding) -> None:
    with Session(engine) as session:
        svc = AgentStateService(session, tenant_id=binding["tenant_id"], user_id=binding["user_id"])
        svc.save_checkpoint(
            conversation_id=binding["conversation_id"], message_id=binding["message_id"],
            tenant_id=binding["tenant_id"], user_id=binding["user_id"],
            stage="done", round_no=1, tool_budget_exhausted=False,
            state_json='{"plan":"x"}', status="error", error="boom",
        )
        assert svc.load_active(binding["conversation_id"], binding["message_id"]) is None


def test_corrupt_snapshot_degrades_to_fresh_run_and_discards_row(binding) -> None:
    with Session(engine) as session:
        session.add(AgentRunState(
            tenant_id=binding["tenant_id"], user_id=binding["user_id"],
            conversation_id=binding["conversation_id"], message_id=binding["message_id"],
            state_json="{{{{bad json", status="running", expires_at=None,
        ))
        session.commit()
        svc = AgentStateService(session, tenant_id=binding["tenant_id"], user_id=binding["user_id"])
        assert svc.load_active(binding["conversation_id"], binding["message_id"]) is None
        # 损坏行被丢弃，不改写其他行
        assert session.exec(select(AgentRunState)).all() == []


def test_expired_snapshot_is_ignored(binding) -> None:
    from datetime import UTC

    with Session(engine) as session:
        session.add(AgentRunState(
            tenant_id=binding["tenant_id"], user_id=binding["user_id"],
            conversation_id=binding["conversation_id"], message_id=binding["message_id"],
            state_json='{"plan":"x"}', status="running",
            expires_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=2),
        ))
        session.commit()
        svc = AgentStateService(session, tenant_id=binding["tenant_id"], user_id=binding["user_id"])
        assert svc.load_active(binding["conversation_id"], binding["message_id"]) is None


def test_checkpoint_cross_tenant_not_restored(binding) -> None:
    with Session(engine) as s1:
        svc_a = AgentStateService(s1, tenant_id=binding["tenant_id"], user_id=binding["user_id"])
        svc_a.save_checkpoint(
            conversation_id=binding["conversation_id"], message_id=binding["message_id"],
            tenant_id=binding["tenant_id"], user_id=binding["user_id"],
            stage="action", round_no=1, tool_budget_exhausted=False,
            state_json='{"plan":"x"}', status="running", error=None,
        )
    with Session(engine) as s2:
        svc_b = AgentStateService(s2, tenant_id="other-tenant", user_id="other-user")
        assert svc_b.load_active(binding["conversation_id"], binding["message_id"]) is None


# ── 回滚与幂等：upsert 单行、开关关零行、新 Session 读回 ──────────────


def test_checkpoint_upsert_is_single_row_not_duplicate(binding) -> None:
    with Session(engine) as session:
        svc = AgentStateService(session, tenant_id=binding["tenant_id"], user_id=binding["user_id"])
        for i in range(2):
            svc.save_checkpoint(
                conversation_id=binding["conversation_id"], message_id=binding["message_id"],
                tenant_id=binding["tenant_id"], user_id=binding["user_id"],
                stage="action", round_no=i + 1, tool_budget_exhausted=False,
                state_json='{"plan":"x"}', status="running", error=None,
            )
        rows = session.exec(select(AgentRunState)).all()
        assert len(rows) == 1
        assert rows[0].round_no == 2  # upsert 取最新


def test_checkpoint_written_at_tool_round_and_read_back_new_session(binding) -> None:
    """开关开时工具轮写 checkpoint；新 Session 读回断言（非同一对象保有值）。"""
    tool, calls = _echo_tool()
    registry = ToolRegistry([tool])
    llm = ScriptLLM(lambda s, u: '<tool_call>{"name": "echo", "arguments": {"q": "hi"}}</tool_call>')
    pipeline = AgentPipeline(llm, tools=registry)
    with Session(engine) as session:
        svc = AgentStateService(session, tenant_id=binding["tenant_id"], user_id=binding["user_id"])
        pipeline.state_store = svc
        pipeline.state_binding = binding
        asyncio.run(pipeline._run_action_loop(AgentState(user_input="hi")))
    # 新 Session 读回
    with Session(engine) as session2:
        rows = session2.exec(select(AgentRunState)).all()
        assert len(rows) >= 1
        assert rows[0].conversation_id == binding["conversation_id"]
        assert rows[0].state_json


def test_persist_disabled_writes_zero_rows(binding, monkeypatch) -> None:
    monkeypatch.setattr(settings, "AGENT_STATE_PERSIST_ENABLED", False)
    tool, calls = _echo_tool()
    registry = ToolRegistry([tool])
    llm = ScriptLLM(lambda s, u: '<tool_call>{"name": "echo", "arguments": {"q": "hi"}}</tool_call>')
    pipeline = AgentPipeline(llm, tools=registry)
    with Session(engine) as session:
        svc = AgentStateService(session, tenant_id=binding["tenant_id"], user_id=binding["user_id"])
        pipeline.state_store = svc
        pipeline.state_binding = binding
        asyncio.run(pipeline._run_action_loop(AgentState(user_input="hi")))
    with Session(engine) as session:
        assert session.exec(select(AgentRunState)).all() == []


# ── 正向续跑（acceptance②）：从 checkpoint 续跑、不重复已完成工具 ───────


def test_resume_continues_from_checkpoint_not_restarting(binding) -> None:
    echo_fp = tool_fingerprint("echo", {"q": "hi"})
    # ① 中段：echo 已执行一次，落 checkpoint
    with Session(engine) as session:
        svc = AgentStateService(session, tenant_id=binding["tenant_id"], user_id=binding["user_id"])
        mid = AgentState(user_input="hi")
        mid.executed_tool_fingerprints = {echo_fp}
        mid.tool_results.append('[echo] 参数={} 结果=echo:hi')
        svc.save_checkpoint(
            conversation_id=binding["conversation_id"], message_id=binding["message_id"],
            tenant_id=binding["tenant_id"], user_id=binding["user_id"],
            stage="action", round_no=1, tool_budget_exhausted=False,
            state_json=StateManager(mid).to_json(), status="running", error=None,
        )
    # ② 新 run（新 Session）加载 checkpoint，应用到全新 state
    with Session(engine) as session:
        svc = AgentStateService(session, tenant_id=binding["tenant_id"], user_id=binding["user_id"])
        data = svc.load_active(binding["conversation_id"], binding["message_id"])
        assert data is not None
        resumed = AgentState(user_input="hi")
        StateManager(resumed).apply_snapshot(data)
        assert echo_fp in resumed.executed_tool_fingerprints  # 指纹保留
        assert len(resumed.tool_results) == 1
        # ③ 续跑：脚本再次发同一 echo 调用 → 指纹命中判 skip，func 不重复执行
        tool, calls = _echo_tool()
        registry = ToolRegistry([tool])
        llm = ScriptLLM(lambda s, u: '<tool_call>{"name": "echo", "arguments": {"q": "hi"}}</tool_call>')
        pipeline = AgentPipeline(llm, tools=registry)
        asyncio.run(pipeline._run_action_loop(resumed))
        assert calls["n"] == 0  # 已完成工具不重复调用（副作用不重复）
