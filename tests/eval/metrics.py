"""RAG 检索层确定性指标。

只做纯计算，不触碰数据库、网络或配置，便于单测锁定语义并在基线报告中复现。

约定：期望文档为空的案例（no_answer / private_resource / cross_tenant 等）
在排序指标上返回 ``None`` 表示「不适用」，由调用方排除出分母，
而不是记 0 分——否则拒答类案例会凭空拉低或抬高检索分数。
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence

_WHITESPACE_RE = re.compile(r"\s+")


def dedupe_preserving_order(doc_ids: Iterable[str]) -> list[str]:
    """按首次出现顺序去重：多个分块命中同一文档时只算一次排名。"""
    seen: set[str] = set()
    ordered: list[str] = []
    for doc_id in doc_ids:
        if doc_id not in seen:
            seen.add(doc_id)
            ordered.append(doc_id)
    return ordered


def recall_at_k(ranked_doc_ids: Sequence[str], expected: set[str], k: int) -> float | None:
    """Recall@k = 前 k 个文档中命中的期望文档占全部期望文档的比例。"""
    if not expected:
        return None
    hit = len(set(ranked_doc_ids[:k]) & expected)
    return hit / len(expected)


def reciprocal_rank(ranked_doc_ids: Sequence[str], expected: set[str]) -> float | None:
    """首个命中期望文档的倒数排名；全部未命中记 0。"""
    if not expected:
        return None
    for position, doc_id in enumerate(ranked_doc_ids, start=1):
        if doc_id in expected:
            return 1.0 / position
    return 0.0


def ndcg_at_k(ranked_doc_ids: Sequence[str], expected: set[str], k: int) -> float | None:
    """二值增益 nDCG@k；理想排序为全部期望文档占据前列。"""
    if not expected:
        return None
    dcg = sum(
        1.0 / math.log2(position + 2)
        for position, doc_id in enumerate(ranked_doc_ids[:k])
        if doc_id in expected
    )
    ideal_hits = min(len(expected), k)
    idcg = sum(1.0 / math.log2(position + 2) for position in range(ideal_hits))
    if idcg == 0.0:
        return None
    return dcg / idcg


def percentile(values: Sequence[float], p: float) -> float | None:
    """最近秩分位数（nearest-rank）；空输入返回 None。"""
    if not values:
        return None
    ordered = sorted(values)
    rank = math.ceil(p / 100.0 * len(ordered))
    index = min(max(rank, 1), len(ordered)) - 1
    return ordered[index]


def normalize_for_quote(text: str) -> str:
    """去掉全部空白后比较，避免切分换行导致原文引用漏判。"""
    return _WHITESPACE_RE.sub("", text or "")


def quote_hit(chunk_texts: Sequence[str], quote: str) -> bool:
    """检索命中的分块中是否逐字包含该证据原文。"""
    needle = normalize_for_quote(quote)
    if not needle:
        return False
    return any(needle in normalize_for_quote(text) for text in chunk_texts)


def mean_or_none(values: Sequence[float]) -> float | None:
    """对可用样本求均值；无样本返回 None，避免用 0 伪装「没测到」。"""
    if not values:
        return None
    return sum(values) / len(values)
