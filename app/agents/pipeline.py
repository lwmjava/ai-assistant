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
    SYSTEM_REFLECT,
    SYSTEM_RESPOND,
    SYSTEM_UNDERSTAND,
)
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
    context: str = ""  # 检索 / 工具产出的外部上下文
    draft: str = ""
    reflection: str = ""
    answer: str = ""
    error: str | None = None


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
    ) -> None:
        self.llm = llm
        self.options = options or LLMOptions()
        self.retriever = retriever

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
        return (
            f"## 回答计划\n{state.plan}\n\n"
            f"## 外部上下文\n{context}\n\n"
            f"## 用户消息\n{state.user_input}\n\n"
            "请按系统要求撰写回答草稿。"
        )

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
        """一次性执行全部环节，返回填充后的状态。"""
        try:
            for attr, _name, system, builder in _STAGES:
                setattr(state, attr, await self._stage(system, builder, state))
                if attr == "plan" and self.retriever is not None:
                    state.context = await self.retriever.retrieve(
                        state.user_input, state.plan
                    )
            state.answer = await self._stage(
                _FINAL_SYSTEM, _FINAL_BUILDER, state
            )
        except Exception as exc:  # noqa: BLE001 — 管线级兜底，避免向上吞没请求
            logger.exception("Agent 管线执行失败")
            state.error = str(exc)
            if not state.answer:
                state.answer = "抱歉，处理你的请求时出现问题，请稍后重试。"
        return state

    async def run_stream(self, state: AgentState) -> AsyncIterator[AgentEvent]:
        """流式执行：广播阶段进度事件，最终环节逐字吐出 token。"""
        try:
            for attr, name, system, builder in _STAGES:
                yield AgentEvent("stage", name)
                setattr(state, attr, await self._stage(system, builder, state))
                if attr == "plan" and self.retriever is not None:
                    yield AgentEvent("stage", "检索")
                    state.context = await self.retriever.retrieve(
                        state.user_input, state.plan
                    )

            yield AgentEvent("stage", _FINAL_NAME)
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
