"""ADR-0003：查询日期解析与 scheduled 窗口。

解析失败返回 None，调用方不得打开预告文档。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

_ISO_DATE = re.compile(r"(20\d{2}-\d{2}-\d{2})")
_YEAR = re.compile(r"(20\d{2})")

SCHEDULED_NOTICE = "【未生效预告，不得作为当前收费或政策答复】"


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def parse_query_schedule_at(query: str, as_of: datetime) -> datetime | None:
    """从查询中取出明确的未来日期；没有则不打开 scheduled。"""
    as_of = ensure_utc(as_of)
    text = query or ""
    iso = _ISO_DATE.search(text)
    if iso:
        try:
            parsed = datetime.fromisoformat(iso.group(1)).replace(tzinfo=UTC)
        except ValueError:
            return None
        return parsed if parsed.date() > as_of.date() else None
    years = [int(match) for match in _YEAR.findall(text)]
    future = [year for year in years if year > as_of.year]
    if not future:
        return None
    return datetime(max(future), 1, 1, tzinfo=UTC)


def document_is_live(
    is_current: bool,
    effective_at: datetime | None,
    expires_at: datetime | None,
    as_of: datetime,
) -> bool:
    """已发布且对 as_of 已生效、未过期。"""
    if not is_current:
        return False
    as_of = ensure_utc(as_of)
    if effective_at is not None and ensure_utc(effective_at) > as_of:
        return False
    if expires_at is not None and as_of >= ensure_utc(expires_at):
        return False
    return True


def document_is_scheduled(
    is_current: bool,
    effective_at: datetime | None,
    as_of: datetime,
    schedule_at: datetime | None,
) -> bool:
    """未发布指针、生效日在未来、且落在查询日期窗口内。"""
    if is_current or schedule_at is None or effective_at is None:
        return False
    as_of = ensure_utc(as_of)
    effective = ensure_utc(effective_at)
    target = ensure_utc(schedule_at)
    if effective <= as_of:
        return False
    return effective.year == target.year


def document_version_status(
    *,
    is_current: bool,
    effective_at: datetime | None,
    expires_at: datetime | None,
    as_of: datetime,
    schedule_at: datetime | None,
) -> str | None:
    """可见则返回 current/scheduled，否则 None。"""
    if document_is_live(is_current, effective_at, expires_at, as_of):
        return "current"
    if document_is_scheduled(is_current, effective_at, as_of, schedule_at):
        return "scheduled"
    return None


def retrieval_window(query: str, as_of: datetime | None = None) -> tuple[datetime, datetime | None]:
    """Flag 关闭时不解析预告窗口。"""
    from app.core.config import settings

    now = ensure_utc(as_of) if as_of is not None else datetime.now(UTC)
    if not settings.RAG_EFFECTIVE_DATE_FILTER:
        return now, None
    return now, parse_query_schedule_at(query, now)


def parse_manifest_datetime(value: object) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
