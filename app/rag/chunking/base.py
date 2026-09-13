"""切分策略层统一协议与模型。

定义策略接口 ``ChunkingStrategy``、统一切分结果 ``Chunk`` 与策略参数
``ChunkParams``。切分结果带 ``parent_id`` / ``metadata``，为父子文档与溯源打基础。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(slots=True)
class Chunk:
    """一个切分结果块。"""

    text: str
    index: int
    parent_id: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass(slots=True)
class ChunkParams:
    """切分参数，通用字段 + 各策略专用字段（可空）。"""

    chunk_size: int = 500
    chunk_overlap: int = 64
    # 滑动窗口
    window_size: int | None = None
    step: int | None = None
    # token 感知
    max_tokens: int | None = None
    overlap_tokens: int | None = None
    # 语义切分
    similarity_threshold: float | None = None
    # 父子文档
    parent_strategy: str | None = None
    parent_ratio: int = 3
    child_strategy: str | None = None
    child_params: dict | None = None


class ChunkingStrategy(ABC):
    """切分策略统一接口。"""

    name: str

    @abstractmethod
    async def split(self, text: str, *, params: ChunkParams) -> list[Chunk]:
        """将文本切分为块列表，空输入返回空列表。"""
        raise NotImplementedError

    async def split_blocks(self, blocks: list, *, params: ChunkParams) -> list[Chunk]:
        """将结构块列表切分为块列表。

        默认回退为拼接块文本后走普通 `split`，供尚未实现结构感知的策略复用。
        """
        text = "\n".join(
            str(getattr(block, "text", "")).strip()
            for block in blocks
            if str(getattr(block, "text", "")).strip()
        )
        return await self.split(text, params=params)
