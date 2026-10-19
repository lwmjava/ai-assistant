"""在隔离数据中复现未修改的基线重新生成排序缺陷，不作为功能通过证据。"""

import ast
import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlmodel import Session, create_engine, select  # noqa: E402

import tests.conftest  # noqa: E402,F401 -- strip credentials and block external network before app imports
from app.llm.mock import MockLLMProvider  # noqa: E402
from app.models.conversation import Conversation, Message  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.agent_execution_guard import ResumeRejectedError  # noqa: E402


def regeneration_ast(source):
    service = next(node for node in ast.parse(source).body
                   if isinstance(node, ast.ClassDef) and node.name == "ChatService")
    method = next(node for node in service.body
                  if isinstance(node, ast.AsyncFunctionDef) and node.name == "regenerate")
    return ast.dump(method, include_attributes=False)


baseline = subprocess.check_output(["git", "show", "d886560:app/services/chat_service.py"], cwd=ROOT, encoding="utf-8")
current = (ROOT / "app/services/chat_service.py").read_text(encoding="utf-8")
assert regeneration_ast(baseline) == regeneration_ast(current)
namespace = {"__name__": "timestamp_repro_baseline"}
exec(compile(baseline, "baseline_chat_service.py", "exec"), namespace)
database = ROOT / "data/v1c003-baseline-20261019/timestamp-repro-baseline.db"
source = ROOT / "data/pytest-tmp/v1c003-full2/test_regenerate_direct_reply_s0/lifecycle.db"
shutil.copyfile(source, database)
engine = create_engine(f"sqlite:///{database.as_posix()}")


async def reproduce():
    with Session(engine) as session:
        rows = list(session.exec(select(Message)))
        assert len(rows) == 2 and rows[0].created_at == rows[1].created_at
        answer = next(row for row in rows if row.role == "assistant")
        conversation = session.get(Conversation, answer.conversation_id)
        actor = session.get(User, conversation.user_id)
        assert actor is not None
        try:
            await namespace["ChatService"](MockLLMProvider()).regenerate(
                session, actor, conversation.id, answer.id,
            )
        except ResumeRejectedError as error:
            assert error.status_code == 409 and "最后一条助手消息" in str(error)
        else:
            raise AssertionError("expected the baseline timestamp-tie defect")
    with Session(engine) as fresh:
        assert len(list(fresh.exec(select(Message)))) == 2


asyncio.run(reproduce())
engine.dispose()
print(json.dumps({"result": "pre_existing_defect_reproduced", "baseline_sha": "d886560",
                  "regenerate_ast_unchanged": True, "equal_created_at": True,
                  "rejection": "baseline 409 before any chat/model call", "messages_after": 2}))
