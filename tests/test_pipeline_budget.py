"""PAGT-003：工具调用预算与循环上限。

失败边界五类反例：数值边界 / 失败路径 / 权限与租户 / 身份与绑定 / 回滚与幂等。
脚本 LLM 模式沿用 tests/test_agent_chain.py：按 system 提示词分支应答，记录全部 user 提示。
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from app.agents.pipeline import AgentPipeline, AgentState
from app.agents.tools.base import Tool, ToolRegistry
from app.core.config import Settings, settings

NOTICE_MARK = "已达到工具调用次数上限"


def _echo_registry() -> tuple[ToolRegistry, dict[str, int]]:
    counter = {"n": 0}

    def echo(arguments: dict) -> str:
        counter["n"] += 1
        return f"echo:{arguments.get('q', '')}"

    tool = Tool(name="echo", description="回显", parameters={"type": "object"}, func=echo)
    return ToolRegistry([tool]), counter


def _boom_registry() -> tuple[ToolRegistry, dict[str, int]]:
    counter = {"n": 0}

    def boom(arguments: dict) -> str:
        counter["n"] += 1
        raise RuntimeError(f"boom:{arguments.get('q', '')}")

    tool = Tool(name="boom", description="抛错工具", parameters={"type": "object"}, func=boom)
    return ToolRegistry([tool]), counter


def _loop_call(n: int) -> str:
    return f'<tool_call>{{"name": "echo", "arguments": {{"q": "{n}"}}}}</tool_call>'


class ScriptLLM:
    """按 system 提示词分支脚本化的假 LLM；记录每次调用的 system/user 提示。"""

    model = "script-budget"

    def __init__(self, reply) -> None:
        self.reply = reply
        self.prompts: list[dict[str, str]] = []

    async def chat(self, messages, options=None) -> str:
        system = messages[0].content
        user = messages[-1].content
        self.prompts.append({"system": system, "user": user})
        return self.reply(system, user)

    async def stream_chat(self, messages, options=None):
        yield "最终回答"


def _full_reply(act_reply, final_reply: str = "最终回答正文", score: str = "0.9"):
    """完整路径脚本：理解/分流/规划/质量门/批判/应答按系统提示词分支；行动阶段交给 act_reply。"""

    def reply(system: str, user: str) -> str:
        if "意图分析器" in system:
            return "意图"
        if "意图分流器" in system:
            return "YES"
        if "任务规划器" in system:
            return "计划"
        if "质量评估员" in system:
            return score
        if "修正导师" in system:
            return "补一下要点"
        if "批判审查员" in system:
            return "无需修正"
        if "最终应答者" in system:
            return final_reply
        return act_reply(user)

    return reply


def _run_full(reply, registry, rounds: int):
    llm = ScriptLLM(reply)
    pipeline = AgentPipeline(llm, tools=registry)
    pipeline.max_tool_rounds = rounds
    state = asyncio.run(pipeline.run(AgentState(user_input="继续")))
    return llm, state


# ── 1. 数值边界 ─────────────────────────────────────────────────────


def test_budget_config_default_is_prd_ten() -> None:
    assert Settings.model_fields["AGENT_MAX_TOOL_ROUNDS"].default == 10


@pytest.mark.parametrize("bad", [0, -1, True, 2.5, "abc"])
def test_budget_config_invalid_rejected(bad: object) -> None:
    with pytest.raises(ValidationError):
        Settings(AGENT_MAX_TOOL_ROUNDS=bad)


def test_budget_config_above_upper_bound_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(AGENT_MAX_TOOL_ROUNDS=101)


# ── 2. 失败路径 ─────────────────────────────────────────────────────


@pytest.mark.usefixtures("legacy_plan_mode")
def test_looping_calls_truncated_at_budget_and_notice_in_draft() -> None:
    seen = {"n": 0}

    def act(user: str) -> str:
        seen["n"] += 1
        return _loop_call(seen["n"])

    registry, counter = _echo_registry()
    _llm, state = _run_full(_full_reply(act), registry, rounds=2)

    assert counter["n"] == 2  # 达到上限后不再发起第 3 次调用
    assert state.draft.count(NOTICE_MARK) == 1  # 确定性追加且幂等


@pytest.mark.usefixtures("legacy_plan_mode")
def test_failing_tool_still_consumes_budget() -> None:
    seen = {"n": 0}

    def act(user: str) -> str:
        seen["n"] += 1
        return f'<tool_call>{{"name": "boom", "arguments": {{"q": "{seen["n"]}"}}}}</tool_call>'

    registry, counter = _boom_registry()
    _llm, state = _run_full(_full_reply(act), registry, rounds=2)

    assert counter["n"] == 2  # 失败调用照常消耗预算，管线不中断
    assert any("boom" in r for r in state.tool_results)
    assert NOTICE_MARK in state.draft


@pytest.mark.usefixtures("legacy_plan_mode")
def test_budget_notice_coexists_with_exhausted_sentinel() -> None:
    """跑满后 finalize 阶段 LLM 仍发工具调用：既有 _EXHAUSTED_DRAFT 与本卡 notice 并存。"""
    seen = {"n": 0}

    def act(user: str) -> str:
        seen["n"] += 1
        return _loop_call(seen["n"])

    registry, counter = _echo_registry()
    _llm, state = _run_full(_full_reply(act), registry, rounds=2)

    assert counter["n"] == 2
    assert "工具调用次数已用完，未能得到最终结果。" in state.draft
    assert NOTICE_MARK in state.draft


@pytest.mark.usefixtures("legacy_plan_mode")
def test_quality_gate_on_scored_draft_excludes_notice() -> None:
    """方案 B：质量门开启时，被评分 draft 不含 notice；最终 draft 仍带 notice 且仅一份。"""
    saved_enabled = settings.AGENT_QUALITY_GATE_ENABLED
    saved_revisions = settings.AGENT_MAX_REVISIONS
    seen = {"n": 0}

    def act(user: str) -> str:
        seen["n"] += 1
        return _loop_call(seen["n"])

    try:
        settings.AGENT_QUALITY_GATE_ENABLED = True
        settings.AGENT_MAX_REVISIONS = 1
        registry, counter = _echo_registry()
        llm, state = _run_full(_full_reply(act, score="0.1"), registry, rounds=2)
    finally:
        settings.AGENT_QUALITY_GATE_ENABLED = saved_enabled
        settings.AGENT_MAX_REVISIONS = saved_revisions

    assert counter["n"] == 4  # 首次循环 2 次 + 质量门重入行动循环 2 次（每轮循环各自获预算，已登记边界）
    score_prompts = [p["user"] for p in llm.prompts if "质量评估员" in p["system"]]
    assert score_prompts, "质量门开启应至少触发一次评分"
    for prompt in score_prompts:
        assert NOTICE_MARK not in prompt, "notice 不得进入被评分草稿"
    assert state.draft.count(NOTICE_MARK) == 1


# ── 3. 权限与租户 ──────────────────────────────────────────────────


@pytest.mark.usefixtures("legacy_plan_mode")
def test_budget_is_per_request_not_global() -> None:
    """两个独立 pipeline/registry 各自跑满预算，计数互不共享。"""
    seen1 = {"n": 0}
    seen2 = {"n": 0}

    def act1(user: str) -> str:
        seen1["n"] += 1
        return _loop_call(seen1["n"])

    def act2(user: str) -> str:
        seen2["n"] += 1
        return _loop_call(seen2["n"])

    registry1, counter1 = _echo_registry()
    _llm1, state1 = _run_full(_full_reply(act1), registry1, rounds=2)
    assert counter1["n"] == 2
    assert NOTICE_MARK in state1.draft

    registry2, counter2 = _echo_registry()
    _llm2, state2 = _run_full(_full_reply(act2), registry2, rounds=2)
    assert counter2["n"] == 2
    assert NOTICE_MARK in state2.draft


# ── 4. 身份与绑定 ──────────────────────────────────────────────────


@pytest.mark.usefixtures("legacy_plan_mode")
def test_no_notice_on_duplicate_break() -> None:
    """同参数重复调用走 skip-break：不是预算耗尽，draft 不得带 notice（防误报）。"""
    call = '<tool_call>{"name": "echo", "arguments": {"q": "same"}}</tool_call>'
    registry, counter = _echo_registry()
    _llm, state = _run_full(_full_reply(lambda user: call), registry, rounds=5)

    assert counter["n"] == 1
    assert NOTICE_MARK not in state.draft


@pytest.mark.parametrize(("rounds", "expected"), [(2, 2), (3, 3)])
@pytest.mark.usefixtures("legacy_plan_mode")
def test_rounds_attribute_drives_truncation_point(rounds: int, expected: int) -> None:
    seen = {"n": 0}

    def act(user: str) -> str:
        seen["n"] += 1
        return _loop_call(seen["n"])

    registry, counter = _echo_registry()
    _llm, state = _run_full(_full_reply(act), registry, rounds=rounds)
    assert counter["n"] == expected


# ── 5. 回滚与幂等 ──────────────────────────────────────────────────


@pytest.mark.usefixtures("legacy_plan_mode")
def test_no_notice_when_llm_stops_voluntarily() -> None:
    """未达上限（LLM 自愿停）= 现状行为：正常草稿，不带 notice。"""
    calls = {"n": 0}

    def act(user: str) -> str:
        if calls["n"] < 2:
            calls["n"] += 1
            return _loop_call(calls["n"])
        return "收尾草稿"

    registry, counter = _echo_registry()
    _llm, state = _run_full(_full_reply(act), registry, rounds=5)

    assert counter["n"] == 2
    assert NOTICE_MARK not in state.draft
    assert "收尾草稿" in state.draft


@pytest.mark.usefixtures("legacy_plan_mode")
def test_at_budget_no_extra_side_effects() -> None:
    """跑满上限后计数器不再增长、无第 N+1 次调用。"""
    seen = {"n": 0}

    def act(user: str) -> str:
        seen["n"] += 1
        return _loop_call(seen["n"])

    registry, counter = _echo_registry()
    _llm, state = _run_full(_full_reply(act), registry, rounds=2)

    assert counter["n"] == 2
    # finalize 阶段虽再次行动，但工具已禁用，不得产生新的 registry 执行
    assert all("echo" not in r for r in state.tool_results[2:])
