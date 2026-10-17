"""Strict, bounded sequential plan and checkpoint progress validation."""

from __future__ import annotations

import json
import math
import re
from typing import Any

MAX_PLAN_BYTES = 64 * 1024
MAX_STEPS = 16
MAX_ARGUMENT_DEPTH = 8
_IDENTIFIER = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{0,63}\Z")
_STEP_FIELDS = {"id", "description", "kind", "depends_on", "expected_output"}
_PROGRESS_FIELDS = {"id", "status", "output", "error_code"}


class PlanValidationError(ValueError):
    """Safe validation error; never embeds untrusted plan contents."""

    def __init__(self, code: str, step_id: str | None = None) -> None:
        self.code = code
        self.step_id = step_id
        super().__init__(code)


def _identifier(value: object) -> bool:
    return isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None


def _json_value(value: object, depth: int = 0) -> bool:
    if depth > MAX_ARGUMENT_DEPTH:
        return False
    if value is None or isinstance(value, str | bool):
        return True
    if type(value) is int:
        return abs(value) <= 10 ** 100
    if type(value) is float:
        return math.isfinite(value) and abs(value) <= 1e100
    if isinstance(value, list):
        return all(_json_value(item, depth + 1) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _json_value(item, depth + 1)
                   for key, item in value.items())
    return False


def _bounded_json(value: object) -> bool:
    try:
        return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")) <= MAX_PLAN_BYTES
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError):
        return False


def _check_plan(data: object) -> None:
    if not isinstance(data, dict) or set(data) != {"schema_version", "steps"}:
        raise PlanValidationError("invalid_plan_schema")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise PlanValidationError("unsupported_plan_version")
    steps = data["steps"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise PlanValidationError("invalid_step_count")
    if not _bounded_json(data):
        raise PlanValidationError("plan_too_large")
    seen: set[str] = set()
    for step in steps:
        step_id = step.get("id") if isinstance(step, dict) else None
        safe_id = step_id if _identifier(step_id) else None
        if not isinstance(step, dict) or not isinstance(step_id, str) or not _identifier(step_id):
            raise PlanValidationError("invalid_step_schema", safe_id)
        if step_id in seen:
            raise PlanValidationError("duplicate_step_id", safe_id)
        kind = step.get("kind")
        fields = _STEP_FIELDS | {"tool", "arguments"} if kind == "tool" else _STEP_FIELDS
        if kind not in {"tool", "reason"} or set(step) != fields:
            raise PlanValidationError("invalid_step_schema", safe_id)
        for name in ("description", "expected_output"):
            value = step[name]
            if not isinstance(value, str) or not value.strip() or len(value) > 1000:
                raise PlanValidationError("invalid_step_text", safe_id)
        dependencies = step["depends_on"]
        if (not isinstance(dependencies, list)
                or not all(isinstance(dep, str) and dep in seen for dep in dependencies)
                or len(set(dependencies)) != len(dependencies)):
            raise PlanValidationError("invalid_step_dependency", safe_id)
        if kind == "tool":
            arguments = step["arguments"]
            if (not _identifier(step["tool"]) or not isinstance(arguments, dict)
                    or not _json_value(arguments) or not _bounded_json(arguments)):
                raise PlanValidationError("invalid_tool_arguments", safe_id)
        seen.add(step_id)


def validate_plan(data: object) -> bool:
    """Return False for malformed plans, without mutating caller-owned data."""
    try:
        _check_plan(data)
        return True
    except (PlanValidationError, TypeError, ValueError, RecursionError, UnicodeError):
        return False


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PlanValidationError("duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise PlanValidationError("invalid_json_number")


def parse_plan(text: str) -> dict[str, Any]:
    """Parse an entire JSON object or an entire json fence, never mixed prose."""
    if not isinstance(text, str):
        raise PlanValidationError("invalid_plan_json")
    try:
        if len(text.encode("utf-8")) > MAX_PLAN_BYTES:
            raise PlanValidationError("plan_too_large")
        body = text.strip()
        if body.startswith("```json\n") and body.endswith("\n```"):
            body = body[8:-4]
        data = json.loads(body, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        _check_plan(data)
    except PlanValidationError:
        raise
    except (ValueError, TypeError, OverflowError, RecursionError, UnicodeError):
        raise PlanValidationError("invalid_plan_json") from None
    return data


def validate_progress(plan: object, progress: object, cursor: object) -> bool:
    """Accept only a successful prefix followed by one active and pending steps."""
    if not validate_plan(plan) or not isinstance(plan, dict):
        return False
    steps = plan["steps"]
    if (type(cursor) is not int or not 0 <= cursor <= len(steps)
            or not isinstance(progress, list) or len(progress) != len(steps)):
        return False
    for index, (step, item) in enumerate(zip(steps, progress, strict=True)):
        if (not isinstance(item, dict) or set(item) != _PROGRESS_FIELDS
                or item["id"] != step["id"]):
            return False
        status, output, error = item["status"], item["output"], item["error_code"]
        if (not isinstance(status, str) or status not in {"pending", "running", "succeeded", "failed"}
                or not isinstance(output, str) or len(output) > 8000
                or (error not in (None, "") and not _identifier(error))):
            return False
        if index < cursor and status != "succeeded":
            return False
        if index > cursor and status != "pending":
            return False
        if index == cursor and status == "succeeded":
            return False
        if status == "failed":
            if not _identifier(error):
                return False
        elif error not in (None, ""):
            return False
        if status in {"pending", "running"} and output:
            return False
    return True
