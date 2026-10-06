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


def protected_split(text: str, chunk_size: int, policy: Any = None) -> list[Chunk]:
    """Partition source exactly; atoms exceed soft targets instead of being cut."""
    result: list[Chunk] = []
    target = max(1, chunk_size)
    units = structure_units(text)

    def plain(start: int, end: int) -> None:
        while start < end:
            stop = min(end, start + target)
            # Prose may split at characters, but honor a verified provider limit.
            if policy is not None:
                while stop > start + 1 and policy.check(text[start:stop]) == "input_limit_exceeded":
                    stop = start + max(1, (stop - start) // 2)
            result.append(_chunk(text, start, stop, "text", policy))
            start = stop

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
