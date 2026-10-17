"""检索状态与最终回复契约（RAG-037）。

检索不再是「返回一段文本」这一个结果，而是「状态 + 命中 + 披露要求」三元组。
状态让入口可以区分「知识库里确实没有」与「检索根本没跑完」，后者必须明确
告知用户，不能让模型用自身知识冒充知识库结论。

判定顺序唯一：异常 > 零候选 > 全低分 > 正常。少返回（部分命中被阈值剔除）
不算异常，只做计数，不补位也不改排名。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.core.config import settings
from app.rag.vectorstore.base import ChunkResult


class RetrievalStatus(str, Enum):
    """一次检索的终态。"""

    OK = "ok"  # 有命中且至少一条通过阈值
    NO_HIT = "no_hit"  # 检索成功，但零候选
    BELOW_THRESHOLD = "below_threshold"  # 有候选，但全部低于 RAG_MIN_SIMILARITY
    UNAVAILABLE = "unavailable"  # 检索未完成（嵌入 / 向量库 / 后端异常）


# 指令块前缀。带这个前缀的内容是给模型的指令，不是不可信资料，
# 因此不进 [UNTRUSTED_SOURCE] 围栏。
DISCLOSURE_PREFIX = "## 检索状态要求\n"


@dataclass(frozen=True)
class RetrievalOutcome:
    """一次检索的结果与状态。

    ``hits`` 只含最终可用的命中；被阈值剔除的数量记在
    ``dropped_by_threshold``，调用方可据此判断 top-k 被削了多少。
    """

    status: RetrievalStatus
    hits: list[ChunkResult] = field(default_factory=list)
    dropped_by_threshold: int = 0
    backend: str = ""
    error_type: str = ""

    @property
    def is_partial(self) -> bool:
        """少返回：有可用命中，但另有命中被阈值剔除。"""
        return self.status is RetrievalStatus.OK and self.dropped_by_threshold > 0

    @property
    def notice(self) -> str:
        """面向最终回复的确定性披露语；无需披露时返回空串。

        关闭 ``RAG_STATUS_NOTICE_ENABLED`` 即整体回退到「不注入披露要求」的
        旧行为，但状态本身仍会被记录与暴露。
        """
        if not settings.RAG_STATUS_NOTICE_ENABLED:
            return ""
        if self.status is RetrievalStatus.NO_HIT:
            return settings.RAG_NO_HIT_NOTICE
        if self.status is RetrievalStatus.BELOW_THRESHOLD:
            return settings.RAG_REFUSE_MESSAGE
        if self.status is RetrievalStatus.UNAVAILABLE:
            return settings.RAG_UNAVAILABLE_NOTICE
        return ""

    def directive(self) -> str:
        """送模型的指令块（非不可信资料）；无需披露时返回空串。"""
        text = self.notice.strip()
        if not text:
            return ""
        return f"{DISCLOSURE_PREFIX}{text}"

    def terminal_reply(self) -> str | None:
        """无需再生成的终态回复；``None`` 表示继续正常生成。

        只有「有候选但全低分」才终态拒答：拒答语本身就是面向用户的结论，
        交给模型改写只会把它变成一段看起来像知识回答的文字。
        ``unavailable`` / ``no_hit`` 不在这里终态——检索故障时把整个对话
        掐断是另一种极端，改为由 ``disclosure_prefix`` 确定性前置披露。
        """
        if self.status is not RetrievalStatus.BELOW_THRESHOLD:
            return None
        if not settings.RAG_STATUS_NOTICE_ENABLED:
            return None
        return settings.RAG_REFUSE_MESSAGE

    def disclosure_prefix(self) -> str:
        """应用层确定性前置到最终回复的披露语；无需披露时返回空串。

        这是「不冒充知识回答」的兜底：模型就算完全忽略 prompt 里的指令，
        用户看到的第一句话也是确定的。与 ``notice``（给模型）分开设值。
        """
        if not settings.RAG_STATUS_NOTICE_ENABLED:
            return ""
        if self.status is RetrievalStatus.NO_HIT:
            return settings.RAG_NO_HIT_REPLY
        if self.status is RetrievalStatus.UNAVAILABLE:
            return settings.RAG_UNAVAILABLE_REPLY
        return ""


def classify(
    *,
    error_type: str = "",
    candidates: int = 0,
    kept: int = 0,
) -> RetrievalStatus:
    """按唯一的判定顺序给出状态。

    ``error_type`` 非空优先，因为检索没跑完时，候选数与保留数都没有意义。
    """
    if error_type:
        return RetrievalStatus.UNAVAILABLE
    if candidates <= 0:
        return RetrievalStatus.NO_HIT
    if kept <= 0:
        return RetrievalStatus.BELOW_THRESHOLD
    return RetrievalStatus.OK
