"""ADR-0003 选项 A：生效日期过滤（ED-01..05）。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.rag import Document
from app.rag.effective_date import (
    SCHEDULED_NOTICE,
    parse_query_schedule_at,
    retrieval_window,
)
from app.rag.embeddings.mock import MockEmbeddingProvider
from app.rag.retriever import format_context
from app.rag.service import RAGService
from app.rag.vectorstore.base import ChunkResult
from tests.eval.harness import build_eval_index


def test_parse_query_schedule_at_requires_future_date():
    as_of = datetime(2026, 9, 19, tzinfo=UTC)
    assert parse_query_schedule_at("现在标准套餐一个月收费多少？", as_of) is None
    assert parse_query_schedule_at("今天是 2026-09-13。月费是 249 吗？", as_of) is None
    parsed = parse_query_schedule_at("2027 年标准套餐打算卖多少钱？", as_of)
    assert parsed == datetime(2027, 1, 1, tzinfo=UTC)
    iso = parse_query_schedule_at("自 2027-01-01 起卖多少？", as_of)
    assert iso == datetime(2027, 1, 1, tzinfo=UTC)


def test_format_context_marks_scheduled_chunks():
    rendered = format_context(
        [
            ChunkResult(
                id="c1",
                content="预告月费 249 元。",
                source="预告",
                document_id="d-future",
                score=1.0,
                version_status="scheduled",
            )
        ]
    )
    assert SCHEDULED_NOTICE in rendered
    assert "预告月费 249 元。" in rendered


async def _billing_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, flag: bool):
    monkeypatch.setattr("app.core.config.settings.RAG_EFFECTIVE_DATE_FILTER", flag)
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'ed.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    embedding = MockEmbeddingProvider(dim=64)
    rag = RAGService(session, "tenant-a", embedding_provider=embedding)
    current = await rag.ingest_text(
        "现行标准套餐月费为 199 元。",
        "现行计费",
        "current",
        "owner-a",
        is_current=True,
        effective_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    stale = await rag.ingest_text(
        "已废止旧价 99 元，不得再引用。",
        "旧版计费",
        "stale",
        "owner-a",
        is_current=False,
        effective_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    future = await rag.ingest_text(
        "自 2027-01-01 起，标准套餐月费调整为 249 元。在该生效日之前不得当现行价。",
        "2027 预告",
        "future",
        "owner-a",
        is_current=False,
        effective_at=datetime(2027, 1, 1, tzinfo=UTC),
    )
    return session, rag, current, stale, future


async def test_ed01_current_query_excludes_future_and_stale(tmp_path: Path, monkeypatch):
    session, rag, current, stale, future = await _billing_index(tmp_path, monkeypatch, flag=True)
    try:
        hits = await rag.search("现在标准套餐一个月收费多少？", top_k=10)
        ids = {hit.document_id for hit in hits}
        assert current.id in ids
        assert future.id not in ids
        assert stale.id not in ids
        assert all(hit.version_status == "current" for hit in hits)
    finally:
        session.close()


async def test_ed02_future_dated_query_opens_scheduled(tmp_path: Path, monkeypatch):
    session, rag, current, stale, future = await _billing_index(tmp_path, monkeypatch, flag=True)
    try:
        hits = await rag.search("2027 年标准套餐打算卖多少钱？", top_k=10)
        ids = {hit.document_id for hit in hits}
        assert future.id in ids
        assert stale.id not in ids
        scheduled = [hit for hit in hits if hit.document_id == future.id]
        assert scheduled
        assert all(hit.version_status == "scheduled" for hit in scheduled)
        assert SCHEDULED_NOTICE in format_context(scheduled)
    finally:
        session.close()


async def test_ed03_undated_249_question_does_not_open_preview(tmp_path: Path, monkeypatch):
    session, rag, current, stale, future = await _billing_index(tmp_path, monkeypatch, flag=True)
    try:
        hits = await rag.search("现在是否已经是 249？", top_k=10)
        ids = {hit.document_id for hit in hits}
        assert future.id not in ids
        assert current.id in ids
    finally:
        session.close()


async def test_ed04_cross_tenant_scheduled_still_hidden(tmp_path: Path, monkeypatch):
    session, rag, _current, _stale, _future = await _billing_index(
        tmp_path, monkeypatch, flag=True
    )
    try:
        other = RAGService(
            session, "tenant-b", embedding_provider=MockEmbeddingProvider(dim=64)
        )
        foreign = await other.ingest_text(
            "青禾 2027 年内部结算预告价 88 元。",
            "青禾预告",
            "tenant-b-future",
            "owner-b",
            is_current=False,
            effective_at=datetime(2027, 1, 1, tzinfo=UTC),
        )
        hits = await rag.search("2027 年标准套餐打算卖多少钱？", top_k=10)
        assert foreign.id not in {hit.document_id for hit in hits}
    finally:
        session.close()


async def test_ed05_flag_off_matches_pre_adr_behavior(tmp_path: Path, monkeypatch):
    session, rag, current, stale, future = await _billing_index(tmp_path, monkeypatch, flag=False)
    try:
        as_of, schedule_at = retrieval_window("2027 年标准套餐打算卖多少钱？")
        assert schedule_at is None
        hits = await rag.search("2027 年标准套餐打算卖多少钱？", top_k=10)
        ids = {hit.document_id for hit in hits}
        assert future.id not in ids
        assert stale.id not in ids
        assert current.id in ids
    finally:
        session.close()


async def test_eval_index_writes_manifest_dates(tmp_path: Path):
    index = await build_eval_index(tmp_path / "eval_dates.db", MockEmbeddingProvider(dim=64))
    try:
        docs = {index.db_doc_id_to_logical[row.id]: row for row in index.session.exec(select(Document)).all()}
        future = docs["doc-billing-future"]
        stale = docs["doc-billing-stale"]
        current = docs["doc-billing-current"]
        assert future.is_current is False
        assert stale.is_current is False
        assert current.is_current is True
        assert future.effective_at is not None
        assert future.effective_at.year == 2027
        assert current.effective_at is not None
        assert current.effective_at.year == 2026
    finally:
        index.close()
