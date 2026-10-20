"""Synthetic observed protocol regressions; no model quality claim."""

import pytest

from app.agents.tools.base import inspect_tool_call


def envelope(parameters, name="calculator", marker="｜｜DSML｜｜"):
    return f'<{marker} calls><{marker} invoke name="{name}">{parameters}</{marker} invoke></{marker} calls>'


def parameter(name="expression", value="2+3", kind="true", marker="｜｜DSML｜｜"):
    return f'<{marker} parameter name="{name}" string="{kind}">{value}</{marker} parameter>'


@pytest.mark.parametrize("marker", ["｜｜DSML｜｜", "｜DSML｜"])
def test_single_call_preserves_string(marker):
    raw = envelope(parameter(value='print("a < b & c")', marker=marker), name="code_sandbox", marker=marker)
    call = inspect_tool_call(raw)
    assert call.status == "ok"
    assert call.call.name == "code_sandbox"
    assert call.call.arguments == {"expression": 'print("a < b & c")'}


def test_json_parameters():
    call = inspect_tool_call(envelope(parameter("items", '[1, true, null]', "false")))
    assert call.status == "ok"
    assert call.call.arguments == {"items": [1, True, None]}


@pytest.mark.parametrize("raw", [
    envelope(parameter()) * 2,
    "prefix" + envelope(parameter()),
    "```" + envelope(parameter()) + "```",
    envelope(parameter() + parameter()),
    envelope(parameter(kind="maybe")),
    envelope(parameter(value="NaN", kind="false")),
    envelope(parameter(value="1e999", kind="false")),
    envelope(parameter(value='{"x":1,"x":2}', kind="false")),
    envelope(parameter()).replace('name="calculator"', 'name="calculator" extra="x"'),
    envelope(parameter()) + '<tool_call>{"name":"calculator"}</tool_call>',
    envelope(parameter())[:-8],
    envelope(parameter(value="x" * 131073)),
    envelope(parameter(value=envelope(parameter()))),
    envelope(parameter(value="\ud800")),
], ids=lambda raw: str(len(raw)) + "-bytes")
def test_ambiguous_or_invalid_dsml_is_rejected(raw):
    intent = inspect_tool_call(raw)
    assert intent.status == "invalid"
    assert intent.call is None
