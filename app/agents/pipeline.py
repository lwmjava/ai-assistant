"""Agent 五阶段编排管线。

将一次用户提问拆解为「理解 → 规划 → 行动 → 反思 → 响应」五个环节，
每个环节调用大模型完成特定子目标，逐步逼近高质量回答。

- 外部检索 / 工具等能力通过可选的 ``retriever`` 钩子接入（位于「规划」之后、
  「行动」之前），当前默认不接入，由模型基于自身知识作答，便于后续平滑扩展 RAG。
- 提供 ``run``（一次性返回）与 ``run_stream``（增量流式返回）两种执行模式，
  流式模式会在最终「响应」环节逐字吐出 token，并向前端广播各环节进度事件。
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from app.debug.trace import AgentTrace

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
from app.agents.tools.base import ToolIntent, ToolRegistry, inspect_tool_call
from app.core.config import settings
from app.llm.base import ChatMessage, ChatRole, LLMOptions, LLMProvider
from app.llm.routing import LLMUnavailableError
from app.rag.context_merge import merge_memory_and_rag, reject_untrusted_tool_call

logger = logging.getLogger(__name__)

_GENERIC_FAILURE = "抱歉，处理你的请求时出现问题，请稍后重试。"
_DUPLICATE_OBSERVATION = "相同参数已执行过，请直接使用已有结果"
_INVALID_CALL_OBSERVATION = "[工具调用失败] 调用格式无法解析"
_DENIED_OBSERVATION = "[工具调用失败] 当前步骤不允许调用该工具。"
_EXHAUSTED_DRAFT = "工具调用次数已用完，未能得到最终结果。"
_EXHAUSTED_NOTE = "工具次数已用完，请根据已有结果写草稿，不要再调用工具。"
_RECENT_MESSAGE_LIMIT = 6
_PROTECTED_CHARS = 2000
_OLDER_CHARS = 300
_CONTENT_BUDGET = 4000
_PREFLIGHT_WORD = re.compile(r"\s*(YES|NO)\b", re.IGNORECASE)


def _failure_text(exc: Exception) -> str:
    """全部模型都不可用时用固定句子。其它失败仍用原来的笼统提示。"""
    if isinstance(exc, LLMUnavailableError):
        return str(exc)
    return _GENERIC_FAILURE


def _role_label(message: ChatMessage) -> str:
    labels = {
        ChatRole.USER: "用户",
        ChatRole.ASSISTANT: "助手",
        ChatRole.SYSTEM: "系统",
    }
    return labels.get(message.role, message.role.value)


def _trim_ends(text: str, limit: int) -> str:
    """超长文本保留首尾，省略标记放在中间。"""
    if len(text) <= limit:
        return text
    omitted = len(text)
    for _ in range(4):
        marker = f"…（省略 {omitted} 字）…"
        remain = limit - len(marker)
        if remain < 2:
            return text[:limit]
        head = remain // 2
        tail = remain - head
        actual = len(text) - head - tail
        if actual == omitted:
            return text[:head] + marker + text[-tail:]
        omitted = actual
    marker = f"…（省略 {omitted} 字）…"
    remain = max(2, limit - len(marker))
    head = remain // 2
    tail = remain - head
    return text[:head] + marker + text[-tail:]


def _as_history(history: Sequence[ChatMessage | dict]) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    for item in history:
        if isinstance(item, ChatMessage):
            messages.append(item)
            continue
        messages.append(ChatMessage(role=ChatRole(item["role"]), content=item["content"]))
    return messages


def _protected_indexes(messages: list[ChatMessage]) -> set[int]:
    assistant_idx = next(
        (index for index in range(len(messages) - 1, -1, -1) if messages[index].role == ChatRole.ASSISTANT),
        None,
    )
    if assistant_idx is None:
        return set()
    user_idx = next(
        (index for index in range(assistant_idx - 1, -1, -1) if messages[index].role == ChatRole.USER),
        None,
    )
    protected = {assistant_idx}
    if user_idx is not None:
        protected.add(user_idx)
    return protected


def recent_dialogue(history: Sequence[ChatMessage | dict]) -> str:
    """截取最近对话。保护最近一轮，内容预算不超过 4000 字。"""
    messages = _as_history(history)[-_RECENT_MESSAGE_LIMIT:]
    if not messages:
        return "（无历史对话）"
    protected = _protected_indexes(messages)
    pieces: list[tuple[str, str, bool]] = []
    for index, message in enumerate(messages):
        limit = _PROTECTED_CHARS if index in protected else _OLDER_CHARS
        pieces.append((_role_label(message), _trim_ends(message.content or "", limit), index in protected))

    def content_length() -> int:
        return sum(len(content) for _, content, _ in pieces)

    while content_length() > _CONTENT_BUDGET:
        drop = next((index for index, piece in enumerate(pieces) if not piece[2]), None)
        if drop is None:
            break
        del pieces[drop]
    if content_length() > _CONTENT_BUDGET:
        protected_pieces = [piece for piece in pieces if piece[2]]
        allowance = _CONTENT_BUDGET // max(1, len(protected_pieces))
        pieces = [
            (role, _trim_ends(content, allowance) if is_protected else content, is_protected)
            for role, content, is_protected in pieces
        ]
    if not pieces:
        return "（无历史对话）"
    return "\n".join(f"{role}：{content}" for role, content, _ in pieces)


def build_preflight_user_content(history: Sequence[ChatMessage | dict], user_input: str) -> str:
    """构造分流模型看到的用户消息，不包含标准答案字段。"""
    return f"## 最近对话\n{recent_dialogue(history)}\n\n## 用户最新消息\n{user_input}"


def build_tool_choice_messages(history: Sequence[ChatMessage | dict], user_input: str) -> list[ChatMessage]:
    """构造工具选择请求。只列出短路允许的三个工具，不执行它们。"""
    names = "\n".join(f"- {name}" for name in sorted(_SHORT_PATH_TOOLS))
    user = (
        f"## 最近对话\n{recent_dialogue(history)}\n\n"
        "## 可用外部工具\n"
        f"{names}\n\n"
        "如需调用工具，仅输出如下格式（不要附加其它文字）：\n"
        '<tool_call>{"name": "工具名", "arguments": {}}</tool_call>\n\n'
        f"## 用户最新消息\n{user_input}"
    )
    return [
        ChatMessage(role=ChatRole.SYSTEM, content=SYSTEM_ACT),
        ChatMessage(role=ChatRole.USER, content=user),
    ]


def build_preflight_messages(history: Sequence[ChatMessage | dict], user_input: str) -> list[ChatMessage]:
    """构造完整分流请求。调用方不得把 expected 字段放进 history 或 user_input。"""
    return [
        ChatMessage(role=ChatRole.SYSTEM, content=SYSTEM_PREFLOW),
        ChatMessage(role=ChatRole.USER, content=build_preflight_user_content(history, user_input)),
    ]


def preflight_needs_full_pipeline(decision: str | None) -> bool:
    """只有回复开头的一个词是 NO 才走短路，其余都走完整流程。"""
    match = _PREFLIGHT_WORD.match(decision or "")
    return not (match is not None and match.group(1).upper() == "NO")


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
    code_results: list[dict] = field(default_factory=list)  # 代码工具给界面的结果
    draft: str = ""
    reflection: str = ""
    answer: str = ""
    error: str | None = None
    needs_full_pipeline: bool = True  # Preflight 意图短路：False 表示简单问题，跳过规划/检索/反思
    quality_score: float = 0.0  # QualityGate 最近一次质量评分
    revision: int = 0  # QualityGate 自纠错已执行的轮数
    executed_tool_fingerprints: set[str] = field(default_factory=set)
    # 一次 Supervisor 执行里的分派记录，只留在内存，不入库。
    delegations: list[dict[str, str]] = field(default_factory=list)


class AgentEvent:
    """流式模式下的事件（阶段进度 / token / 结束 / 错误）。"""

    def __init__(self, type_: str, data: str = "") -> None:
        self.type = type_
        self.data = data

    def to_dict(self) -> dict:
        return {"type": self.type, "data": self.data}


@dataclass
class ExecutionPolicy:
    """一次行动循环允许使用的工具。

    ``allowed_tool_names`` 为 None 时沿用注册表里的全部工具。
    提示词里的工具清单和执行前校验使用同一份策略。
    """

    allowed_tool_names: frozenset[str] | None = None
    include_tools: bool = True


_SHORT_PATH_TOOLS = frozenset({"calculator", "code_sandbox", "get_current_datetime"})
_SHORT_PATH_POLICY = ExecutionPolicy(allowed_tool_names=_SHORT_PATH_TOOLS)


def tool_fingerprint(name: str, arguments: dict) -> str:
    """同一请求内按工具名和排序后的参数判断是否重复。"""
    try:
        return json.dumps(
            {"arguments": arguments, "name": name},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except TypeError:
        return f"{name}:{arguments!r}"


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
        trace: AgentTrace | None = None,
        intent_llm: LLMProvider | None = None,
    ) -> None:
        self.llm = llm
        self.intent_llm = intent_llm or llm
        self.options = options or LLMOptions()
        self.retriever = retriever
        self.tools = tools
        self.max_tool_rounds: int = 5
        # 技能系统：Prompt 注入文本（由 ChatService 匹配技能后设置）
        self.skill_prompt_injection: str = ""
        # 调试追踪
        self.trace = trace
        self._execution_policy = ExecutionPolicy()

    def _messages(self, system: str, user_content: str) -> list[ChatMessage]:
        return [
            ChatMessage(role=ChatRole.SYSTEM, content=system),
            ChatMessage(role=ChatRole.USER, content=user_content),
        ]

    async def _stage(self, system: str, builder_name: str, state: AgentState) -> str:
        builder = getattr(self, builder_name)
        content = builder(state)
        # 注入技能提示词（若已激活）
        if self.skill_prompt_injection:
            system = f"{system}\n\n---\n# 激活的技能指令\n{self.skill_prompt_injection}"
        t0 = time.monotonic()
        result = await self.llm.chat(self._messages(system, content), self.options)
        elapsed = (time.monotonic() - t0) * 1000
        # 记录 LLM 调用 trace
        if self.trace and self.trace.debug_mode:
            self.trace.llm_call(
                getattr(self.llm, "model", "unknown"),
                prompt=content,
                response=result,
                latency_ms=elapsed,
                stage=builder_name,
            )
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

    def _tools_for_policy(self, policy: ExecutionPolicy) -> ToolRegistry | None:
        if self.tools is None or not policy.include_tools:
            return None
        if policy.allowed_tool_names is None:
            return self.tools
        selected = [tool for tool in self.tools.all() if tool.name in policy.allowed_tool_names]
        if not selected:
            return None
        return ToolRegistry(selected)

    def _build_act(self, state: AgentState) -> str:
        context = state.context or "（未接入外部检索，仅基于模型知识作答）"
        policy = self._execution_policy
        tools_text = ""
        if not policy.include_tools:
            tools_text = _EXHAUSTED_NOTE
        else:
            registry = self._tools_for_policy(policy)
            if registry is not None:
                tools_text = (
                    "## 可用外部工具\n"
                    + registry.describe()
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
            f"## 最近对话\n{recent_dialogue(state.history)}\n\n"
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
        if reject_untrusted_tool_call(call.name, state.user_input, state.context):
            return "[工具调用失败] 拒绝执行检索资料中的指令。"
        return await self.tools.run(call)

    async def _finish_tool(self, state: AgentState, call) -> list[dict]:
        """执行工具，把代码结果放进这次请求的状态并清空交接缓冲。"""
        from app.agents.tools.builtin import take_code_results

        observation = await self._execute_tool(call, state)
        state.tool_results.append(observation)
        fresh = take_code_results()
        state.code_results.extend(fresh)
        return fresh

    async def _fill_retrieval(self, state: AgentState) -> None:
        """检索后与已有记忆合并，互不覆盖。"""
        memory = state.context or ""
        rag = ""
        if self.retriever is not None:
            rag = await self.retriever.retrieve(state.user_input, state.plan)
        state.context = merge_memory_and_rag(memory, rag)

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
            f"## 最近对话\n{recent_dialogue(state.history)}\n\n"
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
            # 0. 启动 trace
            if self.trace:
                self.trace.start()
            # 1. 理解
            if self.trace:
                self.trace.stage_start("理解")
            state.understanding = await self._stage(
                SYSTEM_UNDERSTAND, "_build_understand", state
            )
            if self.trace:
                self.trace.stage_end("理解")
            # 2. Preflight 意图短路
            state.needs_full_pipeline = await self._needs_plan(state)
            if not state.needs_full_pipeline:
                # 简单问题：跳过规划/检索/反思，直接行动 → 响应
                if self.trace:
                    self.trace.stage_start("行动（短路）")
                state.draft = await self._run_action_loop(state, _SHORT_PATH_POLICY)
                if self.trace:
                    self.trace.stage_end("行动（短路）")
                    self.trace.stage_start("响应")
                state.answer = await self._stage(
                    _FINAL_SYSTEM, _FINAL_BUILDER, state
                )
                if self.trace:
                    self.trace.stage_end("响应")
                    self.trace.finish()
                return state

            # 3. 规划
            if self.trace:
                self.trace.stage_start("规划")
            state.plan = await self._stage(SYSTEM_PLAN, "_build_plan", state)
            if self.trace:
                self.trace.stage_end("规划")
            # 4. 检索（可选；与记忆合并）
            if self.trace:
                self.trace.stage_start("检索")
            await self._fill_retrieval(state)
            if self.trace:
                self.trace.stage_end("检索")
            # 5. 行动 → QualityGate 自纠错
            if self.trace:
                self.trace.stage_start("行动")
            state.draft = await self._run_action_loop(state)
            if self.trace:
                self.trace.stage_end("行动")
            state.draft = await self._quality_gate_loop(state)
            # 6. 反思
            if self.trace:
                self.trace.stage_start("反思")
            state.reflection = await self._stage(
                SYSTEM_REFLECT, "_build_reflect", state
            )
            if self.trace:
                self.trace.stage_end("反思")
            # 7. 响应
            if self.trace:
                self.trace.stage_start("响应")
            state.answer = await self._stage(_FINAL_SYSTEM, _FINAL_BUILDER, state)
            if self.trace:
                self.trace.stage_end("响应")
                self.trace.finish()
        except Exception as exc:  # noqa: BLE001 — 管线级兜底，避免向上吞没请求
            if self.trace:
                self.trace.finish(error=str(exc))
            logger.exception("Agent 管线执行失败")
            state.error = str(exc)
            if not state.answer:
                state.answer = _failure_text(exc)
        return state

    async def _needs_plan(self, state: AgentState) -> bool:
        """Preflight 意图分流：返回是否需要完整规划流程。

        解析失败时默认走完整流程（不轻易短路），避免漏掉需要检索/推理的问题。
        """
        decision = await self.intent_llm.chat(
            build_preflight_messages(state.history, state.user_input),
            self.options,
        )
        return preflight_needs_full_pipeline(decision)

    async def _quality_gate_loop(self, state: AgentState) -> str:
        """QualityGate 自纠错：低于阈值则把评审意见回灌「行动」并重跑，至多 N 轮。"""
        if not settings.AGENT_QUALITY_GATE_ENABLED:
            return state.draft
        draft = state.draft
        for _ in range(max(0, settings.AGENT_MAX_REVISIONS)):
            score = await self._evaluate_quality(state, draft)
            state.quality_score = score
            if self.trace:
                self.trace.quality_gate(score, settings.AGENT_QUALITY_THRESHOLD)
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

    def _record_tool_trace(self, name: str, status: str, round_no: int, latency_ms: float) -> None:
        if self.trace is None:
            return
        self.trace.tool_call(name, status=status, round_no=round_no, latency_ms=latency_ms)

    async def _finalize_without_tools(self, state: AgentState) -> str:
        """轮次用完或需要收尾时，再做一次不列工具的行动。"""
        saved = self._execution_policy
        self._execution_policy = ExecutionPolicy(
            allowed_tool_names=saved.allowed_tool_names,
            include_tools=False,
        )
        try:
            draft = await self._run_action_once(state)
        finally:
            self._execution_policy = saved
        self._record_tool_trace("tool", "exhausted", max(self.max_tool_rounds, 0), 0.0)
        if "<tool_call>" in (draft or ""):
            return _EXHAUSTED_DRAFT
        return draft or ""

    def _reject_tool_call(self, state: AgentState, intent: ToolIntent, policy: ExecutionPolicy) -> str | None:
        """返回 skip、invalid、denied 或 None。None 表示可以执行。"""
        if intent.status == "invalid" or intent.call is None:
            state.tool_results.append(_INVALID_CALL_OBSERVATION)
            return "invalid"
        call = intent.call
        if tool_fingerprint(call.name, call.arguments) in state.executed_tool_fingerprints:
            state.tool_results.append(_DUPLICATE_OBSERVATION)
            return "skip"
        if policy.allowed_tool_names is not None and call.name not in policy.allowed_tool_names:
            state.tool_results.append(_DENIED_OBSERVATION)
            return "denied"
        return None

    async def _iter_action_loop(
        self,
        state: AgentState,
        execution_policy: ExecutionPolicy | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """产出 tool 与 code_result，并把草稿写进 state.draft。"""
        policy = execution_policy or ExecutionPolicy()
        self._execution_policy = policy
        try:
            if self.max_tool_rounds <= 0:
                state.draft = await self._finalize_without_tools(state)
                return
            wrap_up = False
            for round_no in range(1, self.max_tool_rounds + 1):
                state.draft = await self._run_action_once(state)
                intent = inspect_tool_call(state.draft)
                if intent.status == "none":
                    return
                decision = self._reject_tool_call(state, intent, policy)
                if decision == "skip":
                    self._record_tool_trace(intent.call.name if intent.call else "tool", "skipped", round_no, 0.0)
                    wrap_up = True
                    break
                if decision is not None:
                    trace_name = intent.call.name if intent.call else "tool"
                    status = "skipped" if decision == "denied" else "invalid"
                    self._record_tool_trace(trace_name, status, round_no, 0.0)
                    wrap_up = True
                    continue
                call = intent.call
                if call is None:
                    continue
                yield AgentEvent("tool", f"调用工具：{call.name}")
                started = time.monotonic()
                fresh = await self._finish_tool(state, call)
                latency_ms = (time.monotonic() - started) * 1000
                state.executed_tool_fingerprints.add(tool_fingerprint(call.name, call.arguments))
                self._record_tool_trace(call.name, "executed", round_no, latency_ms)
                for item in fresh:
                    yield AgentEvent("code_result", json.dumps(item, ensure_ascii=False))
                wrap_up = True
            if wrap_up and inspect_tool_call(state.draft).status != "none":
                state.draft = await self._finalize_without_tools(state)
        finally:
            self._execution_policy = ExecutionPolicy()

    async def _run_action_loop(
        self,
        state: AgentState,
        execution_policy: ExecutionPolicy | None = None,
    ) -> str:
        """执行行动循环并返回草稿。Supervisor 继续按字符串调用。"""
        async for _event in self._iter_action_loop(state, execution_policy):
            pass
        return state.draft

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
                async for event in self._iter_action_loop(state, _SHORT_PATH_POLICY):
                    yield event
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
            # 4. 检索（可选；与记忆合并）
            if self.retriever is not None:
                yield AgentEvent("stage", "检索")
            await self._fill_retrieval(state)
            # 5. 行动
            yield AgentEvent("stage", "行动")
            async for event in self._iter_action_loop(state):
                yield event
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
                    async for event in self._iter_action_loop(state):
                        yield event
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
                state.answer = _failure_text(exc)
            yield AgentEvent("error", state.answer)

        yield AgentEvent("done", state.answer)
