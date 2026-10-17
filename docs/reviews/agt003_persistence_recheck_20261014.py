"""Read committed isolated acceptance data in a fresh process/session."""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

assert os.environ.get("DATABASE_URL") == "sqlite:///./data/test_ai_assistant.db"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from sqlmodel import Session, col, select  # noqa: E402

from app.core.database import engine  # noqa: E402
from app.models.conversation import Conversation, Message  # noqa: E402

checks = []
started_at = datetime.fromisoformat(os.environ["ACCEPTANCE_STARTED_AT"])
for route in ("YES", "NO"):
    with Session(engine) as session:
        rows = session.exec(
            select(Message)
            .join(Conversation, col(Conversation.id) == col(Message.conversation_id))
            .where(
                col(Conversation.user_id) == "agt003-" + route,
                col(Conversation.tenant_id) == "agt003-tenant",
                col(Message.role) == "assistant",
                col(Message.code_results).is_not(None),
                col(Message.created_at) >= started_at,
            )
        ).all()
        assert any(
            item.get("stdout") == "persisted"
            for row in rows
            for item in json.loads(row.code_results or "[]")
        ), route
        checks.append({"route": route, "new_session_committed_code_result": True})
print(json.dumps({"evidence": "E2-isolated-test-db-new-process-new-session", "checks": checks}))
