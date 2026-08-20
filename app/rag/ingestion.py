"""文档摄取：将长文本拆分为可检索的分块。

分块策略：按句末标点切分为句子，再将句子顺序拼接到不超过 ``chunk_size`` 的
窗口内；相邻分块共享 ``chunk_overlap`` 个字符的尾部重叠，以降低跨块语义断裂。
单个超长句子（如未分段的整段）会被硬切分为若干块。
"""

import re

# 句末切分：保留中英文标点作为句子边界。
_SENTENCE_RE = re.compile(r"[^。！？!?\n]+[。！？!?]?")


def split_text(text: str, chunk_size: int = 500, chunk_overlap: int = 64) -> list[str]:
    """将文本切分为带重叠的分块列表。

    Args:
        text: 待切分文本。
        chunk_size: 单块字符数上限。
        chunk_overlap: 相邻块尾部重叠字符数。

    Returns:
        非空分块列表；空输入返回空列表。
    """
    text = (text or "").strip()
    if not text:
        return []

    sentences = [s.strip() for s in _SENTENCE_RE.findall(text) if s.strip()]
    if not sentences:
        sentences = [text]

    chunks: list[str] = []
    buf = ""
    tail = ""
    for sentence in sentences:
        candidate = tail + sentence
        if len(buf) + len(candidate) <= chunk_size:
            buf += candidate
            tail = ""
            continue

        if buf:
            chunks.append(buf)
            tail = buf[-chunk_overlap:] if chunk_overlap else ""
        if len(candidate) > chunk_size:
            # 超长句子：硬切分并逐段回填尾部重叠。
            for i in range(0, len(candidate), chunk_size):
                piece = candidate[i : i + chunk_size]
                chunks.append(piece)
                tail = piece[-chunk_overlap:] if chunk_overlap else ""
            buf = ""
        else:
            buf = candidate
            tail = ""

    if buf:
        chunks.append(buf)
    return [c.strip() for c in chunks if c.strip()]
