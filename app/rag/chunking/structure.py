"""Deterministic source-preserving structural fallback for existing strategies.

Offsets refer to the input text, never binary source-file offsets. Structured
inputs deliberately bypass soft-size/overlap behavior that could cut atoms.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from app.rag.chunking.base import Chunk

VERSION = "structure-integrity-v0.1"
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_ASCII_BORDER = re.compile(r"^\s*\+(?:[-=]+\+)+\s*$")
_UNICODE_BOX = re.compile(r"[┌┐└┘├┤┬┴┼╔╗╚╝╠╣╦╩╬]")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$")


@dataclass(frozen=True)
class StructureUnit:
    start: int
    end: int
    kind: str
    header_end: int | None = None


@dataclass(frozen=True)
class _GapPlan:
    """一个正文间隙的切分计划（RAG-031）。offsets 相对间隙起点的字符偏移。"""

    offsets: tuple[int, ...]
    meta: dict[str, object]


def structure_units(text: str) -> list[StructureUnit]:
    """Find nonoverlapping fenced blocks, conservative boxes and pipe tables."""
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    units: list[StructureUnit] = []
    i = 0
    while i < len(lines):
        line = lines[i].rstrip("\r\n")
        fence = _FENCE.match(line)
        if fence:
            marker = fence.group(1)
            # Backtick info strings containing backticks are not fence openings.
            if marker[0] == "`" and "`" in fence.group(2):
                i += 1
                continue
            j = i + 1
            close = re.compile(r"^ {0,3}" + re.escape(marker[0]) + "{" + str(len(marker)) + r",}\s*$")
            while j < len(lines) and not close.match(lines[j].rstrip("\r\n")):
                j += 1
            j = min(j + 1, len(lines))
            units.append(StructureUnit(offsets[i], offsets[j], "fenced_code"))
            i = j
            continue
        if _ASCII_BORDER.match(line) or _UNICODE_BOX.search(line):
            j = i + 1
            while j < len(lines):
                candidate = lines[j].rstrip("\r\n")
                if not candidate.strip() or not (
                    _ASCII_BORDER.match(candidate)
                    or _UNICODE_BOX.search(candidate)
                    or "│" in candidate
                    or "║" in candidate
                    or candidate.lstrip().startswith("|")
                ):
                    break
                j += 1
            units.append(StructureUnit(offsets[i], offsets[j], "box_diagram"))
            i = j
            continue
        if "|" in line and i + 1 < len(lines) and _TABLE_SEPARATOR.match(lines[i + 1].rstrip("\r\n")):
            j = i + 2
            while j < len(lines) and "|" in lines[j] and lines[j].strip():
                j += 1
            units.append(StructureUnit(offsets[i], offsets[j], "table", offsets[i + 2]))
            i = j
            continue
        i += 1
    return units


def _chunk(text: str, start: int, end: int, kind: str, policy: Any, prefix: str = "") -> Chunk:
    content = prefix + text[start:end]
    metadata: dict[str, Any] = {
        "source_start": start,
        "source_end": end,
        "source_coordinate": "input_text",
        "source_version": VERSION,
        "structure_type": kind,
        "structure_protection": "rule_fallback",
    }
    if prefix:
        metadata["derived_prefix"] = prefix
        metadata["derived_context"] = [{"kind": "repeated_table_header", "text": prefix}]
    if policy is None:
        metadata["limit_unverified"] = True
    else:
        describe = getattr(policy, "metadata", None)
        if describe is not None:
            metadata["embedding_input_policy"] = describe()
        reason = policy.check(content)
        if reason is not None:
            metadata["non_vectorization_reason"] = reason
            if reason == "input_limit_exceeded":
                metadata["oversized"] = True
            else:
                metadata["limit_unverified"] = True
    return Chunk(text=content, index=0, metadata=metadata)


def protected_split(
    text: str,
    chunk_size: int,
    policy: Any = None,
    boundaries: dict[int, _GapPlan] | None = None,
) -> list[Chunk]:
    """Partition source exactly; atoms exceed soft targets instead of being cut.

    ``boundaries``（RAG-031）为正文间隙起点 -> 边界计划；``None`` 时行为与
    结构保护基线完全一致（默认关闭路径）。
    """
    result: list[Chunk] = []
    target = max(1, chunk_size)
    units = structure_units(text)

    def emit_one(start: int, end: int, extra_meta: dict | None) -> None:
        chunk = _chunk(text, start, end, "text", policy)
        if extra_meta:
            chunk.metadata.update(extra_meta)
        result.append(chunk)

    def emit_interval(start: int, end: int, extra_meta: dict | None) -> None:
        """emit [start,end) under policy; subdivide by char if it exceeds the limit."""
        if policy is None or policy.check(text[start:end]) != "input_limit_exceeded":
            emit_one(start, end, extra_meta)
            return
        cursor = start
        while cursor < end:
            stop = min(end, cursor + target)
            while stop > cursor + 1 and policy.check(text[cursor:stop]) == "input_limit_exceeded":
                stop = cursor + max(1, (stop - cursor) // 2)
            emit_one(cursor, stop, extra_meta)
            cursor = stop

    def plain(start: int, end: int) -> None:
        plan = boundaries.get(start) if boundaries is not None else None
        if plan is not None and plan.offsets:
            bounds = [0, *plan.offsets, end - start]
            for index in range(len(bounds) - 1):
                emit_interval(start + bounds[index], start + bounds[index + 1], plan.meta)
            return
        # 规则字符切分。plan 存在但无偏移（降级）时，把降级原因写进 chunk。
        extra = plan.meta if plan is not None else None
        cursor = start
        while cursor < end:
            stop = min(end, cursor + target)
            if policy is not None:
                while stop > cursor + 1 and policy.check(text[cursor:stop]) == "input_limit_exceeded":
                    stop = cursor + max(1, (stop - cursor) // 2)
            emit_one(cursor, stop, extra)
            cursor = stop

    cursor = 0
    for unit in units:
        plain(cursor, unit.start)
        if unit.kind != "table":
            result.append(_chunk(text, unit.start, unit.end, unit.kind, policy))
        else:
            assert unit.header_end is not None
            header = text[unit.start : unit.header_end]
            rows = text[unit.header_end : unit.end].splitlines(keepends=True)
            start, end = unit.start, unit.header_end
            prefix = ""
            for row in rows:
                candidate = prefix + text[start:end] + row
                exceeds_limit = policy is not None and policy.check(candidate) == "input_limit_exceeded"
                has_data_row = end > unit.header_end if start == unit.start else end > start
                if has_data_row and (len(candidate) > target or exceeds_limit):
                    result.append(_chunk(text, start, end, "table", policy, prefix))
                    start = end
                    prefix = header
                end += len(row)
            if end > start:
                result.append(_chunk(text, start, end, "table", policy, prefix))
        cursor = unit.end
    plain(cursor, len(text))
    source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    # Section provenance is source metadata, not an unmarked repeated prefix.
    headings: list[dict[str, Any]] = []
    heading_events: list[tuple[int, list[dict[str, Any]]]] = []
    offset = 0
    unit_index = 0
    for line in text.splitlines(keepends=True):
        while unit_index < len(units) and units[unit_index].end <= offset:
            unit_index += 1
        in_structure = unit_index < len(units) and units[unit_index].start <= offset < units[unit_index].end
        match = _HEADING.match(line.rstrip("\r\n")) if not in_structure else None
        if match:
            level = len(match.group(1))
            headings = [heading for heading in headings if heading["level"] < level]
            headings.append(
                {
                    "start": offset,
                    "end": offset + len(line),
                    "level": level,
                    "title": match.group(2).strip(),
                    "text": line,
                }
            )
            heading_events.append((offset, list(headings)))
        offset += len(line)
    event_index = 0
    active: list[dict[str, Any]] = []
    for index, chunk in enumerate(result):
        chunk.index = index
        chunk.metadata["source_text_sha256"] = source_hash
        while event_index < len(heading_events) and heading_events[event_index][0] <= chunk.metadata["source_start"]:
            active = heading_events[event_index][1]
            event_index += 1
        chunk.metadata["section_path"] = [heading["title"] for heading in active]
        chunk.metadata["source_heading_ranges"] = [dict(heading) for heading in active]
    return result


async def resolve_boundary_table(
    text: str,
    chunk_size: int,
    advisor: Any,
) -> dict[int, _GapPlan] | None:
    """RAG-031：对正文间隙异步收集 LLM 边界建议，同步切分仍走 ``protected_split``。

    ``advisor`` 为 ``None``（默认关闭）时返回 ``None``，``protected_split`` 行为
    与结构保护基线一致。只对超过软目标的正文间隙发起有界调用；任何失败都降级为
    规则切分并把原因写进 chunk 元数据。
    """
    if advisor is None:
        return None
    target = max(1, chunk_size)
    units = structure_units(text)
    gaps: list[tuple[int, int]] = []
    cursor = 0
    for unit in units:
        gaps.append((cursor, unit.start))
        cursor = unit.end
    gaps.append((cursor, len(text)))

    table: dict[int, _GapPlan] = {}
    base_meta = advisor.metadata_snapshot()
    for start, end in gaps:
        if end - start <= target:
            continue
        outcome = await advisor.suggest(text[start:end], target_chars=target)
        if outcome.kind == "offsets" and outcome.offsets:
            table[start] = _GapPlan(
                offsets=outcome.offsets,
                meta={**base_meta, "llm_boundary_used": True},
            )
        else:
            table[start] = _GapPlan(
                offsets=(),
                meta={
                    **base_meta,
                    "llm_boundary_used": False,
                    "llm_boundary_degraded_reason": outcome.reason or "no_boundaries",
                },
            )
    return table
