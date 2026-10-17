"""Observed regression CE05: completed steps must agree with execution metadata."""

import copy

import pytest

from app.agents.pipeline import AgentState, tool_fingerprint
from app.agents.state import StateManager


def completed_tool_snapshot():
    state = AgentState(user_input="synthetic")
    state.structured_plan = {"schema_version": 1, "steps": [
        {"id": "Read", "description": "Read a value", "kind": "tool", "depends_on": [],
         "expected_output": "A value", "tool": "calculator", "arguments": {"expression": "1+1"}},
        {"id": "Explain", "description": "Explain", "kind": "reason", "depends_on": ["Read"],
         "expected_output": "An explanation"},
    ]}
    state.plan_progress = [
        {"id": "Read", "status": "succeeded", "output": "2", "error_code": None},
        {"id": "Explain", "status": "pending", "output": "", "error_code": None},
    ]
    state.plan_cursor = 1
    state.tool_rounds_used = 1
    state.executed_tool_fingerprints = {tool_fingerprint("calculator", {"expression": "1+1"})}
    return StateManager(state).snapshot()


@pytest.mark.parametrize("mutation", [
    lambda data: data.update(tool_rounds_used=0, executed_tool_fingerprints=[]),
    lambda data: data.update(executed_tool_fingerprints=[]),
    lambda data: data.update(executed_tool_fingerprints=[tool_fingerprint("calculator", {"expression": "9+9"})]),
    lambda data: data.update(tool_rounds_used=0),
    lambda data: data.pop("tool_rounds_used"),
    lambda data: data.update(_round_no=0),
    lambda data: data.update(_round_no=2),
    lambda data: data.update(executed_tool_fingerprints=data["executed_tool_fingerprints"] * 2),
])
def test_contradictory_success_prefix_is_rejected_atomically(mutation):
    data = completed_tool_snapshot()
    mutation(data)
    target = AgentState(user_input="synthetic", plan="original")
    before = StateManager(target).snapshot()
    assert not StateManager.valid_snapshot(data), "successful tools need matching budget and fingerprints"
    assert not StateManager(target).apply_snapshot(data)
    assert StateManager(target).snapshot() == before


def test_matching_success_prefix_restores_with_database_metadata():
    data = completed_tool_snapshot()
    data["_round_no"] = 1
    before = copy.deepcopy(data)
    target = AgentState(user_input="synthetic")
    assert StateManager(target).apply_snapshot(data)
    assert target.plan_cursor == 1
    assert target.tool_rounds_used == 1
    assert target.executed_tool_fingerprints == set(data["executed_tool_fingerprints"])
    assert data == before


def test_reason_success_prefix_needs_no_tool_budget_or_fingerprint():
    data = completed_tool_snapshot()
    step = data["structured_plan"]["steps"][0]
    step.update(kind="reason")
    step.pop("tool")
    step.pop("arguments")
    data.update(tool_rounds_used=0, executed_tool_fingerprints=[], _round_no=0)
    assert StateManager.valid_snapshot(data)


def test_old_text_checkpoint_preserves_legacy_round_override():
    data = {"plan": "old numbered text", "tool_rounds_used": 0, "_round_no": 2,
            "executed_tool_fingerprints": []}
    target = AgentState(user_input="synthetic")
    assert StateManager(target).apply_snapshot(data)
    assert target.plan == "old numbered text"
    assert target.tool_rounds_used == 2


def test_duplicate_successful_tool_steps_cannot_claim_two_successes():
    data = completed_tool_snapshot()
    step = dict(data["structured_plan"]["steps"][0], id="ReadAgain", depends_on=["Read"])
    data["structured_plan"]["steps"][1] = step
    data["plan_progress"][1].update(id="ReadAgain", status="succeeded", output="2")
    data.update(plan_cursor=2, tool_rounds_used=2)
    assert not StateManager.valid_snapshot(data)
