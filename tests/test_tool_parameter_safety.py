"""Untrusted tool arguments are bounded before either execution entrance."""

import asyncio
import math

import pytest

from app.agents.tools.base import Tool, ToolCall, ToolRegistry, inspect_tool_call
from app.agents.tools.contract import RiskLevel, ToolContract, ToolPermission
from app.agents.tools.executor import ToolExecutor


@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("case", ["unknown", "deep", "large", "nan", "nested_unknown"])
async def test_unsafe_arguments_never_execute_or_leak(bound, case):
    calls, audits = [], []
    schema = {"type": "object", "properties": {"payload": {"type": "object", "additionalProperties": True}}}
    if case == "unknown":
        arguments = {"undeclared": "sensitive-value"}
    elif case == "large":
        arguments = {"payload": {"value": "x" * 131073}}
    elif case == "nan":
        arguments = {"payload": {"value": math.nan}}
    elif case == "nested_unknown":
        schema["properties"]["payload"] = {"type": "object", "properties": {"value": {"type": "string"}}}
        arguments = {"payload": {"secret_extra": "sensitive-value"}}  # pragma: allowlist secret
    else:
        arguments = {"payload": {}}
        target = arguments["payload"]
        for _ in range(20):
            target["child"] = {}
            target = target["child"]

    async def execute(arguments):
        calls.append(arguments)
        return "executed"

    registry = ToolRegistry([Tool("probe", "isolated", schema, execute, ToolContract(
        version="1", permission=ToolPermission(), risk=RiskLevel.L0,
        side_effect="none", timeout_seconds=1,
    ))])

    async def audit(*args):
        audits.append(args)

    registry._record_audit = audit
    executor = ToolExecutor(registry)
    if bound:
        registry.bind_executor(executor)
    output = await registry.run(ToolCall("probe", arguments))
    assert output.status == "invalid"
    assert calls == []
    assert len(audits) == 1 and audits[0][4] is False
    assert "sensitive-value" not in output and "sensitive-value" not in str(audits)


def test_deep_model_envelope_is_invalid_instead_of_raising():
    body = '<tool_call>{"name":"probe","arguments":{"payload":' + '[' * 1100 + '0' + ']' * 1100 + '}}</tool_call>'
    assert inspect_tool_call(body).status == "invalid"


def test_large_model_envelope_is_invalid():
    body = '<tool_call>{"name":"probe","arguments":{"payload":"' + 'x' * 131073 + '"}}</tool_call>'
    assert inspect_tool_call(body).status == "invalid"


def test_existing_calculator_type_compatibility_remains():
    from app.agents.tools.builtin import default_tools

    registry = ToolRegistry(default_tools())
    result = asyncio.run(ToolExecutor(registry).execute(ToolCall("calculator", {"expression": 123})))
    assert result.status == "ok" and "123" in result.observation
    assert result.audit_metadata["passed"] is False


@pytest.mark.parametrize("schema,arguments", [
    ({"allOf": [{"properties": {"a": {"type": "string"}}},
                 {"properties": {"b": {"type": "integer"}}}]}, {"a": "x", "b": 2}),
    ({"$defs": {"input": {"properties": {"a": {"type": "string"}}}},
      "$ref": "#/$defs/input"}, {"a": "x"}),
    ({"properties": {"common": {"type": "string"}}, "anyOf": [
        {"properties": {"a": {"type": "string"}}, "required": ["a"]},
        {"properties": {"b": {"type": "string"}}, "required": ["b"]}]}, {"common": "c", "a": "x"}),
    ({"properties": {"free": {"type": "object", "additionalProperties": True}}},
     {"free": {"arbitrary": [1, "x"]}}),
])
def test_declared_composed_and_freeform_parameters_remain_usable(schema, arguments):
    from app.agents.tools.parameter_policy import prepare_arguments

    tool = Tool("probe", "", schema, lambda args: "ok")
    snapshot, validation = prepare_arguments(tool, arguments)
    assert validation["passed"] and not validation["security_errors"]
    assert snapshot == arguments


def test_composed_schema_still_rejects_undeclared_fields():
    from app.agents.tools.parameter_policy import prepare_arguments

    schema = {"allOf": [{"properties": {"a": {"type": "string"}}},
                        {"properties": {"b": {"type": "integer"}}}]}
    _, validation = prepare_arguments(Tool("probe", "", schema, lambda args: "ok"),
                                      {"a": "x", "b": 2, "undeclared": True})
    assert validation["security_errors"]


@pytest.mark.parametrize("case", ["cycle", "nodes", "non_json", "nested_type", "range", "enum_bool"])
def test_additional_structural_boundaries(case):
    from app.agents.tools.parameter_policy import prepare_arguments

    schema = {"properties": {"payload": {"type": "object", "additionalProperties": True}}}
    value = {"payload": {}}
    if case == "cycle":
        value["payload"]["back"] = value
    elif case == "nodes":
        value["payload"]["items"] = [0] * 4097
    elif case == "non_json":
        value["payload"]["invalid"] = object()
    else:
        field = {"type": "string"} if case == "nested_type" else {"maximum": 2} if case == "range" else {"enum": [1]}
        schema["properties"]["payload"] = {"properties": {"field": field}}
        value["payload"]["field"] = 3 if case != "enum_bool" else True
    _, validation = prepare_arguments(Tool("probe", "", schema, lambda args: "ok"), value)
    assert validation["security_errors"]


async def test_resource_guard_is_inside_timeout_and_cancellable():
    calls = []

    async def guard(arguments):
        await asyncio.sleep(10)

    tool = Tool("probe", "", {}, lambda args: calls.append(True) or "ok",
                ToolContract(version="1", permission=ToolPermission(), risk=RiskLevel.L0,
                             side_effect="none", timeout_seconds=0.01), argument_guard=guard)
    registry = ToolRegistry([tool])
    result = await ToolExecutor(registry).execute(ToolCall("probe", {}))
    assert result.status == "timeout" and not calls
    task = asyncio.create_task(ToolExecutor(registry).execute(ToolCall("probe", {})))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not calls
