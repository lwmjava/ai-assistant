"""Isolated manual acceptance: real service/executor/checkpoints/audit/HTTP."""

import asyncio
import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
importlib.import_module("tests.conftest")
import pytest  # noqa: E402 - isolated conftest bootstrap must precede app imports
from sqlmodel import Session, col, select  # noqa: E402 - isolated conftest bootstrap must precede app imports

from app.agents.tools.base import (  # noqa: E402 - isolated conftest bootstrap must precede app imports
    Tool,
    ToolRegistry,
)
from app.agents.tools.contract import (  # noqa: E402 - isolated conftest bootstrap must precede app imports
    RiskLevel,
    ToolContract,
    ToolPermission,
)
from app.agents.tools.executor import ToolExecutor  # noqa: E402 - isolated conftest bootstrap must precede app imports
from app.api.deps import get_current_user  # noqa: E402 - isolated conftest bootstrap must precede app imports
from app.audit import AuditLog  # noqa: E402 - isolated conftest bootstrap must precede app imports
from app.core import log_context  # noqa: E402 - isolated conftest bootstrap must precede app imports
from app.core.database import engine  # noqa: E402 - isolated conftest bootstrap must precede app imports
from app.main import app  # noqa: E402 - isolated conftest bootstrap must precede app imports
from app.models.agent_state import AgentRunState  # noqa: E402 - isolated conftest bootstrap must precede app imports
from app.models.conversation import Message  # noqa: E402 - isolated conftest bootstrap must precede app imports
from app.models.user import User  # noqa: E402 - isolated conftest bootstrap must precede app imports
from app.services.chat_service import ChatService  # noqa: E402 - isolated conftest bootstrap must precede app imports
from tests.test_agent_runtime_recovery import (  # noqa: E402 - isolated conftest bootstrap must precede app imports
    ScriptPipeline,
    call,
)
from tests.test_chat_resume import resume_client  # noqa: E402 - isolated conftest bootstrap must precede app imports

assert str(engine.url).endswith("data/test_ai_assistant.db")
with pytest.MonkeyPatch.context() as mp:
    fixture = resume_client.__wrapped__(mp)
    client, payload, user, calls = next(fixture)
    try:

        async def execute(args):
            calls.append(args["n"])
            return "counted-" + str(args["n"])

        reg = ToolRegistry(
            [
                Tool(
                    "calculator",
                    "counter",
                    {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]},
                    execute,
                    contract=ToolContract(
                        version="acceptance-1",
                        side_effect="none",
                        timeout_seconds=3,
                        risk=RiskLevel.L0,
                        permission=ToolPermission(authenticated=True, tenant_scoped=True),
                        strict_validation=True,
                    ),
                )
            ]
        )

        async def tools(self):
            return reg

        mp.setattr(ChatService, "_build_tools", tools)
        pipelines = []

        def build(self, *a, **kw):
            p = ScriptPipeline([call(2), asyncio.CancelledError()], tools=reg)
            pipelines.append(p)
            return p

        mp.setattr(ChatService, "_build_pipeline", build)

        async def cancel():
            with Session(engine) as s:
                log_context.set_user(user.id, user.tenant_id)
                stream = ChatService().chat_stream(
                    s,
                    user,
                    payload["message"],
                    payload["conversation_id"],
                    resume_message_id=payload["resume_message_id"],
                )
                try:
                    async for event in stream:
                        pass
                    raise AssertionError("cancellation expected")
                except asyncio.CancelledError:
                    pass
                finally:
                    await stream.aclose()

        asyncio.run(cancel())
        assert isinstance(pipelines[0].executor, ToolExecutor)
        with Session(engine) as s:
            row = s.exec(
                select(AgentRunState).where(col(AgentRunState.message_id) == payload["resume_message_id"])
            ).one()
            assert row.status == "running" and row.round_no == 2
            assert any("counted-2" in x for x in json.loads(row.state_json)["tool_results"])
            audits = s.exec(
                select(AuditLog).where(col(AuditLog.user_id) == user.id, col(AuditLog.resource_id) == "calculator")
            ).all()
            assert len(audits) == 1 and audits[0].tenant_id == user.tenant_id
            details = audits[0].details
            if isinstance(details, str):
                details = json.loads(details)
            assert set(details) == {"risk", "allowed", "reason", "latency_ms"}
        mp.setattr(ChatService, "_build_pipeline", lambda self, *a, **kw: ScriptPipeline([call(2), "draft"], tools=reg))
        response = client.post("/api/chat/stream", json=payload)
        assert response.status_code == 200 and "event: done" in response.text and calls == [2]
        with Session(engine) as s:
            row = s.exec(
                select(AgentRunState).where(col(AgentRunState.message_id) == payload["resume_message_id"])
            ).one()
            rows = s.exec(select(Message).where(col(Message.conversation_id) == payload["conversation_id"])).all()
            assert row.status == "done" and row.round_no == 3  # duplicate attempt consumes budget
            assert len([m for m in rows if m.role == "user"]) == 1
            assistants = [m for m in rows if m.role == "assistant"]
            assert sorted((m.status, m.content) for m in assistants) == [("complete", "answer"), ("stopped", "")]
        repeat = client.post("/api/chat", json=payload)
        assert repeat.status_code == 409
        actor = User(
            id="foreign-acceptance",
            tenant_id="foreign-acceptance",
            username="foreign",
            hashed_password="synthetic",
            role="system_admin",
        )
        app.dependency_overrides[get_current_user] = lambda: actor
        foreign = client.post("/api/chat/stream", json=payload)
        assert foreign.status_code == 404 and calls == [2]
        with Session(engine) as s:
            assert (
                len(s.exec(select(Message).where(col(Message.conversation_id) == payload["conversation_id"])).all())
                == 3
            )
        print(
            json.dumps(
                {
                    "checkpoint_round_after_cancel": 2,
                    "final_round": 3,
                    "executor_wired": True,
                    "audit_rows": 1,
                    "tool_calls": calls,
                    "user_messages": 1,
                    "assistant_statuses": ["stopped", "complete"],
                    "repeat_http": 409,
                    "foreign_http": 404,
                }
            )
        )
    finally:
        with Session(engine) as s:
            for row in s.exec(select(AuditLog).where(col(AuditLog.user_id) == user.id)).all():
                s.delete(row)
            s.commit()
        fixture.close()
        log_context.clear()
