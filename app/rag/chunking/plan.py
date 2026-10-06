"""Validate persisted plans before replay; missing plans alone use legacy routing."""

import json
from dataclasses import fields
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.rag.chunking.base import ChunkParams


class StoredParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    chunk_size: int = Field(default=500, ge=1)
    chunk_overlap: int = Field(default=64, ge=0)
    window_size: int | None = Field(default=None, ge=1)
    step: int | None = Field(default=None, ge=1)
    max_tokens: int | None = Field(default=None, ge=1)
    overlap_tokens: int | None = Field(default=None, ge=0)
    similarity_threshold: float | None = Field(default=None, ge=-1, le=1)
    parent_strategy: str | None = None
    parent_ratio: int = Field(default=3, ge=1)
    child_strategy: str | None = None
    child_params: dict[str, Any] | None = None


class StoredPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal["auto-routing-v0.1"]
    strategy: str
    routing_reason: str = Field(min_length=1)
    chunk_params: dict[str, Any]


def validate_params(values: dict[str, Any], *, complete: bool, depth: int = 0) -> None:
    from app.rag.chunking.factory import build_registry

    if depth > 8:
        raise ValueError("chunk_plan_invalid: child_params nesting")
    required = {field.name for field in fields(ChunkParams)} - {"input_policy"}
    if complete and set(values) != required:
        raise ValueError("chunk_plan_invalid: incomplete or unknown parameters")
    try:
        parsed = StoredParams.model_validate(values)
    except ValidationError as exc:
        raise ValueError("chunk_plan_invalid: parameter types or values") from exc
    names = build_registry().names()
    for name in (parsed.parent_strategy, parsed.child_strategy):
        if name is not None and name not in names:
            raise ValueError("chunk_plan_invalid: nested strategy")
    if parsed.child_params is not None:
        validate_params(parsed.child_params, complete=False, depth=depth + 1)


def load_plan(raw: str | None) -> dict[str, Any] | None:
    """NULL is legacy; a present invalid/unknown plan is rejected without rewriting."""
    if raw is None:
        return None
    try:
        parsed = StoredPlan.model_validate(json.loads(raw))
    except (ValueError, TypeError) as exc:
        raise ValueError("chunk_plan_invalid: format or unsupported version") from exc
    from app.rag.chunking.factory import build_registry

    if parsed.strategy not in build_registry().names():
        raise ValueError("chunk_plan_invalid: strategy")
    validate_params(parsed.chunk_params, complete=True)
    return parsed.model_dump()


def params_from_plan(plan: dict[str, Any], input_policy) -> ChunkParams:
    """Use stored effective values only, never mutable configuration defaults."""
    p = StoredParams.model_validate(plan["chunk_params"])
    return ChunkParams(
        chunk_size=p.chunk_size, chunk_overlap=p.chunk_overlap,
        window_size=p.window_size, step=p.step,
        max_tokens=p.max_tokens, overlap_tokens=p.overlap_tokens,
        similarity_threshold=p.similarity_threshold,
        parent_strategy=p.parent_strategy, parent_ratio=p.parent_ratio,
        child_strategy=p.child_strategy, child_params=p.child_params,
        input_policy=input_policy,
    )
