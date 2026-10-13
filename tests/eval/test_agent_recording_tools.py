"""Recording validates calls without ever executing model-generated code."""
import asyncio
import copy
import json

import pytest

from app.agents import pipeline
from app.agents.tools import builtin
from scripts import agent_gate_check as gate
from scripts import run_agent_chain_eval as runner


def case(number="agent-003"):
    return next(row for row in runner.load_cases() if row["case_id"] == number)


def call(arguments, name="code_sandbox"):
    return '<tool_call>' + json.dumps({"name": name, "arguments": arguments}) + '</tool_call>'


@pytest.mark.parametrize("arguments,error", [({}, "invalid_parameters"), ({"code": " "}, "invalid_parameters"),
    ({"code": 5}, "invalid_parameters"), ({"code": "print("}, "invalid_code"),
    ({"code": "# no executable statements"}, "invalid_code"),
    ({"code": "print(1)", "timeout": True}, "invalid_parameters"),
    ({"code": "print(1)", "timeout": -1}, "invalid_parameters")])
def test_invalid_parameters_never_reach_recording_fake(arguments, error):
    record = asyncio.run(runner.record_tool_call(case(), call(arguments)))
    assert record["error_code"] == error
    assert record["recording_attempts"] == record["recorded_calls"] == 0
    assert record["arguments_valid"] is False


@pytest.mark.parametrize("name", ["web_fetch", "mcp__refund", "unknown"])
def test_forbidden_tools_never_record(name):
    record = asyncio.run(runner.record_tool_call(case(), call({"code": "print(1)"}, name)))
    assert record["error_code"] == "tool_not_allowed"
    assert record["recorded_calls"] == 0


def test_valid_call_records_once_without_real_tool(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("real tool must never run")

    for name in ("code_sandbox", "calculator", "get_current_datetime", "web_fetch"):
        monkeypatch.setattr(builtin, name, fail)
    record = asyncio.run(runner.record_tool_call(case(), call({"code": "print(1)"})))
    assert record["arguments_valid"] is True
    assert record["recording_attempts"] == record["recorded_calls"] == 1
    assert record["recording_result"] == "recorded"
    assert "code" not in record and "stdout" not in record


def test_confirmed_prior_code_cannot_be_replaced():
    row = case("agent-004")
    wrong = asyncio.run(runner.record_tool_call(row, call({"code": "print(1)"})))
    assert wrong["error_code"] == "prior_code_mismatch"
    assert wrong["recorded_calls"] == 0
    code = row["history"][1]["content"].split("```python\n", 1)[1].split("```", 1)[0]
    valid = asyncio.run(runner.record_tool_call(row, call({"code": "# format is immaterial\n" + code})))
    assert valid["recorded_calls"] == 1


def test_prompt_contains_production_schemas_and_uses_runtime_builder(monkeypatch):
    row = case()
    prompt = pipeline.build_tool_choice_messages(row["history"], row["user_input"])[-1].content
    assert '"required": ["code"]' in prompt
    assert "四层安全防护" in prompt
    assert "## 回答计划" in prompt
    assert all(token not in prompt for token in ("expected_tools", "critical_followup", "web_fetch"))
    runtime = pipeline.AgentPipeline(runner.SmokeIntentModel(), tools=pipeline.selection_tool_registry())
    runtime._execution_policy = pipeline._SHORT_PATH_POLICY
    state = pipeline.AgentState(row["user_input"], history=pipeline._as_history(row["history"]))
    assert runtime._build_act(state) == prompt
    before = runner.prompt_hash()
    original = builtin.default_tools

    def changed():
        tools = original()
        next(t for t in tools if t.name == "code_sandbox").description += " changed contract"
        return tools

    monkeypatch.setattr(builtin, "default_tools", changed)
    assert runner.prompt_hash() != before


def test_empty_parameters_fail_new_metric_even_with_correct_names():
    class EmptyArguments:
        async def chat(self, messages, options=None):
            return call({})

    rows = runner.load_cases()
    chosen, raws, records = asyncio.run(runner.predict_tools(EmptyArguments(), rows))
    assert runner.score_tools(rows, chosen)["tool_selection_recall"] == 1.0
    assert runner.score_valid_calls(rows, records) == 0.0
    assert sum(record["recorded_calls"] for record in records) == 0
    assert len(raws) == 10


def test_gate_independently_rejects_validity_summary_forgery(monkeypatch):
    rows = runner.load_cases()
    chosen, raws, records = asyncio.run(runner.predict_tools(runner.SmokeIntentModel(), rows))
    provider = runner.OpenAICompatibleProvider("https://isolated.invalid/v1", "synthetic-only", "test-model")
    report = runner.build_report("official", provider, rows, [row["expected_route"] for row in rows],
                                 ["YES" if row["expected_route"] == "full" else "NO" for row in rows],
                                 chosen, raws, records)
    endpoint = runner.endpoint_hash(provider.base_url)
    assert gate.check_report(report, provider.model, endpoint) == []
    bad = copy.deepcopy(report)
    bad["cases"][2]["tool_reply"] = call({})
    bad["cases"][2]["tool_record"] = runner.expected_tool_record(rows[2], call({}))
    monkeypatch.setattr(runner, "score_valid_calls", lambda *args: 1.0)
    with pytest.raises(ValueError, match="metric_integrity"):
        gate.check_report(bad, provider.model, endpoint)


@pytest.mark.parametrize("field,value", [("recorded_calls", True), ("recording_attempts", 2),
                                       ("arguments_hash", "a" * 64), ("arguments_valid", 1)])
def test_gate_rejects_forged_recording_details(field, value):
    rows = runner.load_cases()
    chosen, raws, records = asyncio.run(runner.predict_tools(runner.SmokeIntentModel(), rows))
    provider = runner.OpenAICompatibleProvider("https://isolated.invalid/v1", "synthetic-only", "test-model")
    report = runner.build_report("official", provider, rows, [row["expected_route"] for row in rows],
                                 ["YES" if row["expected_route"] == "full" else "NO" for row in rows],
                                 chosen, raws, records)
    report["cases"][2]["tool_record"][field] = value
    with pytest.raises(ValueError, match="record_binding"):
        gate.check_report(report, provider.model, runner.endpoint_hash(provider.base_url))


@pytest.mark.parametrize("raw", ["", " ", '<tool_call>{"name":"code_sandbox","arguments":{"code":"x=1",'
                                '"code":"print(1)"}}</tool_call>',
                                '<tool_call>{"name":"code_sandbox","arguments":{"code":"print(1)",'
                                '"timeout":NaN}}</tool_call>'])
def test_empty_ambiguous_or_nonfinite_call_cannot_record(raw):
    record = asyncio.run(runner.record_tool_call(case(), raw))
    assert record["recorded_calls"] == 0
    assert record["arguments_valid"] is False


@pytest.mark.parametrize("tool,args", [("calculator", {"expression": "1+1"}),
                                      ("get_current_datetime", {"format": "%Y"})])
def test_other_whitelisted_tools_are_recorded_only(tool, args, monkeypatch):
    def fail(*args):
        raise AssertionError("real tool executed")

    monkeypatch.setattr(builtin, tool, fail)
    record = asyncio.run(runner.record_tool_call(case(), call(args, tool)))
    assert record["recorded_calls"] == 1
    assert record["arguments_valid"] is True


@pytest.mark.parametrize("tool,args", [("calculator", {}), ("calculator", {"expression": False}),
                                      ("get_current_datetime", {"format": 3})])
def test_other_tools_production_required_fields_and_types(tool, args):
    record = asyncio.run(runner.record_tool_call(case(), call(args, tool)))
    assert record["error_code"] == "invalid_parameters"
    assert record["recorded_calls"] == 0


def test_official_empty_arguments_fail_after_exactly_twelve_isolated_requests(monkeypatch, tmp_path):
    provider = runner.OpenAICompatibleProvider("https://isolated.invalid/v1", "synthetic-only", "test-model")
    requests = []

    async def reply(self, messages, options):
        requests.append(messages)
        if "## 可用外部工具" in messages[-1].content:
            return call({})
        return await runner.SmokeIntentModel().chat(messages, options)

    monkeypatch.setattr(runner.OpenAICompatibleProvider, "chat", reply)
    monkeypatch.setattr(runner.OpenAICompatibleProvider, "_effective_capability", lambda self: object())
    monkeypatch.setattr(runner, "get_llm_provider", lambda task: provider)
    monkeypatch.setattr(runner.settings, "LLM_CAPABILITY_GUARD_ENABLED", True)
    monkeypatch.setattr(runner.settings, "LLM_RESPONSE_CACHE_ENABLED", False)
    target = tmp_path / "isolated-official.json"
    assert asyncio.run(runner.run("official", tmp_path, target)) == 1
    report = json.loads(target.read_text(encoding="utf-8"))
    assert len(requests) == report["model_requests"] == 12
    assert report["tool_selection_recall"] == 1.0
    assert report["tool_call_validity_recall"] == 0.0
    assert gate.check_report(report, provider.model, runner.endpoint_hash(provider.base_url)) == [
        "tool_call_validity_recall"]


@pytest.mark.parametrize("field,old", [("report_schema_version", "agent-chain-report-1.0.0"),
                                    ("gate_version", "agent-chain-gate-1.0.0"),
                                    ("evaluator_version", "agent-chain-eval-0.2.0")])
def test_old_protocol_is_not_current_evidence(field, old):
    rows = runner.load_cases()
    names, raws, records = asyncio.run(runner.predict_tools(runner.SmokeIntentModel(), rows))
    provider = runner.OpenAICompatibleProvider("https://isolated.invalid/v1", "synthetic-only", "test-model")
    report = runner.build_report("official", provider, rows, [row["expected_route"] for row in rows],
                                 ["YES" if row["expected_route"] == "full" else "NO" for row in rows],
                                 names, raws, records)
    report[field] = old
    with pytest.raises(ValueError):
        gate.check_report(report, provider.model, runner.endpoint_hash(provider.base_url))
