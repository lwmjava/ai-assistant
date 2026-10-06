"""Recomputable source-range metrics; metadata is validated against actual text."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evals.chunk_structure_integrity import VERSION


def adapt_chunks(source: str, chunks: list[Any]) -> tuple[list[dict[str, Any]], int]:
    """Adapt Chunk metadata; legacy exact matches are explicitly inferred.

    Inference is a baseline diagnostic, not proof of persisted provenance.
    A failed match leaves no source span and therefore reduces measured coverage.
    """
    records: list[dict[str, Any]] = []
    inferred = 0
    cursor = 0
    for chunk in chunks:
        metadata = chunk.metadata
        prefix = metadata.get("derived_prefix", "")
        prefix_size = len(prefix) if isinstance(prefix, str) else 0
        original = chunk.text[prefix_size:]
        if "source_start" in metadata and "source_end" in metadata:
            start, end = metadata["source_start"], metadata["source_end"]
        else:
            inferred += 1
            start = source.find(original, cursor) if original else -1
            end = start + len(original)
            if start >= 0:
                cursor = start + 1
        records.append({
            "text": chunk.text,
            "oversized": bool(metadata.get("oversized", False)),
            "source_spans": (
                [{"start": start, "end": end, "chunk_start": prefix_size,
                  "chunk_end": len(chunk.text)}] if start >= 0 else []
            ),
            "derived_spans": (
                [{"chunk_start": 0, "chunk_end": prefix_size,
                  "kind": "repeated_header"}] if prefix_size else []
            ),
        })
    return records, inferred


def load_cases() -> list[dict[str, Any]]:
    payload = json.loads(Path(__file__).with_name("cases.json").read_text("utf-8"))
    if payload["version"] != VERSION:
        raise ValueError("Dataset/evaluator version mismatch")
    cases = payload["cases"]
    for case in cases:
        for unit in case["protected"]:
            cursor = 0
            for _ in range(unit.get("occurrence", 0) + 1):
                start = case["text"].find(unit["text"], cursor)
                if start < 0:
                    raise ValueError(f"Missing protected source in {case['id']}")
                cursor = start + len(unit["text"])
            unit["start"] = start
            unit["end"] = cursor
    return cases


def evaluate_chunks(
    source: str,
    chunks: list[dict[str, Any]],
    protected: list[dict[str, Any]],
) -> dict[str, Any]:
    """Each source span maps source[start:end] to text[chunk_start:chunk_end].

    Derived context has separate spans and never contributes original coverage.
    Parent and child layers must be evaluated separately by the caller.
    """
    coverage = bytearray(len(source))
    starts: list[int] = []
    invalid_spans = 0
    faithful_spans: list[tuple[int, int, int]] = []
    derived_chars = 0
    lengths = []
    oversized = 0
    for index, chunk in enumerate(chunks):
        text = chunk["text"]
        lengths.append(len(text))
        oversized += bool(chunk.get("oversized", False))
        for derived in chunk.get("derived_spans", []):
            a, b = derived["chunk_start"], derived["chunk_end"]
            if not 0 <= a <= b <= len(text):
                invalid_spans += 1
            else:
                derived_chars += b - a
        for span in chunk.get("source_spans", []):
            a, b = span["start"], span["end"]
            x, y = span["chunk_start"], span["chunk_end"]
            if (
                not 0 <= a < b <= len(source)
                or not 0 <= x < y <= len(text)
                or source[a:b] != text[x:y]
                or any(
                    max(x, d["chunk_start"]) < min(y, d["chunk_end"])
                    for d in chunk.get("derived_spans", [])
                )
            ):
                invalid_spans += 1
                continue
            starts.append(a)
            faithful_spans.append((index, a, b))
            coverage[a:b] = b"\x01" * (b - a)
    broken = sum(
        not any(a <= unit["start"] and b >= unit["end"] for _, a, b in faithful_spans)
        for unit in protected
    )
    covered = sum(coverage)
    ordered = sorted(lengths)
    return {
        "version": VERSION,
        "source_chars": len(source),
        "covered_chars": covered,
        "missing_chars": len(source) - covered,
        "coverage": covered / len(source) if source else 1.0,
        "out_of_order": sum(b < a for a, b in zip(starts, starts[1:])),
        "invalid_spans": invalid_spans,
        "protected_units": len(protected),
        "broken_units": broken,
        "structure_breakage_rate": broken / len(protected) if protected else 0.0,
        "derived_context_chars": derived_chars,
        "oversized_chunks": oversized,
        "length_distribution": {
            "count": len(ordered),
            "min": ordered[0] if ordered else 0,
            "max": ordered[-1] if ordered else 0,
            "median": (
                (ordered[(len(ordered) - 1) // 2] + ordered[len(ordered) // 2]) / 2
                if ordered else 0
            ),
            "sorted_chars": ordered,
        },
    }
