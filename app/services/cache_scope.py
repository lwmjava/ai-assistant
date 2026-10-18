"""Fresh SQL authorization/data fingerprints for optional response caches."""

from __future__ import annotations

import functools
import math
from collections.abc import AsyncIterator, Callable
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.sql.selectable import Select
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.response_cache import bind_cache_scope, configuration_stamp, digest, object_namespace
from app.core.security import Role
from app.models.rag import Document, DocumentChunk, EmbeddingIndex
from app.models.user import Tenant, User
from app.rag.access import ReadScope, read_scope_for
from app.rag.effective_date import document_version_status, retrieval_window
from app.rag.vectorstore.base import ChunkResult

_WRITE_MARKER = "response_cache_transaction_write"


@event.listens_for(Engine, "before_execute")
def _track_transaction_writes(
    connection: Connection, statement: Any, multiparams: Any, params: Any, execution_options: Any
) -> None:
    # Only structured SELECT is provably read-only. Textual SQL is conservative.
    if not isinstance(statement, Select):
        connection.info[_WRITE_MARKER] = True


@event.listens_for(Engine, "commit")
@event.listens_for(Engine, "rollback")
def _finish_transaction(connection: Connection) -> None:
    connection.info.pop(_WRITE_MARKER, None)


class AuthorizedCacheScope:
    """Frozen request principal, reread through a separate committed SQL session.

    Unpersisted development identities and pending writes bypass caching. Revoked
    persisted identities fail closed; no stale ORM identity map can authorize a hit.
    """

    def __init__(self, session: Session, reader: User | None, tenant_id: str) -> None:
        self.session = session
        self.tenant_id = tenant_id
        self.reader_id = reader.id if reader is not None else None
        self.expected = ((reader.tenant_id, reader.role, reader.token_version)
                         if reader is not None else None)
        self.read_scope = read_scope_for(reader)
        self.bind = session.get_bind()
        with Session(self.bind) as fresh:
            self.persisted = self.reader_id is not None and fresh.get(User, self.reader_id) is not None

    def _reader(self, fresh: Session) -> User:
        user = fresh.get(User, self.reader_id)
        if (user is None or not user.is_active or user.tenant_id != self.tenant_id
                or (user.tenant_id, user.role, user.token_version) != self.expected
                or read_scope_for(user) != self.read_scope):
            raise PermissionError("缓存请求授权已变更，请重新发起请求")
        tenant = fresh.get(Tenant, self.tenant_id)
        if tenant is None or (not tenant.is_active and user.role_enum != Role.SYSTEM_ADMIN):
            raise PermissionError("缓存请求租户不可用")
        return user

    def snapshot(self, query: str = "") -> str | None:
        if not self.persisted:
            return None
        if self.session.new or self.session.dirty or self.session.deleted:
            return None
        # Connection-bound transactional sessions cannot be read as committed leases.
        if not isinstance(self.bind, Engine) or self.session.get_bind() is not self.bind:
            return None
        if self.session.in_transaction():
            connection = self.session.connection()
            if connection.info.get(_WRITE_MARKER):
                return None
            # SQLite DBAPI writes can bypass SQLAlchemy's execution hooks entirely.
            if (self.bind.dialect.name == "sqlite"
                    and getattr(connection.connection.driver_connection, "in_transaction", False)):
                return None
        with Session(self.bind) as fresh:
            user = self._reader(fresh)
            now, schedule_at = retrieval_window(query)
            documents = fresh.exec(select(Document).where(
                col(Document.tenant_id) == self.tenant_id).order_by(col(Document.id))).all()
            chunks = fresh.exec(select(DocumentChunk).where(
                col(DocumentChunk.tenant_id) == self.tenant_id).order_by(col(DocumentChunk.id))).all()
            indexes = fresh.exec(select(EmbeddingIndex).order_by(col(EmbeddingIndex.id))).all()
            temporal = [(d.id, document_version_status(
                is_current=d.is_current, effective_at=d.effective_at, expires_at=d.expires_at,
                as_of=now, schedule_at=schedule_at)) for d in documents]
            return digest((object_namespace(self.bind), user.id, self.expected, self.read_scope,
                           [d.model_dump() for d in documents], [c.model_dump() for c in chunks],
                           [i.model_dump() for i in indexes], temporal, configuration_stamp()))

    def validate_hits(self, hits: list[ChunkResult], query: str, index_id: str | None) -> bool:
        with Session(self.bind) as fresh:
            user = self._reader(fresh)
            scope: ReadScope | None = read_scope_for(user)
            now, schedule_at = retrieval_window(query)
            for hit in hits:
                row = fresh.get(DocumentChunk, hit.id)
                doc = fresh.get(Document, hit.document_id)
                if (row is None or doc is None or row.document_id != doc.id
                        or row.tenant_id != self.tenant_id or doc.tenant_id != self.tenant_id
                        or doc.deleted_at is not None or row.index_id != index_id
                        or row.content != hit.content or row.source != hit.source
                        or (scope is not None and scope.uploader_id is not None
                            and doc.user_id != scope.uploader_id)
                        or not math.isfinite(hit.score) or not math.isfinite(hit.similarity)):
                    return False
                status = (document_version_status(
                    is_current=doc.is_current, effective_at=doc.effective_at, expires_at=doc.expires_at,
                    as_of=now, schedule_at=schedule_at) if settings.RAG_EFFECTIVE_DATE_FILTER
                    else "current" if doc.is_current else None)
                if status is None or status != hit.version_status:
                    return False
            return True


def cached_chat_scope(method: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(method)
    async def wrapped(self: Any, session: Session, user: User, *args: Any, **kwargs: Any) -> Any:
        if kwargs.get("_fresh_response"):
            with bind_cache_scope(lambda: None):
                return await method(self, session, user, *args, **kwargs)
        if not settings.LLM_RESPONSE_CACHE_ENABLED:
            return await method(self, session, user, *args, **kwargs)
        scope = AuthorizedCacheScope(session, user, user.tenant_id)
        with bind_cache_scope(scope.snapshot):
            return await method(self, session, user, *args, **kwargs)
    return wrapped


def _uncached_snapshot() -> str | None:
    return None


def cached_stream_scope(method: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(method)
    async def wrapped(self: Any, session: Session, user: User, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        scope = (AuthorizedCacheScope(session, user, user.tenant_id)
                 if settings.LLM_RESPONSE_CACHE_ENABLED else None)
        iterator = method(self, session, user, *args, **kwargs)
        try:
            while True:
                try:
                    # Do not leave a ContextVar bound while yielding to the caller.
                    with bind_cache_scope(scope.snapshot if scope is not None else _uncached_snapshot):
                        event = await anext(iterator)
                except StopAsyncIteration:
                    break
                yield event
        finally:
            await iterator.aclose()
    return wrapped
