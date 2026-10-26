"""Persistence and authorization for redacted run history."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, col, func, select

from app.core.config import settings
from app.debug.trace import AgentTrace
from app.models.run_history import RunHistory
from app.models.user import User
from app.schemas.run_history import RunEventOut, RunHistoryDetail, RunHistoryOut


class RunHistoryAccessError(LookupError):
    pass


def _events(trace: AgentTrace) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    starts: dict[str, float] = {}
    for event in trace.events[: settings.RUN_HISTORY_MAX_EVENTS]:
        offset = max(0.0, (event.timestamp - trace.started_at) * 1000)
        row: dict[str, object] = {
            "type": event.type[:32],
            "name": event.name[:128],
            "offset_ms": round(offset, 2),
        }
        if event.type == "stage_start":
            starts[event.name] = event.timestamp
        elif event.type == "stage_end" and event.name in starts:
            row["duration_ms"] = round(max(0.0, event.timestamp - starts[event.name]) * 1000, 2)
        if event.type == "tool_call":
            row["status"] = str(event.data.get("status", "unknown"))[:32]
            round_no = event.data.get("round")
            if isinstance(round_no, int) and not isinstance(round_no, bool):
                row["round"] = round_no
            latency = event.data.get("latency_ms")
            if isinstance(latency, int | float) and not isinstance(latency, bool):
                row["duration_ms"] = round(max(0.0, float(latency)), 2)
        elif event.type == "llm_call":
            latency = event.data.get("latency_ms")
            if isinstance(latency, int | float) and not isinstance(latency, bool):
                row["duration_ms"] = round(max(0.0, float(latency)), 2)
        result.append(row)
    return result


class RunHistoryService:
    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def _is_admin(user: User) -> bool:
        return user.role in {"system_admin", "tenant_admin"}

    @staticmethod
    def _visible(user: User, row: RunHistory) -> bool:
        return row.tenant_id == user.tenant_id and (
            row.user_id == user.id or RunHistoryService._is_admin(user)
        )

    def record(self, user: User, conversation_id: str, trace: AgentTrace) -> RunHistory:
        now = datetime.now(UTC)
        duration = max(0.0, trace.duration_ms)
        summary = trace.summary()
        row = RunHistory(
            id=trace.run_id,
            tenant_id=user.tenant_id,
            user_id=user.id,
            conversation_id=conversation_id,
            status="failed" if trace.error else "succeeded",
            error_code=(trace.error if trace.error in {"run_cancelled", "run_interrupted"}
                        else "run_failed" if trace.error else None),
            started_at=now - timedelta(milliseconds=duration),
            finished_at=now,
            duration_ms=round(duration, 2),
            llm_call_count=int(summary["llm_call_count"]),
            tool_call_count=int(summary["tool_call_count"]),
            events_json=json.dumps(_events(trace), ensure_ascii=False, separators=(",", ":")),
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def list(
        self, user: User, *, page: int = 1, page_size: int = 20, conversation_id: str | None = None
    ) -> tuple[list[RunHistory], int]:
        cutoff = datetime.now(UTC) - timedelta(days=settings.RUN_HISTORY_RETENTION_DAYS)
        clauses = [
            col(RunHistory.tenant_id) == user.tenant_id,
            col(RunHistory.created_at) >= cutoff,
        ]
        if not self._is_admin(user):
            clauses.append(col(RunHistory.user_id) == user.id)
        if conversation_id:
            clauses.append(col(RunHistory.conversation_id) == conversation_id)
        count = int(self.session.exec(select(func.count()).select_from(RunHistory).where(*clauses)).one())
        rows = list(
            self.session.exec(
                select(RunHistory)
                .where(*clauses)
                .order_by(col(RunHistory.created_at).desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        )
        return rows, count

    def get(self, user: User, run_id: str) -> RunHistoryDetail:
        row = self.session.get(RunHistory, run_id)
        cutoff = datetime.now(UTC) - timedelta(days=settings.RUN_HISTORY_RETENTION_DAYS)
        created_at = None if row is None else row.created_at
        if created_at is not None and created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        if row is None or created_at is None or created_at < cutoff or not self._visible(user, row):
            raise RunHistoryAccessError
        values = RunHistoryOut.model_validate(row).model_dump()
        events = [RunEventOut.model_validate(item) for item in json.loads(row.events_json)]
        return RunHistoryDetail(**values, events=events)
