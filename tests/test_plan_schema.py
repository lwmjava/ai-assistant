"""Adversarial Plan v1 schema cases; pure unit evidence, no model quality claim."""

from __future__ import annotations

import copy
import json

import pytest

from app.agents.plan import (
    MAX_PLAN_BYTES,
    PlanValidationError,
    parse_plan,
    validate_plan,
    validate_progress,
)


def plan():
    return {"schema_version": 1, "steps": [
        {"id": "Read", "description": "Read current time", "kind": "tool",
         "depends_on": [], "expected_output": "A time value",
         "tool": "datetime", "arguments": {"values": [None, True, 1, 1.25]}},
        {"id": "Explain_2", "description": "Explain the result", "kind": "reason",
         "depends_on": ["Read"], "expected_output": "An explanation"},
    ]}


def progress():
    return [{"id": step["id"], "status": "pending", "output": "", "error_code": None}
            for step in plan()["steps"]]


def test_valid_plan_roundtrip_and_whole_fence():
    data = plan()
    raw = json.dumps(data)
    assert validate_plan(data)
    assert parse_plan(raw) == data
    assert parse_plan("```json\n" + raw + "\n```") == data
    assert data == plan(), "validation must not mutate input"


@pytest.mark.parametrize("version", [True, False, 0, 2, "1", 1.0, None])
def test_version_requires_exact_int_one(version):
    data = plan()
    data["schema_version"] = version
    assert not validate_plan(data)
    with pytest.raises(PlanValidationError):
        parse_plan(json.dumps(data))


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(extra=1),
    lambda p: p.pop("schema_version"),
    lambda p: p.update(steps=[]),
    lambda p: p.update(steps={}),
    lambda p: p["steps"][0].update(id="1bad"),
    lambda p: p["steps"][0].update(id="a" * 65),
    lambda p: p["steps"][0].update(id="x\n"),
    lambda p: p["steps"][1].update(id="Read"),
    lambda p: p["steps"][0].update(description=" "),
    lambda p: p["steps"][0].update(expected_output="a" * 1001),
    lambda p: p["steps"][0].update(kind="parallel"),
    lambda p: p["steps"][0].update(kind=[]),
    lambda p: p["steps"][0].update(depends_on=["Read"]),
    lambda p: p["steps"][0].update(depends_on=["Explain_2"]),
    lambda p: p["steps"][1].update(depends_on=["unknown"]),
    lambda p: p["steps"][1].update(depends_on=["Read", "Read"]),
    lambda p: p["steps"][1].update(depends_on=[{}]),
    lambda p: p["steps"][1].update(depends_on="Read"),
    lambda p: p["steps"][1].update(tool="datetime"),
    lambda p: p["steps"][1].update(arguments={}),
    lambda p: p["steps"][0].pop("arguments"),
    lambda p: p["steps"][0].update(tool="bad.tool"),
    lambda p: p["steps"][0].update(arguments=[]),
    lambda p: p["steps"][0].update(arguments={"x": float("nan")}),
    lambda p: p["steps"][0].update(arguments={"x": float("inf")}),
    lambda p: p["steps"][0].update(arguments={"x": 1.01e100}),
    lambda p: p["steps"][0].update(arguments={"x": -(10 ** 101)}),
    lambda p: p["steps"][0].update(arguments={1: "not a JSON key"}),
    lambda p: p["steps"][0].update(arguments={"x": (1, 2)}),
    lambda p: p["steps"].append(None),
])
def test_reject_invalid_plan_without_partial_mutation(mutation):
    data = plan()
    mutation(data)
    before = copy.deepcopy(data)
    assert not validate_plan(data)
    # NaN does not equal itself, so compare its stable repr, not value equality.
    assert repr(data) == repr(before)


def test_step_count_and_text_boundaries():
    data = plan()
    template = data["steps"][1]
    data["steps"] = [dict(template, id=f"S{i}", depends_on=[]) for i in range(16)]
    data["steps"][0]["description"] = "a" * 1000
    assert validate_plan(data)
    data["steps"].append(dict(template, id="S16", depends_on=[]))
    assert not validate_plan(data)


def test_numeric_and_depth_bounds():
    data = plan()
    data["steps"][0]["arguments"] = {"x": 10 ** 100, "y": -1e100}
    assert validate_plan(data)
    nested = {}
    data["steps"][0]["arguments"] = nested
    for _ in range(8):
        nested["child"] = {}
        nested = nested["child"]
    assert validate_plan(data)
    nested["child"] = {}
    assert not validate_plan(data)


def test_bounded_bytes_unicode_and_cyclic_arguments():
    data = plan()
    data["steps"][0]["arguments"] = {"payload": "界" * 22000}
    assert not validate_plan(data)
    raw = json.dumps(data, ensure_ascii=False)
    assert len(raw.encode("utf-8")) > MAX_PLAN_BYTES
    with pytest.raises(PlanValidationError, match="plan_too_large"):
        parse_plan(raw)
    cyclic = {}
    cyclic["self"] = cyclic
    data["steps"][0]["arguments"] = cyclic
    assert not validate_plan(data)


@pytest.mark.parametrize("raw", [
    "", "[]", "null", "true", "{}", "Here is a plan: {}", "{} trailing",
    "```json\n{}\n```\nadditional text", "```\n{}\n```",
    '{"schema_version":1,"schema_version":1,"steps":[]}',
    '{"schema_version":NaN,"steps":[]}',
    '{"schema_version":Infinity,"steps":[]}',
    '{"schema_version":1e999,"steps":[]}',
])
def test_parser_rejects_mixed_text_numbers_and_duplicate_keys(raw):
    with pytest.raises(PlanValidationError) as error:
        parse_plan(raw)
    assert "Here is" not in str(error.value)
    assert error.value.step_id is None


def test_parser_duplicate_argument_key_and_safe_error_step():
    raw = json.dumps(plan()).replace('"values":', '"values": 0, "values":')
    with pytest.raises(PlanValidationError, match="duplicate_json_key"):
        parse_plan(raw)
    data = plan()
    data["steps"][1]["depends_on"] = ["private untrusted payload"]
    with pytest.raises(PlanValidationError) as error:
        parse_plan(json.dumps(data))
    assert error.value.step_id == "Explain_2"
    assert error.value.code == "invalid_step_dependency"
    assert "private" not in str(error.value)


def test_progress_success_prefix_active_failure_and_complete():
    items = progress()
    assert validate_progress(plan(), items, 0)
    items[0]["status"] = "running"
    assert validate_progress(plan(), items, 0)
    items[0].update(status="succeeded", output="Current time")
    assert validate_progress(plan(), items, 1)
    items[1].update(status="failed", error_code="tool_timeout", output="Safe failure")
    assert validate_progress(plan(), items, 1)
    items[1].update(status="succeeded", error_code=None, output="An explanation")
    assert validate_progress(plan(), items, 2)


@pytest.mark.parametrize("cursor", [True, False, -1, 3, 0.0, "0", None])
def test_invalid_progress_cursor(cursor):
    assert not validate_progress(plan(), progress(), cursor)


@pytest.mark.parametrize("mutation", [
    lambda p: p.pop(),
    lambda p: p[0].update(id="other"),
    lambda p: p[0].update(extra=True),
    lambda p: p[0].pop("output"),
    lambda p: p[0].update(status="unknown"),
    lambda p: p[0].update(status=[]),
    lambda p: p[0].update(output="a" * 8001),
    lambda p: p[0].update(output={}),
    lambda p: p[0].update(output="stale output"),
    lambda p: p[0].update(status="failed"),
    lambda p: p[0].update(status="failed", error_code="secret\npayload"),
    lambda p: p[0].update(error_code="tool_error"),
    lambda p: p[0].update(error_code=[]),
    lambda p: p[0].update(status="succeeded"),
    lambda p: p[1].update(status="running"),
    lambda p: p[1].update(status="succeeded"),
])
def test_reject_corrupt_progress_without_mutation(mutation):
    items = progress()
    mutation(items)
    before = copy.deepcopy(items)
    assert not validate_progress(plan(), items, 0)
    assert items == before


def test_failure_cannot_have_successful_successor_or_cursor_beyond_it():
    items = progress()
    items[0].update(status="failed", error_code="tool_error")
    items[1].update(status="succeeded", output="Forged success")
    assert not validate_progress(plan(), items, 0)
    assert not validate_progress(plan(), items, 1)
    assert not validate_progress(plan(), items, 2)


def test_success_output_limit_and_no_error():
    items = progress()
    items[0].update(status="succeeded", output="a" * 8000)
    assert validate_progress(plan(), items, 1)
    items[0]["error_code"] = "tool_error"
    assert not validate_progress(plan(), items, 1)
