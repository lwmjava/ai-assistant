"""Offline AGT-003 review probes; no model/network/DB/sandbox execution."""
import asyncio
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.agents.pipeline import AgentPipeline, AgentState, build_tool_choice_messages  # noqa: E402
from app.agents.prompts import SYSTEM_ACT, SYSTEM_PREFLOW, SYSTEM_RESPOND  # noqa: E402
from app.agents.tools.base import Tool, ToolRegistry, inspect_tool_call  # noqa: E402
from app.agents.tools.builtin import code_sandbox  # noqa: E402
from app.llm.base import ChatMessage, ChatRole, LLMProvider  # noqa: E402
from scripts.run_agent_chain_eval import load_cases, predict_tools, score_tools  # noqa: E402

HISTORY = [
    {"role": "user", "content": "请执行代码 print(1)。"},
    {"role": "assistant", "content": "1. 执行代码；2. 取消。请选择。"},
]


class EmptyActionLLM(LLMProvider):
    model = "isolated-review-not-quality"

    async def chat(self, messages, options=None):
        system = messages[0].content
        if system == SYSTEM_PREFLOW:
            return "NO"
        if system == SYSTEM_ACT:
            return ""
        if system == SYSTEM_RESPOND:
            return "模拟最终回复"
        return "理解"

    async def stream_chat(self, messages, options=None):
        yield "模拟最终回复"


class MissingArgsLLM(LLMProvider):
    model = "isolated-review-not-quality"

    async def chat(self, messages, options=None):
        return '<tool_call>{"name":"code_sandbox","arguments":{}}</tool_call>'

    async def stream_chat(self, messages, options=None):
        yield await self.chat(messages, options)


async def main():
    calls = []

    async def recording_tool(arguments):
        calls.append(arguments)
        return "recorded"

    registry = ToolRegistry([Tool("code_sandbox", "记录工具", {}, recording_tool)])
    observations = {}
    for stream in [False, True]:
        history = [ChatMessage(role=ChatRole(x["role"]), content=x["content"]) for x in HISTORY]
        state = AgentState(user_input="1", history=history)
        pipeline = AgentPipeline(EmptyActionLLM(), tools=registry)
        checkpoints = []
        pipeline._checkpoint = lambda state, **kw: checkpoints.append(kw)
        events = []
        if stream:
            events = [e.type async for e in pipeline.run_stream(state)]
        else:
            await pipeline.run(state)
        assert state.draft == "" and not state.error and not calls
        assert state.answer == "模拟最终回复"
        assert checkpoints[-1]["status"] == "done"
        observations["empty_action_stream" if stream else "empty_action_run"] = {
            "draft_empty": True, "error": state.error, "tool_calls": len(calls),
            "code_results": state.code_results, "checkpoint_status": checkpoints[-1]["status"],
            "events": events,
        }
    cases = load_cases()
    chosen, raw = await predict_tools(MissingArgsLLM(), cases)
    score = score_tools(cases, chosen)
    assert score["tool_selection_recall"] == 1.0
    parsed = inspect_tool_call(next(x for x in raw if x))
    result = await code_sandbox(parsed.call.arguments)
    assert result.error_code == "missing_code"
    observations["missing_args_counts_as_selection_hit"] = {
        "tool_selection_recall": score["tool_selection_recall"],
        "actual_builtin_validation_error": result.error_code,
        "recording_tool_calls_by_evaluator": len(calls),
    }
    case = next(x for x in cases if x["case_id"] == "agent-004")
    messages = build_tool_choice_messages(case["history"], case["user_input"])
    observations["agent004_input"] = {
        "history": case["history"], "user_input": case["user_input"],
        "expected_tools": case["expected_tools"],
        "sent_message_count": len(messages), "omitted_code_in_dataset": True,
    }
    observations["scope"] = "E2 controlled offline behavior; not new real-model quality evidence"
    observations["source_hashes"] = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in ["app/agents/pipeline.py", "scripts/run_agent_chain_eval.py",
                     "evals/datasets/agent-chain-v0.1/cases.json"]
    }
    output = ROOT / "docs/reviews/agt003_review_probe_20261013.json"
    output.write_text(json.dumps(observations, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8", newline="\n")
    print("AGT003_REVIEW_PROBES:3 findings reproduced; no model/network/DB/sandbox execution")


if __name__ == "__main__":
    asyncio.run(main())
