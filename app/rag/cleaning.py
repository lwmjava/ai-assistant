"""保守清洗：仅换行/BOM 规范化，原始结构不变，异常回退原文。"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, replace
from typing import TypedDict

from app.rag.document_parsers.base import ParsedDocument

logger = logging.getLogger(__name__)
CLEANING_VERSION = "conservative-v1"


class CleaningReport(TypedDict):
    version: str
    original_hash: str
    derived_hash: str
    fallback: bool
    error_code: str | None
    quality_score: int
    character_count: int
    replacement_count: int
    block_count: int
    extension: str
    parser_name: str
    line_count: int
    table_count: int
    page_count: int
    has_layout: bool


@dataclass
class CleaningResult:
    original: ParsedDocument
    document: ParsedDocument
    report: CleaningReport


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical(text: str) -> str:
    return text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")


def _normalize(text: str) -> str:
    return _canonical(text)


def clean_document(parsed: ParsedDocument) -> CleaningResult:
    """不改原对象；任何超出换行/BOM 的改写均拒绝并回退。"""
    error_code = None
    try:
        text = _normalize(parsed.text)
        blocks = [replace(block, text=_normalize(block.text)) for block in parsed.blocks]
        if text != _canonical(parsed.text) or any(
            new.text != _canonical(old.text) for new, old in zip(blocks, parsed.blocks, strict=True)
        ):
            raise ValueError("cleaning_structure_changed")
        cleaned = replace(parsed, text=text, blocks=blocks, metadata=dict(parsed.metadata))
    except Exception as exc:  # noqa: BLE001 — 清洗故障回退原始解析结果
        error_code = type(exc).__name__
        logger.warning("rag_cleaning_fallback version=%s error_code=%s", CLEANING_VERSION, error_code)
        cleaned = parsed
    characters = len(cleaned.text)
    replacements = cleaned.text.count("\ufffd")
    quality = max(0, 100 - round(100 * replacements / max(1, characters))) if cleaned.text.strip() else 0
    report: CleaningReport = {
        "version": CLEANING_VERSION,
        "original_hash": text_hash(parsed.text),
        "derived_hash": text_hash(cleaned.text),
        "fallback": error_code is not None,
        "error_code": error_code,
        "quality_score": quality,
        "character_count": characters,
        "replacement_count": replacements,
        "block_count": len(parsed.blocks),
        "extension": parsed.extension,
        "parser_name": str(parsed.metadata.get("parser_name") or "unknown"),
        "line_count": len(cleaned.text.splitlines()),
        "table_count": sum(block.type == "table" for block in parsed.blocks),
        "page_count": len({block.page for block in parsed.blocks if block.page is not None}),
        "has_layout": any(block.bbox is not None for block in parsed.blocks),
    }
    return CleaningResult(parsed, cleaned, report)
