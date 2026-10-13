"""AgentStateStore 的服务层实现（PAGT-006，ADR-0010 决定2/决定3）。

持 Session 做 checkpoint 的 upsert / 活动行读取 / 丢弃。agents 层（StateManager）不碰 DB，
通过注入本类实现 ``AgentStateStore`` Protocol。

语义：
- upsert 按 (conversation_id, message_id) 唯一行；message_id 为 None 时按该会话的活动行；
- 读取时跳过过期行、丢弃损坏行；绝不覆盖其他活动行；失败不回灌用户可见文本。
- 写入/读取失败均降级不阻断主链（与审计写路径一致）。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlmodel import Session, col, select

from app.agents.state import StateManager
from app.core.config import settings
from app.models.agent_state import AgentRunState

logger = logging.getLogger(__name__)

# 只有「中断中」状态的行才可被恢复续跑；终态 done/error 绝不续跑（PAGT-006 修复轮）。
RESUMABLE_STATUS = frozenset({"running", "suspended"})


def _utcnow_naive() -> datetime:
    """naive UTC（SQLite DATETIME 读写一致，避免 aware/naive 比较 TypeError）。"""
    return datetime.now(UTC).replace(tzinfo=None)


def cleanup_expired_checkpoints(session: Session, *, limit: int = 100, tenant_id: str | None = None) -> int:
    """Delete a bounded batch of expired snapshots, retaining concurrently refreshed rows."""
    if limit < 1:
        raise ValueError("cleanup limit must be positive")
    now = _utcnow_naive()
    try:
        statement = select(AgentRunState.id).where(col(AgentRunState.expires_at) < now)
        if tenant_id is not None:
            statement = statement.where(col(AgentRunState.tenant_id) == tenant_id)
        ids = session.exec(statement.order_by(col(AgentRunState.expires_at), col(AgentRunState.id)).limit(limit)).all()
        if not ids:
            return 0
        result = session.execute(delete(AgentRunState).where(
            col(AgentRunState.id).in_(ids), col(AgentRunState.expires_at) < now))
        session.commit()
        return int(getattr(result, "rowcount", 0))
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.warning("Agent checkpoint cleanup unavailable error_type=%s", type(exc).__name__)
        return 0


class AgentStateService:
    """实现 ``AgentStateStore`` Protocol 的服务层（按请求持有 Session）。"""

    def __init__(self, session: Session, *, tenant_id: str, user_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._user_id = user_id

    def _find_row(self, conversation_id: str, message_id: str | None) -> AgentRunState | None:
        statement = select(AgentRunState).where(
            col(AgentRunState.conversation_id) == conversation_id,
            col(AgentRunState.tenant_id) == self._tenant_id,
            col(AgentRunState.user_id) == self._user_id,
        )
        statement = statement.where(col(AgentRunState.message_id).is_(None) if message_id is None
                                    else col(AgentRunState.message_id) == message_id)
        return self._session.exec(statement).first()

    def save_checkpoint(
        self,
        *,
        conversation_id: str,
        message_id: str | None,
        tenant_id: str,
        user_id: str,
        stage: str,
        round_no: int,
        tool_budget_exhausted: bool,
        state_json: str,
        status: str,
        error: str | None,
    ) -> None:
        try:
            if tenant_id != self._tenant_id or user_id != self._user_id:
                raise ValueError("checkpoint identity mismatch")
            expires_at = _utcnow_naive() + timedelta(seconds=settings.AGENT_STATE_TTL_SECONDS)
            row = self._find_row(conversation_id, message_id)
            if row is None:
                row = AgentRunState(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    stage=stage,
                    round_no=round_no,
                    tool_budget_exhausted=tool_budget_exhausted,
                    state_json=state_json,
                    status=status,
                    error=error,
                    expires_at=expires_at,
                )
                self._session.add(row)
            else:
                row.stage = stage
                row.round_no = round_no
                row.tool_budget_exhausted = tool_budget_exhausted
                row.state_json = state_json
                row.status = status
                row.error = error
                row.expires_at = expires_at
            self._session.commit()
        except Exception:  # noqa: BLE001 — checkpoint 失败不阻断主链
            logger.exception("Agent checkpoint 写入失败（已回滚，不阻断主链）")
            self._session.rollback()

    def load_active(self, conversation_id: str, message_id: str | None) -> dict | None:
        try:
            return self._load_active(conversation_id, message_id)
        except Exception as exc:  # noqa: BLE001
            self._session.rollback()
            logger.warning("Agent checkpoint read unavailable error_type=%s", type(exc).__name__)
            return None

    def _load_active(self, conversation_id: str, message_id: str | None) -> dict | None:
        row = self._find_row(conversation_id, message_id)
        if row is None:
            return None
        # 终态（done/error）绝不续跑：直接丢弃该行，按全新 run（PAGT-006 修复轮）。
        if row.status not in RESUMABLE_STATUS:
            self.discard(conversation_id, message_id)
            return None
        # 过期 → 丢弃，按全新 run。
        if row.expires_at is not None and row.expires_at < _utcnow_naive():
            self.discard(conversation_id, message_id)
            return None
        try:
            data = json.loads(row.state_json)
        except (json.JSONDecodeError, TypeError):
            # 损坏 → 丢弃该行，按全新 run，不改写其他行。
            self.discard(conversation_id, message_id)
            return None
        if not isinstance(data, dict):
            self.discard(conversation_id, message_id)
            return None
        data["_status"] = row.status
        data["_round_no"] = row.round_no
        data["tool_budget_exhausted"] = row.tool_budget_exhausted or data.get("tool_budget_exhausted", False)
        if not StateManager.valid_snapshot(data):
            self.discard(conversation_id, message_id)
            return None
        return data

    def cleanup_expired(self, *, limit: int = 100) -> int:
        """Remove expired rows in this service's tenant only."""
        return cleanup_expired_checkpoints(self._session, limit=limit, tenant_id=self._tenant_id)

    def discard(self, conversation_id: str, message_id: str | None) -> None:
        row = self._find_row(conversation_id, message_id)
        if row is None:
            return
        try:
            self._session.delete(row)
            self._session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("Agent checkpoint 丢弃失败（已回滚）")
            self._session.rollback()
