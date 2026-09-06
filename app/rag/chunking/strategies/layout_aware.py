"""版式感知切分策略：按页码与阅读顺序重建阅读流后切分。

优先按页面分组，再在页内做版式重排：
- 带 ``bbox`` 且非表格的块，先按 x 轴投影空白带分成左右列（双栏），
  列内按 y 从上到下排序，从而还原“先左栏读完再读右栏”的阅读流；
- 无 ``bbox`` 或表格块，保持解析层给出的 ``reading_order``。
"""

from __future__ import annotations

from typing import Any

from app.rag.chunking.base import Chunk, ChunkingStrategy, ChunkParams
from app.rag.chunking.strategies.recursive import RecursiveChunkingStrategy

# 相邻 x 投影区间的空白带宽度超过页面有效宽度的该比例时，视为列分隔。
_COLUMN_GAP_RATIO = 0.08


def _reading_order_key(block: Any) -> tuple[int, int]:
    """返回无 bbox 块用于排序的 (reading_order, order) 键。"""
    metadata = getattr(block, "metadata", {}) or {}
    reading_order = metadata.get("reading_order")
    if reading_order is None:
        reading_order = getattr(block, "order", 0)
    return (reading_order or 0, getattr(block, "order", 0) or 0)


def _is_table_block(block: Any) -> bool:
    """判断块是否为表格相关角色。"""
    role = str((getattr(block, "metadata", {}) or {}).get("layout_role", "") or "")
    return role.startswith("table")


def _split_into_columns(blocks: list[Any]) -> list[list[Any]]:
    """按 x 轴投影的空白带把块分成左右列，并逐列按 y 排序。"""
    if len(blocks) < 2:
        return [blocks]

    x_intervals = sorted(
        (float(block.bbox[0]), float(block.bbox[2])) for block in blocks
    )
    merged: list[tuple[float, float]] = []
    for x0, x1 in x_intervals:
        if merged and x0 <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], x1))
        else:
            merged.append((x0, x1))

    page_width = merged[-1][1] - merged[0][0]
    if page_width <= 0:
        return [blocks]

    boundaries = [
        merged[i + 1][0]
        for i in range(len(merged) - 1)
        if merged[i + 1][0] - merged[i][1] > page_width * _COLUMN_GAP_RATIO
    ]
    if not boundaries:
        return [blocks]

    columns: list[list[Any]] = [[] for _ in range(len(boundaries) + 1)]
    for block in blocks:
        center_x = (float(block.bbox[0]) + float(block.bbox[2])) / 2.0
        column_index = sum(1 for boundary in boundaries if center_x >= boundary)
        columns[column_index].append(block)

    for column in columns:
        column.sort(key=lambda block: (float(block.bbox[1]), float(block.bbox[0])))
    return columns


def _order_page_blocks(blocks: list[Any]) -> list[Any]:
    """对同一页内的块做版式重排，支持双栏阅读顺序。"""
    positioned = [block for block in blocks if block.bbox and not _is_table_block(block)]
    if len(positioned) >= 2:
        columns = _split_into_columns(positioned)
        ordered = [block for column in columns for block in column]
        static = [block for block in blocks if not (block.bbox and not _is_table_block(block))]
        ordered.extend(sorted(static, key=_reading_order_key))
        return ordered
    return sorted(blocks, key=_reading_order_key)


class LayoutAwareChunkingStrategy(ChunkingStrategy):
    """优先按页面和阅读顺序消费解析块，保留版式元信息。"""

    name = "layout_aware"

    def __init__(self) -> None:
        self._recursive = RecursiveChunkingStrategy()

    async def split(self, text: str, *, params: ChunkParams) -> list[Chunk]:
        """缺少版式块输入时，退化到递归切分。"""
        return await self._recursive.split(text, params=params)

    async def split_blocks(self, blocks: list, *, params: ChunkParams) -> list[Chunk]:
        ordered_blocks = self._order_blocks(blocks)
        chunks: list[Chunk] = []
        current_page: int | None = None
        page_order = 0
        for block in ordered_blocks:
            text = str(getattr(block, "text", "")).strip()
            if not text:
                continue
            page = getattr(block, "page", None) or 0
            if page != current_page:
                current_page = page
                page_order = 0
            page_order += 1
            metadata = {
                "block_type": str(getattr(block, "type", "text")),
                "page": getattr(block, "page", None),
                "reading_order": page_order,
                "bbox": getattr(block, "bbox", None),
                "layout_role": getattr(block, "metadata", {}).get("layout_role"),
                "source_parser": getattr(block, "metadata", {}).get("parser_name"),
            }
            if len(text) <= params.chunk_size:
                chunks.append(Chunk(text=text, index=len(chunks), metadata=metadata))
                continue
            sub_chunks = await self._recursive.split(text, params=params)
            for sub_chunk in sub_chunks:
                chunks.append(
                    Chunk(
                        text=sub_chunk.text,
                        index=len(chunks),
                        metadata=dict(metadata),
                    )
                )
        return chunks

    def _order_blocks(self, blocks: list) -> list[Any]:
        """按页码分组，页内做版式重排。"""
        by_page: dict[int, list[Any]] = {}
        for block in blocks:
            page = getattr(block, "page", None) or 0
            by_page.setdefault(page, []).append(block)
        ordered: list[Any] = []
        for page in sorted(by_page):
            ordered.extend(_order_page_blocks(by_page[page]))
        return ordered
