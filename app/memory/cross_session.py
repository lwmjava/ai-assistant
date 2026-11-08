"""Store and recall cross-session memory under the conversation memory policy.

Writing is explicit. Turning the feature off stops new writes and recalls
without deleting rows.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, col, select

from app.core.config import settings
from app.models.conversation import Conversation, Message
from app.models.cross_session_memory import CrossSessionMemory
from app.models.user import User
from app.rag.embeddings.factory import get_embedding_provider
from app.rag.vectorstore import memory_index
from app.rag.vectorstore.milvus import MilvusUnavailableError
from app.services.chat_service import HISTORY_REDACTED, sanitize_history_text
from app.services.memory_policy import (
    MemoryPolicyError,
    assert_memory_access,
    assert_memory_subject,
)

POLICY_VERSION = "cross-session-memory-v1"


class CrossSessionMemoryService:
    def __init__(self, session: Session) -> None:
        self.session = session

    async def remember(self, user: User, conversation_id: str, message_id: str) -> str | None:
        if not settings.MEMORY_CROSS_SESSION_ENABLED:
            return None
        conversation = self.session.get(Conversation, conversation_id)
        message = self.session.get(Message, message_id)
        if (
            conversation is None
            or message is None
            or message.conversation_id != conversation.id
            or conversation.deleted_at is not None
        ):
            raise MemoryPolicyError("conversation memory is unavailable")
        assert_memory_access(self.session, user, conversation)
        if conversation.user_id != user.id:
            raise MemoryPolicyError("conversation memory is unavailable")
        content = sanitize_history_text(message.content or "")[: settings.MEMORY_MAX_SUMMARY_CHARS]
        if not content.strip() or content == HISTORY_REDACTED:
            raise MemoryPolicyError("conversation memory is unavailable")
        provider = get_embedding_provider()
        vectors = await provider.embed([content])
        vector = memory_index.normalize(list(vectors[0]))
        if len(vector) != int(provider.dim):
            raise RuntimeError("嵌入维度与提供商不一致")
        current = self.session.get(User, user.id)
        if current is None:
            raise MemoryPolicyError("conversation memory is unavailable")
        row = CrossSessionMemory(
            id=uuid.uuid4().hex,
            tenant_id=user.tenant_id,
            user_id=user.id,
            user_token_version=current.token_version,
            source_conversation_id=conversation.id,
            source_message_id=message.id,
            content=content,
            embedding_json=json.dumps(vector),
            embedding_provider=settings.EMBEDDING_PROVIDER.strip().lower() or "mock",
            embedding_model=str(provider.model),
            embedding_dim=len(vector),
            policy_version=POLICY_VERSION,
            expires_at=datetime.now(UTC) + timedelta(days=settings.MEMORY_SNAPSHOT_RETENTION_DAYS),
        )
        self.session.add(row)
        self.session.flush()
        try:
            await memory_index.upsert(row.id, row.tenant_id, row.user_id, vector)
        except Exception:
            self.session.rollback()
            raise
        self.session.commit()
        return row.id

    async def recall_text(self, user: User, query: str) -> str:
        try:
            rows = await self._recall(user, query)
        except MilvusUnavailableError:
            return ""
        except MemoryPolicyError:
            return ""
        if not rows:
            return ""
        lines = ["[跨会话记忆] 以下内容来自当前用户此前明确保存的消息，不是本轮原文。"]
        for row in rows:
            lines.append(
                f"- 来源会话 {row.source_conversation_id} 消息 {row.source_message_id}：{row.content}"
            )
        return "\n".join(lines)

    async def _recall(self, user: User, query: str) -> list[CrossSessionMemory]:
        if not settings.MEMORY_CROSS_SESSION_ENABLED:
            return []
        assert_memory_subject(self.session, user)
        text = (query or "").strip()
        if not text:
            return []
        provider = get_embedding_provider()
        vectors = await provider.embed([text])
        query_vector = memory_index.normalize(list(vectors[0]))
        candidates = self._candidates(user, provider.model, len(query_vector))
        ids = await memory_index.search_ids(
            user.tenant_id,
            None if user.role == "system_admin" else user.id,
            query_vector,
            settings.MEMORY_CROSS_SESSION_TOP_K,
        )
        if ids is None:
            return memory_index.rank_local(candidates, query_vector, settings.MEMORY_CROSS_SESSION_TOP_K)
        allowed = {row.id: row for row in candidates}
        return [allowed[item] for item in ids if item in allowed]

    def _candidates(self, user: User, model: str, dim: int) -> list[CrossSessionMemory]:
        now = datetime.now(UTC)
        statement = select(CrossSessionMemory).where(
            col(CrossSessionMemory.tenant_id) == user.tenant_id,
            col(CrossSessionMemory.invalidated_at).is_(None),
            col(CrossSessionMemory.expires_at) > now,
            col(CrossSessionMemory.policy_version) == POLICY_VERSION,
            col(CrossSessionMemory.embedding_model) == str(model),
            col(CrossSessionMemory.embedding_dim) == dim,
        )
        if user.role != "system_admin":
            statement = statement.where(col(CrossSessionMemory.user_id) == user.id)
        rows = list(self.session.exec(statement).all())
        visible: list[CrossSessionMemory] = []
        for row in rows:
            owner = self.session.get(User, row.user_id)
            conversation = self.session.get(Conversation, row.source_conversation_id)
            if (
                owner is None
                or not owner.is_active
                or owner.tenant_id != row.tenant_id
                or owner.token_version != row.user_token_version
                or conversation is None
                or conversation.deleted_at is not None
                or conversation.tenant_id != row.tenant_id
            ):
                continue
            if user.role != "system_admin" and row.user_id != user.id:
                continue
            visible.append(row)
        return visible
