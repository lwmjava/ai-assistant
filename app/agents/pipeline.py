"""Agent 五阶段编排管线。

将一次用户提问拆解为「理解 → 规划 → 行动 → 反思 → 响应」五个环节，
每个环节调用大模型完成特定子目标，逐步逼近高质量回答。

- 外部检索 / 工具等能力通过可选的 ``retriever`` 钩子接入（位于「规划」之后、
  「行动」之前），当前默认不接入，由模型基于自身知识作答，便于后续平滑扩展 RAG。
- 提供 ``run``（一次性返回）与 ``run_stream``（增量流式返回）两种执行模式，
  流式模式会在最终「响应」环节逐字吐出 token，并向前端广播各环节进度事件。
"""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol

from app.agents.prompts import (
    SYSTEM_ACT,
    SYSTEM_PLAN,
    SYSTEM_PREFLOW,
    SYSTEM_QUALITY_CRITIQUE,
    SYSTEM_QUALITY_GATE,
    SYSTEM_REFLECT,
    SYSTEM_RESPOND,
    SYSTEM_UNDERSTAND,
)
from app.agents.tools.base import ToolRegistry, parse_tool_call
from app.core.config import settings
from app.llm.base import ChatMessage, ChatRole, LLMOptions, LLMProvider

logger = logging.getLogger(__name__)


class Retriever(Protocol):
    """外部检索钩子（RAG / 工具）。

    在「规划」之后被调用，返回与用户问题相关的外部上下文文本。
    """

    async def retrieve(self, query: str, plan: str) -> str:
        """返回检索到的上下文文本。"""
        ...


@dataclass
class AgentState:
    """管线在一次执行中的可变状态。"""

    user_input: str
    history: list[ChatMessage] = field(default_factory=list)
    understanding: str = ""
    plan: str = ""
    context: str = ""  # 检索产出的外部上下文
    tool_results: list[str] = field(default_factory=list)  # 工具调用的观测结果
    draft: str = ""
    reflection: str = ""
    answer: str = ""
    error: str | None = None
    needs_full_pipeline: bool = True  # Preflight 意图短路：False 表示简单问题，跳过规划/检索/反思
    quality_score: float = 0.0  # QualityGate 最近一次质量评分
    revision: int = 0  # QualityGate 自纠错已执行的轮数


class AgentEvent:
    """流式模式下的事件（阶段进度 / token / 结束 / 错误）。"""

    def __init__(self, type_: str, data: str = "") -> None:
        self.type = type_
        self.data = data

    def to_dict(self) -> dict:
        return {"type": self.type, "data": self.data}


# 前四个环节（理解 / 规划 / 行动 / 反思）：(状态属性, 阶段名, 系统提示, 内容构造器)
_STAGES = [
    ("understanding", "理解", SYSTEM_UNDERSTAND, "_build_understand"),
    ("plan", "规划", SYSTEM_PLAN, "_build_plan"),
    ("draft", "行动", SYSTEM_ACT, "_build_act"),
    ("reflection", "反思", SYSTEM_REFLECT, "_build_reflect"),
]

# 最终环节（响应）单独处理以支持流式输出。
_FINAL_ATTR = "answer"
_FINAL_NAME = "响应"
_FINAL_SYSTEM = SYSTEM_RESPOND
_FINAL_BUILDER = "_build_respond"


class AgentPipeline:
    """五阶段编排执行器。"""

    def __init__(
        self,
        llm: LLMProvider,
        options: LLMOptions | None = None,
        retriever: Retriever | None = None,
        tools: ToolRegistry | None = None,
    ) -> None:
        self.llm = llm
        self.options = options or LLMOptions()
        self.retriever = retriever
        self.tools = tools
        self.max_tool_rounds: int = 5

    def _messages(self, system: str, user_content: str) -> list[ChatMessage]:
        return [
            ChatMessage(role=ChatRole.SYSTEM, content=system),
            ChatMessage(role=ChatRole.USER, content=user_content),
        ]

    async def _stage(self, system: str, builder_name: str, state: AgentState) -> str:
        builder = getattr(self, builder_name)
        content = builder(state)
        result = await self.llm.chat(self._messages(system, content), self.options)
        return result.strip()

    # ── 各环节内容构造 ──────────────────────────────
    @staticmethod
    def _history_text(state: AgentState) -> str:
        if not state.history:
            return "（无历史对话）"
        role_map = {
            ChatRole.USER.value: "用户",
            ChatRole.ASSISTANT.value: "助手",
            ChatRole.SYSTEM.value: "系统",
        }
        lines = [
            f"{role_map.get(m.role.value, m.role.value)}：{m.content}"
            for m in state.history
        ]
        return "\n".join(lines)

    def _build_understand(self, state: AgentState) -> str:
        return (
            f"## 历史对话\n{self._history_text(state)}\n\n"
            f"## 用户最新消息\n{state.user_input}\n\n"
            "请按系统要求输出意图分析。"
        )

    def _build_plan(self, state: AgentState) -> str:
        return (
            f"## 用户意图理解\n{state.understanding}\n\n"
            f"## 用户最新消息\n{state.user_input}\n\n"
            "请按系统要求制定步骤计划。"
        )

    def _build_act(self, state: AgentState) -> str:
        context = state.context or "（未接入外部检索，仅基于模型知识作答）"
        tools_text = ""
        if self.tools is not None:
            tools_text = (
                "## 可用外部工具\n"
                + self.tools.describe()
                + "\n\n如需调用工具，仅输出如下格式（不要附加其它文字）：\n"
                '<tool_call>{"name": "工具名", "arguments": {参数键值对}}</tool_call>'
            )
        tool_results = ""
        if state.tool_results:
            tool_results = "## 已调用工具结果\n" + "\n".join(state.tool_results)
        return (
            f"## 回答计划\n{state.plan}\n\n"
            f"## 外部上下文\n{context}\n\n"
            f"{tools_text}\n\n"
            f"{tool_results}\n\n"
            f"## 用户消息\n{state.user_input}\n\n"
            "请按系统要求撰写回答草稿，或输出工具调用指令。"
        )

    async def _run_action_once(self, state: AgentState) -> str:
        """执行一次「行动」环节，返回模型原始输出（可能是草稿或工具调用）。"""
        return await self._stage(SYSTEM_ACT, "_build_act", state)

    async def _execute_tool(self, call, state: AgentState) -> str:
        """执行工具调用并追加观测结果到状态。"""
        if self.tools is None:
            return "[工具调用失败] 当前未配置任何工具。"
        return await self.tools.run(call)

    def _build_reflect(self, state: AgentState) -> str:
        return (
            f"## 回答计划\n{state.plan}\n\n"
            f"## 回答草稿\n{state.draft}\n\n"
            "请按系统要求审查草稿并给出修正点。"
        )

    def _build_respond(self, state: AgentState) -> str:
        reflection = (
            state.reflection
            if state.reflection and "无需修正" not in state.reflection
            else "（审查认为无需修正）"
        )
        return (
            f"## 用户消息\n{state.user_input}\n\n"
            f"## 回答草稿\n{state.draft}\n\n"
            f"## 审查意见\n{reflection}\n\n"
            "请按系统要求产出最终回复。"
        )

    # ── 执行入口 ────────────────────────────────────
    async def run(self, state: AgentState) -> AgentState:
        """一次性执行全部环节，返回填充后的状态。

        流程：理解 → Preflight 意图短路 →（复杂则）规划 → 检索 → 行动 →
        QualityGate 自纠错 → 反思 → 响应；（简单则）直接行动 → 响应。
        """
        try:
            # 1. 理解
            state.understanding = await self._stage(
                SYSTEM_UNDERSTAND, "_build_understand", state
            )
            # 2. Preflight 意图短路
            state.needs_full_pipeline = await self._needs_plan(state)
            if not state.needs_full_pipeline:
                # 简单问题：跳过规划/检索/反思，直接行动 → 响应
                state.draft = await self._run_action_once(state)
                state.answer = await self._stage(
                    _FINAL_SYSTEM, _FINAL_BUILDER, state
                )
                return state

            # 3. 规划
            state.plan = await self._stage(SYSTEM_PLAN, "_build_plan", state)
            # 4. 检索（可选）
            if self.retriever is not None:
                state.context = await self.retriever.retrieve(
                    state.user_input, state.plan
                )
            # 5. 行动 → QualityGate 自纠错
            state.draft = await self._run_action_loop(state)
            state.draft = await self._quality_gate_loop(state)
            # 6. 反思
            state.reflection = await self._stage(
                SYSTEM_REFLECT, "_build_reflect", state
            )
            # 7. 响应
            state.answer = await self._stage(_FINAL_SYSTEM, _FINAL_BUILDER, state)
        except Exception as exc:  # noqa: BLE001 — 管线级兜底，避免向上吞没请求
            logger.exception("Agent 管线执行失败")
            state.error = str(exc)
            if not state.answer:
                state.answer = "抱歉，处理你的请求时出现问题，请稍后重试。"
        return state

    async def _needs_plan(self, state: AgentState) -> bool:
        """Preflight 意图分流：返回是否需要完整规划流程。

        解析失败时默认走完整流程（不轻易短路），避免漏掉需要检索/推理的问题。
        """
        decision = await self.llm.chat(
            [
                ChatMessage(role=ChatRole.SYSTEM, content=SYSTEM_PREFLOW),
                ChatMessage(role=ChatRole.USER, content=state.user_input),
            ],
            self.options,
        )
        return "NO" not in (decision or "").upper()

    async def _quality_gate_loop(self, state: AgentState) -> str:
        """QualityGate 自纠错：低于阈值则把评审意见回灌「行动」并重跑，至多 N 轮。"""
        if not settings.AGENT_QUALITY_GATE_ENABLED:
            return state.draft
        draft = state.draft
        for _ in range(max(0, settings.AGENT_MAX_REVISIONS)):
            score = await self._evaluate_quality(state, draft)
            state.quality_score = score
            if score >= settings.AGENT_QUALITY_THRESHOLD:
                state.revision = 0
                return draft
            # 自纠错：生成修正要点作为补充上下文，重跑「行动」
            critique = await self._stage(SYSTEM_QUALITY_CRITIQUE, "_build_critique", state)
            state.context = (state.context + "\n" + critique).strip()
            draft = await self._run_action_loop(state)
            state.revision += 1
        return draft

    async def _evaluate_quality(self, state: AgentState, draft: str) -> float:
        """对草稿打分（0~1）。解析失败时视为合格（1.0），不触发无谓重跑。"""
        prompt = (
            f"## 用户目标\n{state.user_input}\n\n"
            f"## 回答计划\n{state.plan}\n\n"
            f"## 回答草稿\n{draft}\n\n"
            "请输出 0~1 的质量评分。"
        )
        raw = await self.llm.chat(
            [
                ChatMessage(role=ChatRole.SYSTEM, content=SYSTEM_QUALITY_GATE),
                ChatMessage(role=ChatRole.USER, content=prompt),
            ],
            self.options,
        )
        try:
            return float((raw or "").strip())
        except (ValueError, TypeError):
            return 1.0

    def _build_critique(self, state: AgentState) -> str:
        return (
            f"## 当前质量评分\n{state.quality_score:.2f}（合格线 "
            f"{settings.AGENT_QUALITY_THRESHOLD:.2f}）\n\n"
            f"## 当前草稿\n{state.draft}\n\n"
            "请基于上述要点给出具体修正方向（仅要点，不要重写整段）。"
        )

    async def _run_action_loop(self, state: AgentState) -> str:
        """执行「行动」环节：如需工具则循环调用，直至产出最终草稿。"""
        draft = ""
        for _ in range(self.max_tool_rounds):
            draft = await self._run_action_once(state)
            call = parse_tool_call(draft)
            if call is None:
                break
            observation = await self._execute_tool(call, state)
            state.tool_results.append(observation)
        return draft

    async def run_stream(self, state: AgentState) -> AsyncIterator[AgentEvent]:
        """流式执行：广播阶段进度事件，最终环节逐字吐出 token。"""
        try:
            # 1. 理解
            yield AgentEvent("stage", "理解")
            state.understanding = await self._stage(
                SYSTEM_UNDERSTAND, "_build_understand", state
            )
            # 2. Preflight 意图短路
            yield AgentEvent("stage", "意图分流")
            state.needs_full_pipeline = await self._needs_plan(state)
            if not state.needs_full_pipeline:
                yield AgentEvent("stage", "行动")
                state.draft = await self._run_action_once(state)
                yield AgentEvent("stage", "响应")
                chunks: list[str] = []
                async for delta in self.llm.stream_chat(
                    self._messages(_FINAL_SYSTEM, self._build_respond(state)),
                    self.options,
                ):
                    chunks.append(delta)
                    yield AgentEvent("token", delta)
                state.answer = "".join(chunks)
                yield AgentEvent("done", state.answer)
                return

            # 3. 规划
            yield AgentEvent("stage", "规划")
            state.plan = await self._stage(SYSTEM_PLAN, "_build_plan", state)
            # 4. 检索（可选）
            if self.retriever is not None:
                yield AgentEvent("stage", "检索")
                state.context = await self.retriever.retrieve(
                    state.user_input, state.plan
                )
            # 5. 行动
            yield AgentEvent("stage", "行动")
            draft = ""
            for _ in range(self.max_tool_rounds):
                draft = await self._run_action_once(state)
                call = parse_tool_call(draft)
                if call is None:
                    break
                yield AgentEvent("tool", f"调用工具：{call.name}")
                observation = await self._execute_tool(call, state)
                state.tool_results.append(observation)
            state.draft = draft
            # 5b. QualityGate 自纠错
            if settings.AGENT_QUALITY_GATE_ENABLED:
                for _ in range(max(0, settings.AGENT_MAX_REVISIONS)):
                    score = await self._evaluate_quality(state, state.draft)
                    state.quality_score = score
                    if score >= settings.AGENT_QUALITY_THRESHOLD:
                        break
                    yield AgentEvent("stage", "质量门自纠错")
                    critique = await self._stage(
                        SYSTEM_QUALITY_CRITIQUE, "_build_critique", state
                    )
                    state.context = (state.context + "\n" + critique).strip()
                    new_draft = ""
                    for _ in range(self.max_tool_rounds):
                        new_draft = await self._run_action_once(state)
                        call = parse_tool_call(new_draft)
                        if call is None:
                            break
                        observation = await self._execute_tool(call, state)
                        state.tool_results.append(observation)
                    state.draft = new_draft
                    state.revision += 1
            # 6. 反思
            yield AgentEvent("stage", "反思")
            state.reflection = await self._stage(
                SYSTEM_REFLECT, "_build_reflect", state
            )
            # 7. 响应
            yield AgentEvent("stage", "响应")
            chunks: list[str] = []
            async for delta in self.llm.stream_chat(
                self._messages(_FINAL_SYSTEM, self._build_respond(state)),
                self.options,
            ):
                chunks.append(delta)
                yield AgentEvent("token", delta)
            state.answer = "".join(chunks)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Agent 流式管线执行失败")
            state.error = str(exc)
            if not state.answer:
                state.answer = "抱歉，处理你的请求时出现问题，请稍后重试。"
            yield AgentEvent("error", state.answer)

        yield AgentEvent("done", state.answer)
