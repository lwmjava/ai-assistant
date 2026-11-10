"""查询改写：在检索前做确定性扩展与澄清，不调用模型。

只追加词项，不替换原句，避免丢掉用户原词。未来年份或预告意图会补上
生效日和预告用语；“今天/当前”类问题只强调现行标准，不打开预告用语。
本层不开关生效日过滤：是否纳入 scheduled 文档仍由 RAG_EFFECTIVE_DATE_FILTER 决定。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime

_ISO_DATE = re.compile(r"(20\d{2}-\d{2}-\d{2})")
_YEAR = re.compile(r"(20\d{2})")
_FUTURE_INTENT = re.compile(r"(打算|计划|预告|即将|将要|拟于|调整为)")
_PRESENT_INTENT = re.compile(r"(今天|今日|现在|当前|现行|目前)")

_FUTURE_TERMS = ("价格调整预告", "预告价格", "尚未生效")
_PRESENT_TERMS = ("现行", "当前收费标准")


@dataclass(frozen=True, slots=True)
class RewrittenQuery:
    """改写结果：始终保留原句，额外词项只追加在后面。"""

    original: str
    text: str
    expanded: bool


def query_rewrite_enabled() -> bool:
    """生产路径默认关闭；设置 RAG_QUERY_REWRITE_ENABLED=true 可开启。不写配置文件。"""
    raw = os.getenv("RAG_QUERY_REWRITE_ENABLED", "false").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def rewrite_query(query: str, *, as_of: datetime | None = None) -> RewrittenQuery:
    """对检索语句做确定性扩展。

    空句原样返回。已包含的追加词不会重复写入。
    """
    original = (query or "").strip()
    if not original:
        return RewrittenQuery(original=original, text=original, expanded=False)

    now = as_of.astimezone(UTC) if as_of is not None else datetime.now(UTC)
    extras: list[str] = []
    future_years = [int(year) for year in _YEAR.findall(original) if int(year) > now.year]
    future_iso: list[str] = []
    for match in _ISO_DATE.findall(original):
        try:
            parsed = datetime.fromisoformat(match).replace(tzinfo=UTC)
        except ValueError:
            continue
        if parsed.date() > now.date():
            future_iso.append(match)

    present = bool(_PRESENT_INTENT.search(original))
    future_intent = bool(_FUTURE_INTENT.search(original)) or bool(future_years) or bool(future_iso)
    if future_intent and not present:
        if future_iso:
            extras.extend(future_iso)
        elif future_years:
            extras.append(f"{max(future_years)}-01-01")
        extras.extend(_FUTURE_TERMS)
    elif present and not future_years and not future_iso:
        extras.extend(_PRESENT_TERMS)

    additions = [term for term in extras if term and term not in original]
    # 去重但保序
    seen: set[str] = set()
    unique: list[str] = []
    for term in additions:
        if term in seen:
            continue
        seen.add(term)
        unique.append(term)
    if not unique:
        return RewrittenQuery(original=original, text=original, expanded=False)
    return RewrittenQuery(
        original=original,
        text=f"{original} {' '.join(unique)}",
        expanded=True,
    )
