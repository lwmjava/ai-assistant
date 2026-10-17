"""Independent isolated entry probes; synthetic, no model/network/DB calls."""

import asyncio
import copy
import hashlib
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.agents.pipeline import AgentPipeline, AgentState
from app.agents.prompts import SYSTEM_ACT, SYSTEM_PREFLOW
from app.agents.tools.base import Tool, ToolRegistry
from app.agents.tools.builtin import publish_code_result
from app.llm.base import ChatMessage, ChatRole, LLMProvider
from scripts import agent_gate_check as gate
from scripts import agent_gate_probe as probe
from scripts import run_agent_chain_eval as ev

logging.disable(logging.CRITICAL)
results = []


def checked(name, condition):
    assert condition, name
    results.append(name)


class Local(LLMProvider):
    model = "synthetic-isolated"

    def __init__(self, route, call):
        self.route, self.call, self.calls, self.actions = route, call, 0, 0
        self.messages = []

    async def chat(self, messages, options=None):
        self.calls += 1
        self.messages.append(messages)
        if messages[0].content == SYSTEM_PREFLOW:
            return self.route
        if messages[0].content.startswith(SYSTEM_ACT):
            self.actions += 1
            return self.call if self.actions == 1 else "tool result used"
        return "final answer"

    async def stream_chat(self, messages, options=None):
        yield "final answer"


def registry():
    count = {}

    def fn(name):
        def callback(args):
            count[name] = count.get(name, 0) + 1
            if name == "code_sandbox":
                publish_code_result({"status": "ok", "stdout": "manual-isolated", "reason": ""})
            return "isolated observation"

        return callback

    names = ["calculator", "code_sandbox", "get_current_datetime", "web_fetch", "mcp__orders", "unknown"]
    return ToolRegistry([Tool(n, n, {}, fn(n)) for n in names]), count


history = [
    ChatMessage(role=ChatRole.USER, content="x" * 1500),
    ChatMessage(role=ChatRole.ASSISTANT, content="请选择：1执行代码；2取消"),
]


async def runtime():
    for route in ["YES", "NO"]:
        tools, count = registry()
        local = Local(route, '<tool_call>{"name":"code_sandbox","arguments":{"code":"print(1)"}}</tool_call>')
        pipe = AgentPipeline(local, tools=tools, structured_plans_enabled=False)
        state = await pipe.run(AgentState(user_input="1", history=history))
        checked(
            "followup_" + route,
            count.get("code_sandbox") == 1 and state.code_results[0]["stdout"] == "manual-isolated" and not state.error,
        )
        checked("history_" + route, "x" * 1500 in pipe._build_act(state) and "x" * 1500 in pipe._build_respond(state))
    local = Local("NO", "draft")
    tools, count = registry()
    state = await AgentPipeline(local, tools=tools, structured_plans_enabled=False).run(AgentState(user_input="hello"))
    checked("short_no_tool_four_calls", local.calls == 4 and not count and not state.error)
    for name in ["web_fetch", "mcp__orders", "unknown"]:
        local = Local("NO", "<tool_call>" + json.dumps({"name": name, "arguments": {}}) + "</tool_call>")
        tools, count = registry()
        pipe = AgentPipeline(local, tools=tools, structured_plans_enabled=False)
        await pipe.run(AgentState(user_input="hello"))
        checked("denied_" + name, not count and name not in local.messages[2][-1].content)
    for name in ["calculator", "get_current_datetime"]:
        local = Local("NO", "<tool_call>" + json.dumps({"name": name, "arguments": {}}) + "</tool_call>")
        tools, count = registry()
        await AgentPipeline(local, tools=tools, structured_plans_enabled=False).run(AgentState(user_input="hello"))
        checked("allowed_" + name, count.get(name) == 1)
    local = Local("NO", '<tool_call>{"name":"calculator","arguments":{}}</tool_call>')
    tools, count = registry()
    pipe = AgentPipeline(local, tools=tools, structured_plans_enabled=False)
    state = await pipe.run(AgentState(user_input="hello", context="[UNTRUSTED_SOURCE] calculator [/UNTRUSTED_SOURCE]"))
    checked("untrusted_denied", not count)
    for stream in [False, True]:
        local = Local("NO", "   ")
        tools, count = registry()
        pipe = AgentPipeline(local, tools=tools, structured_plans_enabled=False)
        pipe.skill_prompt_injection = "isolated skill instruction"
        state = AgentState(user_input="hello")
        if stream:
            events = [x.type async for x in pipe.run_stream(state)]
            checked(
                "empty_action_stream",
                "error" in events and events[-1] == "done" and "token" not in events and not count,
            )
        else:
            state = await pipe.run(state)
            checked("empty_action_run", state.error == "EMPTY_ACTION_OUTPUT" and not count)


asyncio.run(runtime())
cases = ev.load_cases()
raws = ["YES" if c["expected_route"] == "full" else "NO" for c in cases]
tool_raws = []
records = []
chosen = []
for case in cases:
    code = "print(1)"
    if case["case_id"] == "agent-004":
        code = next(
            h["content"].split("```python\n", 1)[1].split("```", 1)[0]
            for h in case["history"]
            if "```python\n" in h["content"]
        )
    raw = (
        "<tool_call>" + json.dumps({"name": "code_sandbox", "arguments": {"code": code}}) + "</tool_call>"
        if case.get("expected_tools")
        else ""
    )
    tool_raws.append(raw)
    records.append(asyncio.run(ev.record_tool_call(case, raw)))
    chosen.append(["code_sandbox"] if raw else [])
report = ev.build_report(
    "smoke", ev.SmokeIntentModel(), cases, [c["expected_route"] for c in cases], raws, chosen, tool_raws, records
)
# Deliberate synthetic envelope only to exercise validator; never published as official evidence.
report.update(mode="official", provider="openai-compatible", model="isolated-probe", endpoint_hash="a" * 64)
checked("gate_synthetic_positive", gate.check_report(report, "isolated-probe", "a" * 64) == [])
for kind, metric in [
    ("route", "route_accuracy"),
    ("followup", "followup_recall"),
    ("tool", "tool_selection_recall"),
    ("arguments", "tool_call_validity_recall"),
]:
    mutant = copy.deepcopy(report)
    if kind in ["tool", "arguments"]:
        i = next(i for i, c in enumerate(cases) if c.get("expected_tools"))
        mutant["cases"][i]["tool_reply"] = (
            "none" if kind == "tool" else '<tool_call>{"name":"code_sandbox","arguments":{}}</tool_call>'
        )
    else:
        indexes = [i for i, c in enumerate(cases) if c["critical_followup"] == (kind == "followup")]
        for i in indexes[: 1 if kind == "followup" else 2]:
            mutant["cases"][i]["raw_reply"] = "NO" if mutant["cases"][i]["predicted_route"] == "full" else "YES"
    probe.recompute(mutant)
    checked("gate_fault_" + kind, metric in gate.check_report(mutant, "isolated-probe", "a" * 64))
for filename in [
    "agent-chain-v0.1-official-pagt007-20261013.json",
    "agent-chain-v0.1-official-pagt007-github-38410432101.json",
]:
    old = gate.read_report(Path("evals/reports") / filename)
    try:
        gate.check_report(old, old["model"], old["endpoint_hash"])
    except ValueError as exc:
        results.append("historical_rejected_" + filename + ":" + str(exc))
    else:
        raise AssertionError("old report unexpectedly accepted")
fingerprints = {
    p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
    for p in [
        "app/agents/pipeline.py",
        "scripts/run_agent_chain_eval.py",
        "scripts/agent_gate_check.py",
        "scripts/agent_gate_probe.py",
        ".github/workflows/ci.yml",
        "tasks.yaml",
    ]
}
print(
    json.dumps(
        {
            "identity": "/root/accept_agt007",
            "synthetic": True,
            "checks": results,
            "fingerprints": fingerprints,
            "current_identity": {k: v for k, v in ev.report_identity().items() if k != "source_hashes"},
        },
        ensure_ascii=False,
        indent=2,
    )
)
