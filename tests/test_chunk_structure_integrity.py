"""Synthetic structure integrity cases; no retrieval quality claims."""

import pytest

from app.rag.chunking.base import ChunkParams
from app.rag.chunking.factory import build_registry, get_chunking_strategy


@pytest.mark.parametrize("name", build_registry().names())
async def test_registered_strategies_keep_fence_and_source(name):
    class NeverEmbedding:
        async def embed(self, texts):
            raise AssertionError("Protected structure must not trigger semantic embedding")

    text = "  before\n\n````python\n# internal\n  x = 1\n```\n````\n\nafter  "
    chunks = await get_chunking_strategy(name, NeverEmbedding()).split(
        text, params=ChunkParams(chunk_size=12, chunk_overlap=0)
    )
    primary = [c for c in chunks if c.metadata.get("kind") != "child"]
    assert "".join(text[c.metadata["source_start"] : c.metadata["source_end"]] for c in primary) == text
    fence = text[text.index("````") : text.rindex("````") + 5]
    assert any(fence in c.text for c in primary)
    for chunk in chunks:
        start, end = chunk.metadata["source_start"], chunk.metadata["source_end"]
        assert chunk.text == chunk.metadata.get("derived_prefix", "") + text[start:end]


async def test_table_headers_are_derived_and_rows_intact():
    text = "| a | b |\n|---|---|\n| one | two |\n| three | four |\n"
    chunks = await get_chunking_strategy("paragraph").split(text, params=ChunkParams(chunk_size=25, chunk_overlap=0))
    assert "".join(text[c.metadata["source_start"] : c.metadata["source_end"]] for c in chunks) == text
    assert any(c.metadata.get("derived_prefix") for c in chunks[1:])
    assert all(c.text.endswith("\n") for c in chunks)


async def test_unknown_limit_preserves_unclosed_fence():
    text = "~~~\n" + "  body\n" * 20
    chunks = await get_chunking_strategy("paragraph").split(text, params=ChunkParams(chunk_size=10))
    assert len(chunks) == 1
    assert chunks[0].text == text
    assert chunks[0].metadata["limit_unverified"] is True


async def test_policy_reject_marks_oversized_without_cutting():
    class Policy:
        def check(self, text):
            return "input_limit_exceeded" if len(text) > 20 else None

    class Embedding:
        input_policy = Policy()

    text = "+----------------------+\n|  a very long box     |\n+----------------------+\n"
    chunks = await get_chunking_strategy("paragraph", Embedding()).split(text, params=ChunkParams(chunk_size=10))
    assert len(chunks) == 1
    assert chunks[0].text == text
    assert chunks[0].metadata["oversized"] is True
    assert chunks[0].metadata["non_vectorization_reason"] == "input_limit_exceeded"


async def test_parent_child_table_ranges_and_complete_rows():
    text = "| a | b |\n|---|---|\n| one | two |\n| three | four |\n"
    chunks = await get_chunking_strategy("parent_child").split(
        text, params=ChunkParams(chunk_size=8, parent_ratio=3, chunk_overlap=0)
    )
    parents = {c.metadata["parent_key"]: c for c in chunks if c.metadata.get("kind") == "parent"}
    for child in [c for c in chunks if c.metadata.get("kind") == "child"]:
        parent = parents[child.parent_id]
        assert parent.metadata["source_start"] <= child.metadata["source_start"]
        assert child.metadata["source_end"] <= parent.metadata["source_end"]
        assert (
            child.text
            == child.metadata.get("derived_prefix", "")
            + text[child.metadata["source_start"] : child.metadata["source_end"]]
        )
        assert child.text.endswith("\n")


@pytest.mark.parametrize("name", ["format_aware", "layout_aware", "paragraph", "parent_child"])
async def test_blocks_bind_offsets_to_reading_flow(name):
    from app.rag.document_parsers.base import ParsedBlock

    blocks = [
        ParsedBlock(type="text", text="```\n  x = 1\n```\n", order=0),
        ParsedBlock(type="text", text="  tail\n", order=1),
    ]
    text = "\n".join(b.text for b in blocks)
    chunks = await get_chunking_strategy(name).split_blocks(blocks, params=ChunkParams(chunk_size=5))
    for chunk in chunks:
        assert chunk.metadata["source_coordinate"] == "parsed_reading_flow"
        assert (
            chunk.text
            == chunk.metadata.get("derived_prefix", "")
            + text[chunk.metadata["source_start"] : chunk.metadata["source_end"]]
        )
        assert chunk.metadata["source_block_indices"]


async def test_semantic_unknown_policy_does_not_embed_prose():
    class NeverEmbedding:
        async def embed(self, texts):
            raise AssertionError("Unknown input policy must fail closed")

    chunks = await get_chunking_strategy("semantic", NeverEmbedding()).split(
        "First sentence.\nSecond sentence.", params=ChunkParams(chunk_size=10)
    )
    assert chunks
    assert all(c.metadata["semantic_fallback_reason"] == "input_limit_unverified" for c in chunks)


async def test_child_cannot_override_service_policy():
    with pytest.raises(ValueError, match="input_policy"):
        await get_chunking_strategy("parent_child").split(
            "```\nx\n```", params=ChunkParams(child_params={"input_policy": None})
        )


async def test_direct_strategy_unicode_box_and_service_policy():
    from app.rag.chunking.strategies.paragraph import ParagraphChunkingStrategy
    from app.rag.embeddings.base import EmbeddingInputPolicy

    text = "┌─────────┐\n│  数据   │\n└─────────┘\n"
    policy = EmbeddingInputPolicy(max_input_tokens=5, counter=len, counting_method="test-characters")
    chunks = await ParagraphChunkingStrategy().split(text, params=ChunkParams(chunk_size=3, input_policy=policy))
    assert [c.text for c in chunks] == [text]
    assert chunks[0].metadata["oversized"]
    assert chunks[0].metadata["embedding_input_policy"]["max_input_tokens"] == 5


async def test_long_table_row_is_preserved_and_rejected():
    from app.rag.embeddings.base import EmbeddingInputPolicy

    text = "| a | b |\n|---|---|\n| " + "x" * 100 + " | value |\n"
    policy = EmbeddingInputPolicy(max_input_tokens=30, counter=len, counting_method="test-characters")
    chunks = await get_chunking_strategy("paragraph").split(
        text, params=ChunkParams(chunk_size=25, input_policy=policy)
    )
    assert "".join(text[c.metadata["source_start"] : c.metadata["source_end"]] for c in chunks) == text
    assert "x" * 100 in chunks[-1].text
    assert chunks[-1].metadata["oversized"]


async def test_structure_keeps_real_section_and_ignores_fenced_heading():
    text = "# Outer\n\n## Real\n```\n# Fake\n  code\n```\n+----+\n|box |\n+----+\n"
    chunks = await get_chunking_strategy("parent_child").split(text, params=ChunkParams(chunk_size=8))
    structures = [c for c in chunks if c.metadata.get("structure_type") in {"fenced_code", "box_diagram"}]
    assert structures
    for chunk in structures:
        assert chunk.metadata["section_path"] == ["Outer", "Real"]
        for heading in chunk.metadata["source_heading_ranges"]:
            assert text[heading["start"] : heading["end"]] == heading["text"]
        assert all("Fake" not in h["text"] for h in chunk.metadata["source_heading_ranges"])


async def test_soft_target_never_splits_table_header_from_first_row():
    text = "| a | b |\n|---|---|\n| first | row |\n| second | row |\n"
    chunks = await get_chunking_strategy("paragraph").split(text, params=ChunkParams(chunk_size=3))
    assert "| first | row |\n" in chunks[0].text
    assert len(chunks) == 2


async def test_source_hash_computed_once_per_partition(monkeypatch):
    import app.rag.chunking.structure as structure

    original = structure.hashlib.sha256
    calls = []

    def count_hash(value):
        calls.append(value)
        return original(value)

    monkeypatch.setattr(structure.hashlib, "sha256", count_hash)
    chunks = structure.protected_split("a" * 1000 + "\n```\nx\n```", 10)
    assert len(chunks) > 10
    assert len(calls) == 1


async def test_child_local_heading_merges_inherited_ancestors():
    text = "# Outer\n" + "a" * 78 + "\n## Inner\nbody\n~~~\nx\n~~~\n"
    chunks = await get_chunking_strategy("parent_child").split(
        text, params=ChunkParams(chunk_size=29, parent_ratio=3, chunk_overlap=0)
    )
    children = [c for c in chunks if c.metadata.get("kind") == "child" and c.metadata["source_start"] == 87]
    assert children
    assert children[0].metadata["section_path"] == ["Outer", "Inner"]
    ranges = children[0].metadata["source_heading_ranges"]
    assert [heading["level"] for heading in ranges] == [1, 2]
    assert len({(heading["start"], heading["end"]) for heading in ranges}) == 2
    assert all(text[heading["start"] : heading["end"]] == heading["text"] for heading in ranges)
