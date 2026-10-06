"""父子文档切分策略：父块为子块的倍数粗切分块，子块为细粒度块。"""

from __future__ import annotations

from dataclasses import fields
from typing import Any

from app.rag.chunking.base import Chunk, ChunkingStrategy, ChunkParams
from app.rag.chunking.registry import ChunkingRegistry


def _chunk_params_for_child(params: ChunkParams) -> ChunkParams:
    """用父级尺寸作底，再用 child_params 覆盖已知字段。未知键失败。"""
    overrides: dict[str, Any] = dict(params.child_params or {})
    allowed = {item.name for item in fields(ChunkParams)}
    allowed.discard("input_policy")
    unknown = sorted(str(key) for key in overrides if key not in allowed)
    if unknown:
        raise ValueError("未知的 ChunkParams 字段: " + ", ".join(unknown))
    return ChunkParams(
        chunk_size=overrides.get("chunk_size", params.chunk_size),
        chunk_overlap=overrides.get("chunk_overlap", params.chunk_overlap),
        window_size=overrides.get("window_size"),
        step=overrides.get("step"),
        max_tokens=overrides.get("max_tokens"),
        overlap_tokens=overrides.get("overlap_tokens"),
        similarity_threshold=overrides.get("similarity_threshold"),
        parent_strategy=overrides.get("parent_strategy"),
        parent_ratio=overrides.get("parent_ratio", 3),
        child_strategy=overrides.get("child_strategy"),
        child_params=overrides.get("child_params"),
        input_policy=params.input_policy,
    )


class ParentChildChunkingStrategy(ChunkingStrategy):
    """父块 = 子块 N 倍粗块；子块记录 ``parent_id`` 指向父块。"""

    name = "parent_child"

    def __init__(self, *, registry: ChunkingRegistry | None, embedding=None) -> None:
        self._registry = registry
        self._embedding = embedding

    def _build_child(self, name: str) -> ChunkingStrategy:
        if self._registry is not None:
            return self._registry.build(name, embedding=self._embedding)
        # 回退到 factory，避免顶层循环导入。
        from app.rag.chunking.factory import get_chunking_strategy

        return get_chunking_strategy(name, embedding=self._embedding)

    async def split(self, text: str, *, params: ChunkParams) -> list[Chunk]:
        from app.rag.chunking.structure import protected_split, structure_units

        has_structure = bool(structure_units(text or ""))
        text = (text or "") if has_structure else (text or "").strip()
        if not text:
            return []
        child_name = params.child_strategy or "paragraph"
        parent_name = params.parent_strategy or "paragraph"
        ratio = params.parent_ratio or 3

        child_strategy = self._build_child(child_name)
        child_params = _chunk_params_for_child(params)

        parent_size = max(1, params.chunk_size * ratio)
        parent_strategy = self._build_child(parent_name)
        parent_params = ChunkParams(
            chunk_size=parent_size,
            chunk_overlap=params.chunk_overlap,
            input_policy=params.input_policy,
        )
        policy = params.input_policy or getattr(self._embedding, "input_policy", None)
        parents = (
            protected_split(text, parent_size, policy)
            if has_structure
            else await parent_strategy.split(text, params=parent_params)
        )

        result: list[Chunk] = []
        for i, parent in enumerate(parents):
            parent.index = len(result)
            parent.metadata["kind"] = "parent"
            parent.metadata["parent_key"] = f"p{i}"
            result.append(parent)
        for parent in parents:
            if has_structure:
                # Split the source body; repeated headers are derived, not source.
                left = parent.metadata["source_start"]
                right = parent.metadata["source_end"]
                if parent.metadata.get("structure_type") == "table":
                    # A row group without its original header cannot be scanned
                    # as a table again. Keep the verified parent row group intact.
                    children = [
                        Chunk(
                            text=text[left:right],
                            index=0,
                            metadata={
                                **parent.metadata,
                                "source_start": 0,
                                "source_end": right - left,
                            },
                        )
                    ]
                    children[0].metadata.pop("derived_prefix", None)
                    children[0].metadata.pop("derived_context", None)
                else:
                    children = protected_split(text[left:right], child_params.chunk_size, policy)
                for child in children:
                    child.metadata["source_start"] += left
                    child.metadata["source_end"] += left
                    child.metadata["source_text_sha256"] = parent.metadata["source_text_sha256"]
                    local_headings = child.metadata.get("source_heading_ranges", [])
                    if not local_headings:
                        child.metadata["section_path"] = list(parent.metadata.get("section_path", []))
                        child.metadata["source_heading_ranges"] = [
                            dict(heading) for heading in parent.metadata.get("source_heading_ranges", [])
                        ]
                    elif parent.metadata.get("structure_type") != "table":
                        # Local headings replace their level and descendants,
                        # while retaining ancestors inherited from the document.
                        # Identity uses source coordinates, never title strings.
                        merged_headings = [
                            dict(heading) for heading in parent.metadata.get("source_heading_ranges", [])
                        ]
                        for heading in local_headings:
                            absolute_heading = {
                                **heading,
                                "start": heading["start"] + left,
                                "end": heading["end"] + left,
                            }
                            merged_headings = [
                                inherited for inherited in merged_headings if inherited["level"] < heading["level"]
                            ]
                            merged_headings.append(absolute_heading)
                        child.metadata["source_heading_ranges"] = merged_headings
                        child.metadata["section_path"] = [heading["title"] for heading in merged_headings]
                    prefix = parent.metadata.get("derived_prefix", "")
                    if prefix and not child.metadata.get("derived_prefix"):
                        child.text = prefix + child.text
                        child.metadata["derived_prefix"] = prefix
                        child.metadata["derived_context"] = [{"kind": "repeated_table_header", "text": prefix}]
                        reason = policy.check(child.text) if policy is not None else None
                        if reason is not None:
                            child.metadata["non_vectorization_reason"] = reason
                            child.metadata["oversized"] = reason == "input_limit_exceeded"
            else:
                children = await child_strategy.split(parent.text, params=child_params)
            for child in children:
                child.index = len(result)
                child.parent_id = parent.metadata["parent_key"]
                child.metadata["kind"] = "child"
                result.append(child)
        return result
