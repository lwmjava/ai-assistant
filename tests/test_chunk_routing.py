"""RAG-022 auto-routing 决策表与复用计划测试。

覆盖：
- 不同文档类型/blocks/bbox/表格/盒图/Markdown 路由到对应策略；
- 无 bbox/blocks 时显式降级（不选 format_aware/layout_aware）；
- 路由决策可解释（reason 非空，写入 chunk metadata）；
- 重解析复用旧策略，旧数据缺策略有兼容路径。
"""

from __future__ import annotations

import pytest
from sqlmodel import select

from app.models.rag import DocumentChunk
from app.rag.chunking.base import ChunkParams
from app.rag.document_parsers.base import ParsedBlock, ParsedDocument

# ── signals 提取 ──────────────────────────────────────────────


def test_signals_from_text_detects_markdown_headings() -> None:
    from app.rag.chunking.routing import signals_from_text

    sig = signals_from_text("# 标题\n正文内容")
    assert sig.has_headings is True
    assert sig.has_blocks is False
    assert sig.has_bbox is False


def test_signals_from_text_detects_structure_units() -> None:
    from app.rag.chunking.routing import signals_from_text

    text = "| 列A | 列B |\n| --- | --- |\n| 1 | 2 |"
    sig = signals_from_text(text)
    assert sig.has_structure_units is True


def test_signals_from_parsed_aggregates_blocks_and_bbox() -> None:
    from app.rag.chunking.routing import signals_from_parsed

    parsed = ParsedDocument(
        text="正文",
        title="t",
        source="s.pdf",
        extension="pdf",
        content_type="application/pdf",
        blocks=[
            ParsedBlock(type="text", text="a", order=0, page=1, bbox=(0, 0, 100, 50)),
            ParsedBlock(type="heading", text="b", order=1, page=1, bbox=(0, 0, 100, 30)),
        ],
    )
    sig = signals_from_parsed(parsed)
    assert sig.has_blocks is True
    assert sig.has_bbox is True
    assert sig.extension == "pdf"
    assert "heading" in sig.block_types


# ── 决策表 ───────────────────────────────────────────────────


def test_bbox_blocks_route_to_layout_aware() -> None:
    from app.rag.chunking.routing import route, signals_from_parsed

    sig = signals_from_parsed(ParsedDocument(
        text="正文", title="t", source="s.pdf", extension="pdf",
        content_type="application/pdf",
        blocks=[ParsedBlock(type="text", text="a", order=0, bbox=(0, 0, 100, 50))],
    ))
    decision = route(sig, requested="auto", configured_default="structured")
    assert decision.strategy == "layout_aware"
    assert "bbox" in decision.reason


def test_structural_blocks_without_bbox_route_to_format_aware() -> None:
    from app.rag.chunking.routing import route, signals_from_parsed

    parsed = ParsedDocument(
        text="正文", title="t", source="s.docx", extension="docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        blocks=[
            ParsedBlock(type="heading", text="一", order=0),
            ParsedBlock(type="paragraph", text="内容", order=1),
        ],
    )
    decision = route(signals_from_parsed(parsed), requested="auto", configured_default="structured")
    assert decision.strategy == "format_aware"
    assert "blocks" in decision.reason


def test_markdown_text_routes_to_structured_and_not_format_aware() -> None:
    from app.rag.chunking.routing import route, signals_from_text

    decision = route(signals_from_text("# 标题\n正文"), requested="auto", configured_default="structured")
    assert decision.strategy == "structured"


def test_plain_text_without_blocks_does_not_pick_layout_or_format() -> None:
    from app.rag.chunking.routing import route, signals_from_text

    text = "这是一段没有标题、没有表格、没有 bbox 的纯中文正文。" * 5
    decision = route(signals_from_text(text), requested="auto", configured_default="structured")
    assert decision.strategy in {"recursive", "paragraph", "token_aware"}
    assert decision.strategy not in {"format_aware", "layout_aware"}


def test_non_cjk_code_like_text_routes_to_token_aware() -> None:
    from app.rag.chunking.routing import route, signals_from_text

    code = "def foo():\n    return 42\n" * 5
    decision = route(signals_from_text(code), requested="auto", configured_default="structured")
    assert decision.strategy == "token_aware"


def test_requested_strategy_wins_over_signals() -> None:
    from app.rag.chunking.routing import route, signals_from_parsed

    parsed = ParsedDocument(
        text="正文", title="t", source="s.pdf", extension="pdf",
        content_type="application/pdf",
        blocks=[ParsedBlock(type="text", text="a", order=0, bbox=(0, 0, 100, 50))],
    )
    decision = route(signals_from_parsed(parsed), requested="paragraph", configured_default="structured")
    assert decision.strategy == "paragraph"
    assert decision.reason == "request_override"


def test_decision_is_explainable() -> None:
    from app.rag.chunking.routing import route, signals_from_text

    decision = route(signals_from_text("# H\n正文"), requested="auto", configured_default="structured")
    assert decision.strategy
    assert decision.reason
    assert decision.signals.char_count >= 0


# ── factory 向后兼容 ─────────────────────────────────────────


def test_resolve_strategy_name_backward_compat() -> None:
    from app.rag.chunking.factory import resolve_strategy_name

    assert resolve_strategy_name("内容", "semantic") == "semantic"
    assert resolve_strategy_name("# 标题\n正文", "auto") == "structured"


def test_resolve_with_decision_accepts_parsed() -> None:
    from app.rag.chunking.factory import resolve_strategy_with_decision

    parsed = ParsedDocument(
        text="正文", title="t", source="s.pdf", extension="pdf",
        content_type="application/pdf",
        blocks=[ParsedBlock(type="text", text="a", order=0, bbox=(0, 0, 100, 50))],
    )
    decision = resolve_strategy_with_decision("正文", "auto", parsed=parsed)
    assert decision.strategy == "layout_aware"


# ── service 重解析复用旧策略 ────────────────────────────────


@pytest.mark.asyncio
async def test_reindex_reuses_previous_strategy() -> None:
    """重解析时若旧 chunk 策略一致，复用之；reason 标记 reuse。"""
    from app.rag.service import RAGService

    decision = RAGService._decide_reindex_strategy(
        previous_strategies=["format_aware", "format_aware"],
        parsed=ParsedDocument(text="正文", title="t", source="s", extension="docx", content_type=None),
    )
    assert decision.strategy == "format_aware"
    assert "reuse" in decision.reason


@pytest.mark.asyncio
async def test_reindex_falls_back_to_routing_when_no_previous_strategy() -> None:
    """旧数据 strategy 全为 NULL 时走路由兼容路径。"""
    from app.rag.service import RAGService

    parsed = ParsedDocument(
        text="# 标题\n正文", title="t", source="s.md", extension="md", content_type=None,
    )
    decision = RAGService._decide_reindex_strategy(
        previous_strategies=[None, None],
        parsed=parsed,
    )
    assert decision.strategy == "structured"
    assert "reuse" not in decision.reason


# ── 父子范围包含（沿用 RAG-021 不回归） ──────────────────────


@pytest.mark.asyncio
async def test_parent_child_source_ranges_still_contained() -> None:
    """父子源范围包含关系（RAG-021 已保证，RAG-022 不回归）。"""
    from app.rag.chunking.strategies.parent_child import ParentChildChunkingStrategy

    # 用 Markdown 表格触发 structure_units -> protected_split，保证 source_start/end 存在。
    text = (
        "| 列A | 列B |\n| --- | --- |\n| 1 | 2 |\n"
        "段落正文。" * 40
    )
    strategy = ParentChildChunkingStrategy(registry=None, embedding=None)
    chunks = await strategy.split(
        text,
        params=ChunkParams(
            child_strategy="paragraph",
            child_params={"chunk_size": 40},
            parent_strategy="paragraph",
            parent_ratio=2,
        ),
    )
    parents = [c for c in chunks if c.metadata.get("kind") == "parent"]
    children = [c for c in chunks if c.metadata.get("kind") == "child"]
    assert parents and children
    parent_ranges = [
        (p.metadata["source_start"], p.metadata["source_end"])
        for p in parents
        if "source_start" in p.metadata
    ]
    assert parent_ranges, "parent chunks should carry source ranges on structure text"
    for child in children:
        if "source_start" not in child.metadata:
            continue
        cs, ce = child.metadata["source_start"], child.metadata["source_end"]
        assert any(ps <= cs and ce <= pe for ps, pe in parent_ranges), (cs, ce, parent_ranges)


# ── upload → reparse 集成测试 ──────────────────────────────


@pytest.fixture()
def isolated_session():
    from sqlmodel import Session, SQLModel, create_engine

    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


async def test_upload_then_reparse_replays_persisted_plan(isolated_session, monkeypatch):
    """请求级策略与参数被持久化；重解析按同一 plan 重放，chunk 数与内容一致。"""
    from app.rag.embeddings.mock import MockEmbeddingProvider
    from app.rag.service import RAGService

    provider = MockEmbeddingProvider(dim=4)
    service = RAGService(isolated_session, tenant_id="t", embedding_provider=provider)
    text = "第一段内容。\n\n第二段内容。\n\n第三段内容。"
    doc = await service.ingest_text(
        text, "src", "title", "user",
        strategy="recursive", chunk_params={"chunk_size": 20, "chunk_overlap": 0},
    )
    assert doc.chunk_plan is not None
    import json as _json

    plan = _json.loads(doc.chunk_plan)
    assert plan["strategy"] == "recursive"
    assert plan["chunk_params"]["chunk_size"] == 20
    chunk_count_v1 = doc.chunk_count

    # 重解析同原文：应严格重放 plan，chunk 数与首次一致。
    parsed = ParsedDocument(text=text, title="t", source="src", extension="txt", content_type="text/plain")
    doc2 = await service.reindex_document_in_place(doc, parsed, content_hash="same-hash")
    assert doc2.chunk_count == chunk_count_v1
    plan2 = _json.loads(doc2.chunk_plan)
    assert plan2["strategy"] == "recursive"
    assert plan2["chunk_params"]["chunk_size"] == 20
    # 所有新 chunk 都带 replay_chunk_plan reason。
    rows = isolated_session.exec(select(DocumentChunk).where(DocumentChunk.document_id == doc.id)).all()
    reasons = {_json.loads(r.chunk_metadata or "{}").get("routing_reason") for r in rows}
    assert "replay_chunk_plan" in reasons


async def test_reparse_uses_stored_params_even_when_config_changes(
    isolated_session, monkeypatch
):
    """配置 RAG_CHUNK_SIZE 变化不影响重解析：plan 中的参数优先。"""
    from app.core.config import settings
    from app.rag.embeddings.mock import MockEmbeddingProvider
    from app.rag.service import RAGService

    provider = MockEmbeddingProvider(dim=4)
    service = RAGService(isolated_session, tenant_id="t", embedding_provider=provider)
    text = "这是一段足够长的中文正文。" * 20
    doc = await service.ingest_text(
        text, "src", "title", "user",
        strategy="recursive", chunk_params={"chunk_size": 30, "chunk_overlap": 0},
    )
    v1_count = doc.chunk_count

    # 模拟配置变化：把全局 chunk_size 调到很大。
    monkeypatch.setattr(settings, "RAG_CHUNK_SIZE", 2000)
    parsed = ParsedDocument(text=text, title="t", source="src", extension="txt", content_type="text/plain")
    doc2 = await service.reindex_document_in_place(doc, parsed, content_hash="h")
    # 仍按 plan 里的 30 切，chunk 数与首次一致。
    assert doc2.chunk_count == v1_count


async def test_reparse_old_data_without_plan_degrades_gracefully(isolated_session):
    """旧文档（chunk_plan 为 NULL）重解析走路由兼容路径，不报错。"""
    from app.rag.embeddings.mock import MockEmbeddingProvider
    from app.rag.service import RAGService

    provider = MockEmbeddingProvider(dim=4)
    service = RAGService(isolated_session, tenant_id="t", embedding_provider=provider)
    doc = await service.ingest_text("# 标题\n正文内容", "src", "t", "user")
    # 模拟旧数据：清空 chunk_plan。
    doc.chunk_plan = None
    isolated_session.add(doc)
    isolated_session.commit()

    parsed = ParsedDocument(
        text="# 标题\n新正文", title="t", source="src", extension="md", content_type="text/markdown"
    )
    doc2 = await service.reindex_document_in_place(doc, parsed, content_hash="h2")
    assert doc2.chunk_count >= 1
    assert doc2.chunk_plan is not None
    import json as _json

    plan = _json.loads(doc2.chunk_plan)
    assert plan["strategy"] == "structured"

