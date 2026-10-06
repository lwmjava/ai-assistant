"""RAG-016 父块展开锁定测试（夜间 2026-10-06）。

锁定知识库搜索（``RAGService.search``）的父块展开边界行为，作为回归基线：

1. 命中父块本身 → 只返回父块，不向下追加子块。
2. 子块的父块记录缺失 → 安全跳过，不崩溃。
3. 展开的父块继承子块 ``version_status``。
4. 只展开一层（不递归到祖父块）。

这些用例不改变实现契约；任何用例失败即表示展开行为漂移。
复用 ``tests/test_rag.py`` 的 session fixture 与 Mock 混合检索模式。
"""

from typing import cast

import pytest
from sqlmodel import Session, col, select

from app.core.database import engine, init_db
from app.models.rag import DocumentChunk
from app.rag.backend.native import NativeRagBackend
from app.rag.service import RAGService
from app.rag.vectorstore.base import ChunkResult, VectorStore


@pytest.fixture()
def session():
    init_db()
    with Session(engine) as s:
        yield s


def _native_store(rag: RAGService) -> VectorStore:
    """RAGService 默认 native backend 的本地 store（注入伪检索用）。"""
    return cast(NativeRagBackend, rag._backend)._store


async def _fetch_parent_child(
    session: Session, doc_id: str
) -> tuple[DocumentChunk, DocumentChunk]:
    """按 ingest 顺序取该文档的父块与第一个子块。"""
    parent = session.exec(
        select(DocumentChunk).where(
            col(DocumentChunk.document_id) == doc_id,
            col(DocumentChunk.parent_id).is_(None),
        )
    ).first()
    assert parent is not None
    child = session.exec(
        select(DocumentChunk).where(
            col(DocumentChunk.document_id) == doc_id,
            col(DocumentChunk.parent_id) == parent.id,
        )
    ).first()
    assert child is not None
    return parent, child


async def test_parent_hit_not_expand_downward(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """命中父块本身时，不向下追加其子块（只向上展开）。"""
    rag = RAGService(session, "pc-down-lock")
    doc = await rag.ingest_text(
        "父块内容。子块内容A。",
        title="父块命中",
        source="test",
        user_id="pc-down-user",
        strategy="parent_child",
    )
    parent, child = await _fetch_parent_child(session, doc.id)

    async def fake_hybrid_search(*args, **kwargs):
        return [
            ChunkResult(
                id=parent.id,
                content=parent.content,
                source="test",
                document_id=doc.id,
                score=0.9,
                similarity=0.7,
            )
        ]

    monkeypatch.setattr(_native_store(rag), "hybrid_search", fake_hybrid_search)
    results = await rag.search("父块")
    ids = [r.id for r in results]
    assert parent.id in ids
    assert child.id not in ids, "父块命中不应向下追加子块"


async def test_child_with_missing_parent_skipped(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """子块 parent_id 指向不存在的父块记录时，安全跳过不崩溃。"""
    rag = RAGService(session, "pc-orphan")
    doc = await rag.ingest_text(
        "父块内容。子块内容B。",
        title="孤儿子块",
        source="test",
        user_id="pc-orphan-user",
        strategy="parent_child",
    )
    parent, child = await _fetch_parent_child(session, doc.id)
    session.delete(parent)
    session.commit()

    async def fake_hybrid_search(*args, **kwargs):
        return [
            ChunkResult(
                id=child.id,
                content=child.content,
                source="test",
                document_id=doc.id,
                score=0.8,
            )
        ]

    monkeypatch.setattr(_native_store(rag), "hybrid_search", fake_hybrid_search)
    results = await rag.search("子块")
    assert [r.id for r in results] == [child.id]


async def test_expand_inherits_version_status(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """展开的父块继承子块的 version_status。"""
    rag = RAGService(session, "pc-ver")
    doc = await rag.ingest_text(
        "父块内容。子块内容C。",
        title="版本继承",
        source="test",
        user_id="pc-ver-user",
        strategy="parent_child",
    )
    parent, child = await _fetch_parent_child(session, doc.id)

    async def fake_hybrid_search(*args, **kwargs):
        return [
            ChunkResult(
                id=child.id,
                content=child.content,
                source="test",
                document_id=doc.id,
                score=0.75,
                similarity=0.6,
                version_status="current",
            )
        ]

    monkeypatch.setattr(_native_store(rag), "hybrid_search", fake_hybrid_search)
    results = await rag.search("子块")
    parent_result = next(r for r in results if r.id == parent.id)
    assert parent_result.version_status == "current"


async def test_expand_only_one_level(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """三层父子链：只追加直接父块，不递归追加祖父块。"""
    rag = RAGService(session, "pc-3level")
    doc = await rag.ingest_text(
        "父块内容。子块内容D。",
        title="三层锁定",
        source="test",
        user_id="pc-3level-user",
        strategy="parent_child",
    )
    parent, child = await _fetch_parent_child(session, doc.id)

    # 人为构造祖父块 G，并把父块挂到 G 之下，形成 G → P → C 三层链。
    grandparent = DocumentChunk(
        tenant_id=doc.tenant_id,
        document_id=doc.id,
        chunk_index=99,
        content="祖父块内容。",
        source="test",
        strategy="parent_child",
    )
    session.add(grandparent)
    session.flush()
    parent.parent_id = grandparent.id
    session.add(parent)
    session.commit()

    async def fake_hybrid_search(*args, **kwargs):
        return [
            ChunkResult(
                id=child.id,
                content=child.content,
                source="test",
                document_id=doc.id,
                score=0.85,
                similarity=0.66,
            )
        ]

    monkeypatch.setattr(_native_store(rag), "hybrid_search", fake_hybrid_search)
    results = await rag.search("子块")
    ids = [r.id for r in results]
    assert parent.id in ids, "应追加直接父块"
    assert grandparent.id not in ids, "不递归追加祖父块"
