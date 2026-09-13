"""多模式切分策略层测试。"""

import pytest

from app.rag.chunking.base import Chunk, ChunkParams


def test_chunk_defaults() -> None:
    chunk = Chunk(text="内容", index=0)
    assert chunk.parent_id is None
    assert chunk.metadata == {}


def test_chunk_params_defaults() -> None:
    params = ChunkParams()
    assert params.chunk_size == 500
    assert params.chunk_overlap == 64
    assert params.parent_ratio == 3


async def test_fixed_chars_strategy_splits_by_length() -> None:
    from app.rag.chunking.strategies.fixed_chars import FixedCharsChunkingStrategy

    chunks = await FixedCharsChunkingStrategy().split(
        "0123456789",
        params=ChunkParams(chunk_size=4, chunk_overlap=0),
    )
    assert [c.text for c in chunks] == ["0123", "4567", "89"]


async def test_fixed_chars_strategy_supports_overlap() -> None:
    from app.rag.chunking.strategies.fixed_chars import FixedCharsChunkingStrategy

    chunks = await FixedCharsChunkingStrategy().split(
        "0123456789",
        params=ChunkParams(chunk_size=4, chunk_overlap=1),
    )
    assert [c.text for c in chunks] == ["0123", "3456", "6789"]


async def test_paragraph_splits_on_blank_lines() -> None:
    from app.rag.chunking.strategies.paragraph import ParagraphChunkingStrategy

    text = "第一段内容。\n\n第二段内容。\n\n第三段内容。"
    chunks = await ParagraphChunkingStrategy().split(
        text, params=ChunkParams(chunk_size=100)
    )
    assert len(chunks) == 3
    assert [c.text for c in chunks] == ["第一段内容。", "第二段内容。", "第三段内容。"]


async def test_sliding_window_overlaps_by_step() -> None:
    from app.rag.chunking.strategies.sliding_window import SlidingWindowChunkingStrategy

    text = "0123456789"
    chunks = await SlidingWindowChunkingStrategy().split(
        text, params=ChunkParams(window_size=6, step=4)
    )
    assert [c.text for c in chunks] == ["012345", "456789"]


async def test_token_aware_uses_fallback_estimator(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.rag.chunking.strategies.token_aware as token_aware
    from app.rag.chunking.strategies.token_aware import TokenAwareChunkingStrategy

    # 强制走估算回退路径，避免本机 tiktoken 影响断言。
    monkeypatch.setattr(token_aware, "_try_load_tokenizer", lambda: None)

    # 中文每字约 1 token，max_tokens=4 应切出两块。
    text = "甲乙丙丁戊己"
    chunks = await TokenAwareChunkingStrategy().split(
        text, params=ChunkParams(max_tokens=4)
    )
    assert len(chunks) == 2
    assert all(c.text for c in chunks)


async def test_structured_preserves_headings() -> None:
    from app.rag.chunking.strategies.structured import StructuredChunkingStrategy

    text = "# 章节A\n" + "A内容。" * 20 + "\n\n# 章节B\n" + "B内容。" * 20
    chunks = await StructuredChunkingStrategy().split(
        text, params=ChunkParams(chunk_size=50, chunk_overlap=10)
    )
    assert any(c.text.startswith("# 章节A") for c in chunks)
    assert any(c.text.startswith("# 章节B") for c in chunks)


async def test_semantic_splits_on_low_similarity() -> None:
    from app.rag.chunking.strategies.semantic import SemanticChunkingStrategy

    class FakeEmbedding:
        async def embed(self, texts):
            return [[hash(t) % 100, 1.0] for t in texts]

    text = "主题A第一句。主题A第二句。主题B第三句。"
    strategy = SemanticChunkingStrategy(FakeEmbedding())
    chunks = await strategy.split(
        text, params=ChunkParams(similarity_threshold=0.0)
    )
    assert chunks


async def test_parent_child_builds_relations() -> None:
    from app.rag.chunking.strategies.parent_child import ParentChildChunkingStrategy

    text = "第一段。" * 20 + "\n\n" + "第二段。" * 20
    strategy = ParentChildChunkingStrategy(registry=None, embedding=None)
    chunks = await strategy.split(
        text,
        params=ChunkParams(
            child_strategy="paragraph",
            child_params={"chunk_size": 30},
            parent_strategy="paragraph",
            parent_ratio=3,
        ),
    )
    children = [c for c in chunks if c.metadata.get("kind") == "child"]
    parents = [c for c in chunks if c.metadata.get("kind") == "parent"]
    assert children
    assert parents
    assert all(c.parent_id is not None for c in children)


async def test_recursive_strategy_prefers_natural_boundaries() -> None:
    from app.rag.chunking.strategies.recursive import RecursiveChunkingStrategy

    text = "第一段第一句。第一段第二句。\n\n第二段第一句。第二段第二句。"
    chunks = await RecursiveChunkingStrategy().split(
        text,
        params=ChunkParams(chunk_size=16, chunk_overlap=0),
    )
    assert chunks
    assert any("第一段" in c.text for c in chunks)
    assert any("第二段" in c.text for c in chunks)


async def test_recursive_strategy_falls_back_to_char_split() -> None:
    from app.rag.chunking.strategies.recursive import RecursiveChunkingStrategy

    text = "abcdefghij"
    chunks = await RecursiveChunkingStrategy().split(
        text,
        params=ChunkParams(chunk_size=4, chunk_overlap=0),
    )
    assert [c.text for c in chunks] == ["abcd", "efgh", "ij"]


async def test_format_aware_strategy_uses_blocks() -> None:
    from app.rag.chunking.strategies.format_aware import FormatAwareChunkingStrategy
    from app.rag.document_parsers.base import ParsedBlock

    strategy = FormatAwareChunkingStrategy()
    chunks = await strategy.split_blocks(
        [
            ParsedBlock(type="heading", text="一、总则", order=0, section_path=["一、总则"]),
            ParsedBlock(
                type="paragraph",
                text="这里是正文。",
                order=1,
                section_path=["一、总则"],
            ),
        ],
        params=ChunkParams(chunk_size=200, chunk_overlap=0),
    )
    assert len(chunks) == 2
    assert chunks[0].metadata["block_type"] == "heading"
    assert chunks[0].metadata["section_path"] == ["一、总则"]


async def test_layout_aware_strategy_respects_reading_order() -> None:
    from app.rag.chunking.strategies.layout_aware import LayoutAwareChunkingStrategy
    from app.rag.document_parsers.base import ParsedBlock

    blocks = [
        ParsedBlock(
            type="text",
            text="右栏第二段",
            order=2,
            page=1,
            metadata={"reading_order": 2, "layout_role": "body"},
        ),
        ParsedBlock(
            type="text",
            text="左栏第一段",
            order=1,
            page=1,
            metadata={"reading_order": 1, "layout_role": "body"},
        ),
    ]
    chunks = await LayoutAwareChunkingStrategy().split_blocks(
        blocks,
        params=ChunkParams(chunk_size=100, chunk_overlap=0),
    )
    assert [c.text for c in chunks] == ["左栏第一段", "右栏第二段"]
    assert [c.metadata["reading_order"] for c in chunks] == [1, 2]


async def test_layout_aware_reorders_two_columns_by_bbox() -> None:
    from app.rag.chunking.strategies.layout_aware import LayoutAwareChunkingStrategy
    from app.rag.document_parsers.base import ParsedBlock

    blocks = [
        ParsedBlock(type="text", text="左栏下段", order=0, page=1, bbox=(10, 200, 200, 280)),
        ParsedBlock(type="text", text="右栏上段", order=1, page=1, bbox=(300, 20, 500, 100)),
        ParsedBlock(type="text", text="左栏上段", order=2, page=1, bbox=(10, 20, 200, 100)),
        ParsedBlock(type="text", text="右栏下段", order=3, page=1, bbox=(300, 200, 500, 280)),
    ]
    chunks = await LayoutAwareChunkingStrategy().split_blocks(
        blocks,
        params=ChunkParams(chunk_size=100, chunk_overlap=0),
    )
    assert [c.text for c in chunks] == ["左栏上段", "左栏下段", "右栏上段", "右栏下段"]
    assert [c.metadata["reading_order"] for c in chunks] == [1, 2, 3, 4]


async def test_layout_aware_keeps_table_blocks_by_reading_order() -> None:
    from app.rag.chunking.strategies.layout_aware import LayoutAwareChunkingStrategy
    from app.rag.document_parsers.base import ParsedBlock

    blocks = [
        ParsedBlock(
            type="table_cell",
            text="表头",
            order=0,
            page=1,
            bbox=(10, 20, 200, 60),
            metadata={"reading_order": 1, "layout_role": "table_header"},
        ),
        ParsedBlock(
            type="table_cell",
            text="数据",
            order=1,
            page=1,
            bbox=(300, 20, 500, 60),
            metadata={"reading_order": 2, "layout_role": "table_cell"},
        ),
    ]
    chunks = await LayoutAwareChunkingStrategy().split_blocks(
        blocks,
        params=ChunkParams(chunk_size=100, chunk_overlap=0),
    )
    assert [c.text for c in chunks] == ["表头", "数据"]
    assert [c.metadata["reading_order"] for c in chunks] == [1, 2]


async def test_layout_aware_single_column_not_reordered() -> None:
    from app.rag.chunking.strategies.layout_aware import LayoutAwareChunkingStrategy
    from app.rag.document_parsers.base import ParsedBlock

    blocks = [
        ParsedBlock(type="text", text="第一段", order=0, page=1, bbox=(10, 20, 500, 80)),
        ParsedBlock(type="text", text="第二段", order=1, page=1, bbox=(10, 100, 500, 160)),
        ParsedBlock(type="text", text="第三段", order=2, page=1, bbox=(10, 180, 500, 240)),
    ]
    chunks = await LayoutAwareChunkingStrategy().split_blocks(
        blocks,
        params=ChunkParams(chunk_size=100, chunk_overlap=0),
    )
    assert [c.text for c in chunks] == ["第一段", "第二段", "第三段"]


def test_resolve_strategy_name_request_wins() -> None:
    from app.rag.chunking.factory import resolve_strategy_name

    assert resolve_strategy_name("内容", "semantic") == "semantic"


def test_resolve_strategy_name_auto_routes_structured() -> None:
    from app.rag.chunking.factory import resolve_strategy_name

    assert resolve_strategy_name("# 标题\n正文", "auto") == "structured"


def test_resolve_strategy_name_auto_routes_recursive_for_long_plain_text() -> None:
    from app.rag.chunking.factory import resolve_strategy_name

    text = "这是没有标题的长正文。" * 100
    assert resolve_strategy_name(text, "auto") == "recursive"


def test_get_chunking_strategy_paragraph() -> None:
    from app.rag.chunking.factory import get_chunking_strategy
    from app.rag.chunking.strategies.paragraph import ParagraphChunkingStrategy

    strategy = get_chunking_strategy("paragraph")
    assert isinstance(strategy, ParagraphChunkingStrategy)


def test_get_chunking_strategy_unknown_raises() -> None:
    from app.rag.chunking.factory import get_chunking_strategy

    with pytest.raises(ValueError, match="不支持的切分策略"):
        get_chunking_strategy("nope")
