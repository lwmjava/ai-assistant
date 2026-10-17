"""Parent-child mapping must follow content generation, not block counts."""

import pytest

from app.rag.chunking.base import Chunk, ChunkingStrategy, ChunkParams
from app.rag.chunking.factory import get_chunking_strategy


@pytest.mark.parametrize("child_strategy", ["paragraph", "recursive", "token_aware"])
async def test_children_are_generated_inside_their_actual_parent(child_strategy):
    text = "A" * 71 + "\n\n" + "B" * 17 + "\n\n" + "C" * 61 + "\n\n" + "D" * 11
    strategy = get_chunking_strategy("parent_child")
    params = ChunkParams(
        chunk_size=20, chunk_overlap=0, parent_ratio=3,
        child_strategy=child_strategy, child_params={"max_tokens": 10},
    )
    chunks = await strategy.split(text, params=params)
    parents = {c.metadata["parent_key"]: c for c in chunks if c.metadata["kind"] == "parent"}
    children = [c for c in chunks if c.metadata["kind"] == "child"]
    assert parents and children
    for child in children:
        assert child.text in parents[child.parent_id].text
    assert [c.index for c in chunks] == list(range(len(chunks)))


async def test_repeated_content_children_stay_with_generating_parent():
    strategy = get_chunking_strategy("parent_child")
    chunks = await strategy.split(
        "same\n\n" + "X" * 37 + "\n\nsame\n\n" + "Y" * 11,
        params=ChunkParams(chunk_size=10, chunk_overlap=0, parent_ratio=2),
    )
    parents = [c for c in chunks if c.metadata["kind"] == "parent"]
    for parent in parents:
        children = [c for c in chunks if c.parent_id == parent.metadata["parent_key"]]
        assert children
        assert all(c.text in parent.text for c in children)


@pytest.mark.parametrize("parent_strategy", ["paragraph", "recursive", "structured"])
async def test_each_parent_generates_its_own_children(parent_strategy, monkeypatch):
    inputs = []

    class RecordingChild(ChunkingStrategy):
        name = "recording"

        async def split(self, text, *, params):
            inputs.append(text)
            return [Chunk(text=text, index=0)]

    strategy = get_chunking_strategy("parent_child")
    build = strategy._build_child
    monkeypatch.setattr(
        strategy, "_build_child",
        lambda name: RecordingChild() if name == "recording" else build(name),
    )
    chunks = await strategy.split(
        "shared\n\nshared\n\n" + "long" * 12,
        params=ChunkParams(
            chunk_size=10, chunk_overlap=0, parent_ratio=2,
            parent_strategy=parent_strategy, child_strategy="recording",
        ),
    )
    parents = [c for c in chunks if c.metadata["kind"] == "parent"]
    children = [c for c in chunks if c.metadata["kind"] == "child"]
    assert inputs == [p.text for p in parents]
    assert len(children) == len(parents)
    for parent, child in zip(parents, children, strict=True):
        assert child.parent_id == parent.metadata["parent_key"]
        assert child.text == parent.text
