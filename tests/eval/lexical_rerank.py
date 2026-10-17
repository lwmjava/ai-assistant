"""功能概述
对已经截断的检索结果按词面覆盖率重排，并按调参集决定是否采纳。

功能涵盖
- 覆盖率是查询词集合与分块词集合的交集大小，除以查询词集合大小。
- 覆盖率相同则保持原顺序。
- 只根据 development 与 validation 的汇总做决定，本模块不读取 holdout。

已完成
- 分词使用与混合检索相同的 tokenize。
- 三条同时成立才采纳：Recall@1 更高、MRR 更高、越权案例数不更高。

待完善
- 不在这里改检索实现。是否写进本地检索由实验结论决定。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, TypeVar

from app.rag.embeddings.mock import tokenize

T = TypeVar("T")


def lexical_coverage(query_tokens: Sequence[str], chunk_text: str) -> float:
    """计算一份分块相对查询的词面覆盖率。

    作用：只比较词集合，不看词频，也不看向量距离。
    入参：query_tokens 为已经用 tokenize 切好的查询词；chunk_text 为分块原文。
    出参：0 到 1 的覆盖率。查询词为空时返回 0。
    """
    query_terms = set(query_tokens)
    chunk_terms = set(tokenize(chunk_text))
    return len(query_terms & chunk_terms) / max(len(query_terms), 1)


def rerank_by_lexical_coverage(query_tokens: Sequence[str], chunks: Sequence[T]) -> list[T]:
    """按词面覆盖率重排已经截断的命中。

    作用：不增删条目。覆盖率高的在前；覆盖率相同则保持原来的次序。
    入参：query_tokens 为查询词；chunks 为已截断列表，元素需有 content 属性。
    出参：新的列表。原列表不被就地修改。
    """
    indexed = list(enumerate(chunks))
    indexed.sort(
        key=lambda item: (-lexical_coverage(query_tokens, item[1].content), item[0])
    )
    return [item[1] for item in indexed]


def decide_lexical_coverage(
    control_summary: dict[str, Any],
    treatment_summary: dict[str, Any],
) -> dict[str, Any]:
    """决定词面覆盖重排是否采纳。

    作用：只看调参集汇总。Recall@1 与 MRR 都严格高于对照，且越权案例数不高于对照，才采纳。
    入参：两份汇总都需含 recall['@1']、mrr、safety.violation_cases。不要传入 holdout。
    出参：字典，含 decision（adopt 或 keep）、reason_code、reason。
    """
    ok, reason_code = _judge(control_summary, treatment_summary)
    if ok:
        reason = "采纳 lexical_coverage：调参集上 Recall@1 与 MRR 都更高，且越权案例未增加"
        decision = "adopt"
    else:
        reason = "不采纳 lexical_coverage：调参集上没有同时提升 Recall@1 与 MRR 且不增加越权案例"
        decision = "keep"
    return {
        "decision": decision,
        "reason_code": reason_code,
        "reason": reason,
        "control_recall@1": control_summary["recall"]["@1"],
        "treatment_recall@1": treatment_summary["recall"]["@1"],
        "control_mrr": control_summary["mrr"],
        "treatment_mrr": treatment_summary["mrr"],
        "control_violation_cases": control_summary["safety"]["violation_cases"],
        "treatment_violation_cases": treatment_summary["safety"]["violation_cases"],
    }


def _judge(control: dict[str, Any], treatment: dict[str, Any]) -> tuple[bool, str]:
    """判断处理是否允许替换对照顺序。

    作用：三条规则同时成立才返回采纳。
    入参：control、treatment 均为评测汇总。
    出参：(是否采纳, 原因码)。
    """
    if treatment["safety"]["violation_cases"] > control["safety"]["violation_cases"]:
        return False, "safety_regression"
    control_recall = control["recall"]["@1"]
    treatment_recall = treatment["recall"]["@1"]
    control_mrr = control["mrr"]
    treatment_mrr = treatment["mrr"]
    if None in (control_recall, treatment_recall, control_mrr, treatment_mrr):
        return False, "missing_primary_metric"
    if treatment_recall < control_recall or treatment_mrr < control_mrr:
        return False, "primary_regression"
    if treatment_recall > control_recall and treatment_mrr > control_mrr:
        return True, "adopt"
    return False, "not_both_primary_improved"
