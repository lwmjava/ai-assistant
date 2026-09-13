"""token 感知切分策略：按 token 数切分而非字符数。"""

from __future__ import annotations

import re

from app.rag.chunking.base import Chunk, ChunkingStrategy, ChunkParams

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数：中文 1 字符 ≈ 1 token，其余约 4 字符 / token。"""
    cjk = len(_CJK_RE.findall(text))
    other = len(text) - cjk
    return cjk + (other + 3) // 4


def _try_load_tokenizer():
    """优先加载 tiktoken，不可用则返回 None。"""
    try:
        import tiktoken  # noqa: PLC0415

        return tiktoken.get_encoding("cl100k_base").encode
    except Exception:  # noqa: BLE001 - tiktoken 可选
        return None


class TokenAwareChunkingStrategy(ChunkingStrategy):
    """按 token 上限切分，优先 tiktoken，缺失时回退估算。"""

    name = "token_aware"

    async def split(self, text: str, *, params: ChunkParams) -> list[Chunk]:
        text = (text or "").strip()
        if not text:
            return []
        max_tokens = params.max_tokens or max(1, params.chunk_size // 2)
        encode = _try_load_tokenizer()

        def count(value: str) -> int:
            return len(encode(value)) if encode else _estimate_tokens(value)

        chunks: list[Chunk] = []
        buf = ""
        for char in text:
            if count(buf + char) > max_tokens and buf:
                chunks.append(Chunk(text=buf.strip(), index=len(chunks)))
                buf = char
            else:
                buf += char
        if buf.strip():
            chunks.append(Chunk(text=buf.strip(), index=len(chunks)))
        return chunks
