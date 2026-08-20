"""离线 / 测试用嵌入模型。

不依赖任何外部服务，基于「词哈希词袋 + L2 归一化」生成确定性向量：
相同 / 相近的词汇会落入相同哈希桶，从而使余弦相似度近似反映词面重合度。
这保证了在没有真实嵌入 API 时，混合检索（稠密 + BM25）仍有可解释的结果，
便于本地开发、演示与单元测试。
"""

import hashlib
import math
import re
from collections.abc import Sequence

from app.rag.embeddings.base import EmbeddingProvider

# 中英文统一的 token 切分：连续字母数字下划线 + 连续 CJK 单字。
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    """将文本切分为归一化 token（小写；中文按单字切分）。"""
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


class MockEmbeddingProvider(EmbeddingProvider):
    """确定性伪嵌入：哈希词袋向量，余弦相似度近似词面重合度。"""

    def __init__(self, dim: int = 256, seed: int = 0) -> None:
        self.model = "mock-embedding"
        self.dim = dim
        self._seed = seed

    def _vector_for_text(self, text: str) -> list[float]:
        buckets = [0.0] * self.dim
        for tok in tokenize(text):
            digest = hashlib.md5(f"{self._seed}:{tok}".encode("utf-8")).hexdigest()
            buckets[int(digest, 16) % self.dim] += 1.0
        norm = math.sqrt(sum(b * b for b in buckets))
        if norm == 0.0:
            return buckets
        return [b / norm for b in buckets]

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector_for_text(t) for t in texts]
