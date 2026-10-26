"""Persistence for governed, single-conversation compression snapshots."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from sqlmodel import Session

from app.core.config import settings
from app.debug.trace import sanitize_trace_text
from app.memory.base import CompressionStrategy, MemorySnapshot
from app.models.conversation import Conversation, Message
from app.models.memory_snapshot import ConversationMemorySnapshot
from app.models.user import User
from app.services.memory_policy import MEMORY_POLICY_VERSION, assert_memory_access

MEMORY_COMPRESSOR_VERSION = "memory-compressor-v1"


def memory_policy_fingerprint() -> str:
    values = (
        MEMORY_POLICY_VERSION, settings.MEMORY_WINDOW_SIZE,
        settings.MEMORY_COMPRESSION_THRESHOLD, settings.MEMORY_STRATEGY,
        settings.MEMORY_MAX_SUMMARY_CHARS, settings.MEMORY_KEEP_RECENT,
    )
    return hashlib.sha256(repr(values).encode()).hexdigest()


def ordered_messages(conversation: Conversation) -> list[Message]:
    return sorted(conversation.messages, key=lambda row: (row.created_at, row.id))


def prefix_hash(messages: list[Message], count: int) -> str:
    digest = hashlib.sha256()
    for row in messages[:count]:
        digest.update(row.id.encode())
        digest.update(b"\0")
        digest.update(row.role.encode())
        digest.update(b"\0")
        digest.update(row.content.encode())
        digest.update(b"\0")
        digest.update(row.updated_at.isoformat().encode())
        digest.update(b"\n")
    return digest.hexdigest()


class MemorySnapshotService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _invalidate(self, row: ConversationMemorySnapshot, reason: str) -> None:
        row.invalidated_at = datetime.now(UTC)
        row.invalidation_reason = reason[:64]
        self.session.add(row)
        self.session.commit()

    def load(
        self, user: User, conversation: Conversation
    ) -> MemorySnapshot | None:
        assert_memory_access(self.session, user, conversation)
        row = self.session.get(ConversationMemorySnapshot, conversation.id)
        if row is None or row.invalidated_at is not None:
            return None
        now = datetime.now(UTC)
        owner = self.session.get(User, conversation.user_id)
        expires_at = row.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        messages = ordered_messages(conversation)
        valid = (
            row.tenant_id == conversation.tenant_id == user.tenant_id
            and row.user_id == conversation.user_id
            and owner is not None
            and owner.is_active
            and row.user_token_version == owner.token_version
            and row.policy_version == memory_policy_fingerprint()
            and row.compressor_version == MEMORY_COMPRESSOR_VERSION
            and expires_at >= now
            and 0 <= row.compressed_count <= len(messages)
            and row.prefix_hash == prefix_hash(messages, row.compressed_count)
        )
        if not valid:
            self._invalidate(row, "snapshot_binding_changed")
            return None
        return MemorySnapshot(
            summary=row.summary,
            compressed_count=row.compressed_count,
            compressed_at=row.updated_at.isoformat(),
            strategy=CompressionStrategy(row.strategy),
        )

    def save(
        self, user: User, conversation: Conversation, snapshot: MemorySnapshot
    ) -> ConversationMemorySnapshot | None:
        assert_memory_access(self.session, user, conversation)
        if snapshot.is_empty or not snapshot.summary.strip():
            return None
        messages = ordered_messages(conversation)
        owner = self.session.get(User, conversation.user_id)
        if owner is None or not owner.is_active:
            return None
        if snapshot.compressed_count <= 0 or snapshot.compressed_count > len(messages):
            return None
        row = self.session.get(ConversationMemorySnapshot, conversation.id)
        if row is None:
            row = ConversationMemorySnapshot(
                conversation_id=conversation.id,
                tenant_id=conversation.tenant_id,
                user_id=conversation.user_id,
                user_token_version=owner.token_version,
                summary="",
                compressed_count=0,
                prefix_hash=prefix_hash(messages, 0),
                strategy=snapshot.strategy.value,
                policy_version=memory_policy_fingerprint(),
                compressor_version=MEMORY_COMPRESSOR_VERSION,
                expires_at=datetime.now(UTC),
            )
        row.tenant_id = conversation.tenant_id
        row.user_id = conversation.user_id
        row.user_token_version = owner.token_version
        row.summary = sanitize_trace_text(snapshot.summary)[: settings.MEMORY_MAX_SUMMARY_CHARS]
        row.compressed_count = snapshot.compressed_count
        row.prefix_hash = prefix_hash(messages, snapshot.compressed_count)
        row.strategy = snapshot.strategy.value
        row.policy_version = memory_policy_fingerprint()
        row.compressor_version = MEMORY_COMPRESSOR_VERSION
        row.expires_at = datetime.now(UTC) + timedelta(days=settings.MEMORY_SNAPSHOT_RETENTION_DAYS)
        row.invalidated_at = None
        row.invalidation_reason = None
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def invalidate(self, user: User, conversation: Conversation, reason: str) -> None:
        assert_memory_access(self.session, user, conversation)
        row = self.session.get(ConversationMemorySnapshot, conversation.id)
        if row is not None and row.invalidated_at is None:
            self._invalidate(row, reason)
