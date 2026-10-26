"""简单问题、点名工具和单次检索的短路径。

这些路径不进入 Supervisor，也不跑理解、规划、反思。
"""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from contextlib import aclosing

from app.agents.pipeline import (
    AgentEvent,
    AgentPipeline,
    AgentState,
    ExecutionPolicy,
    _failure_text,
    error_metadata,
    node_event,
    protocol_event,
)
from app.agents.route import ChatRoute, RouteKind
from app.agents.skills.base import SkillBindingRejected, SkillRuntime
from app.agents.tools.base import ToolRegistry
from app.agents.tools.executor import ToolExecutor
from app.debug.trace import AgentTrace
from app.llm.base import ChatMessage, ChatRole, LLMOptions, LLMProvider, content_text, user_message_content
from app.rag.context_builder import CONTEXT_BUDGET_EXHAUSTED
from app.rag.retrieval_status import RetrievalOutcome, RetrievalStatus
from app.rag.retriever import assemble_retrieval

logger = logging.getLogger(__name__)

_DIRECT_SYSTEM = (
    "你是企业助手。直接回答用户最新消息。"
    "不要调用工具，不要声称已经检索过知识库。"
    "使用与用户相同的语言。"
)
_TOOL_ROUND_CAP = 2
_TOKEN_CHUNK = 24


def _system(base: str, skill_prompt: str) -> str:
    if not skill_prompt:
        return base
    return f"{base}\n\n---\n# 激活的技能指令\n{skill_prompt}"


def _role_name(role: object) -> str:
    return str(getattr(role, "value", role))


def _user_block(state: AgentState, extra: str = "") -> str:
    parts: list[str] = []
    if state.context:
        parts.append(f"## 已有上下文\n{state.context}")
    if state.history:
        recent = state.history[-6:]
        dialogue = "\n".join(f"{_role_name(item.role)}：{content_text(item.content)}" for item in recent)
        parts.append(f"## 最近对话\n{dialogue}")
    if extra:
        parts.append(extra)
    parts.append(f"## 用户最新消息\n{state.user_input}")
    return "\n\n".join(parts)


async def _stream_reply(
    llm: LLMProvider,
    options: LLMOptions,
    state: AgentState,
    system: str,
    user: str,
    trace: AgentTrace | None = None,
) -> AsyncGenerator[AgentEvent, None]:
    yield AgentEvent("stage", "响应")
    messages = [
        ChatMessage(role=ChatRole.SYSTEM, content=system),
        ChatMessage(role=ChatRole.USER, content=user_message_content(user, state.user_images)),
    ]
    parts: list[str] = []
    # 披露语先吐给用户：即使模型完全忽略 system 里的指令，
    # 用户看到的第一句话也是确定的。
    if state.retrieval_disclosure:
        for piece in _chunk_tokens(state.retrieval_disclosure + "\n\n"):
            yield AgentEvent("token", piece)
    response = llm.stream_chat(messages, options)
    started = time.monotonic()
    try:
        async for delta in response:
            if not delta:
                continue
            parts.append(delta)
            yield AgentEvent("token", delta)
    finally:
        try:
            close_response = getattr(response, "aclose", None)
            if close_response is not None:
                await close_response()
        finally:
            if trace is not None:
                trace.llm_call(
                    getattr(llm, "model", "unknown"),
                    latency_ms=(time.monotonic() - started) * 1000,
                    stage="fast.respond",
                )
    body = "".join(parts).strip()
    state.answer = f"{state.retrieval_disclosure}\n\n{body}" if state.retrieval_disclosure else body
    state.draft = state.answer
    yield AgentEvent("done", state.answer)


def _chunk_tokens(text: str) -> list[str]:
    if not text:
        return []
    return [text[index : index + _TOKEN_CHUNK] for index in range(0, len(text), _TOKEN_CHUNK)]


async def iter_fast_path(
    llm: LLMProvider,
    options: LLMOptions,
    state: AgentState,
    route: ChatRoute,
    retriever,
    tools: ToolRegistry | None,
    skill_prompt: str = "",
    skill_runtime: SkillRuntime | None = None,
    trace: AgentTrace | None = None,
) -> AsyncGenerator[AgentEvent, None]:
    """短路路径只报告实际执行的节点，不伪造被省略的规划/反思。"""
    yield protocol_event("intent.classified", intent=route.kind.value, shortcut=True)
    current: str | None = None
    names = {"行动": "action", "检索": "retrieval", "响应": "respond"}
    try:
        async with aclosing(_iter_fast_path_events(
                llm, options, state, route, retriever, tools, skill_prompt, skill_runtime, trace)) as events:
            async for event in events:
                if event.type == "stage":
                    if current is not None:
                        yield node_event("node.finished", current, status="done")
                    current = names.get(event.data, event.data)
                    yield node_event("node.started", current)
                elif event.type == "done" and current is not None:
                    yield node_event("node.finished", current, status="done")
                    current = None
                yield event
    except asyncio.CancelledError:
        if current is not None:
            yield node_event("node.finished", current, status="cancelled")
        raise
    except SkillBindingRejected:
        raise
    except Exception as exc:
        if current is not None:
            yield node_event("node.finished", current, status="failed")
        state.error = type(exc).__name__
        state.answer = _failure_text(exc)
        yield AgentEvent("error", state.answer, metadata=error_metadata(exc))
        yield AgentEvent("done", state.answer)


async def _iter_fast_path_events(
    llm: LLMProvider,
    options: LLMOptions,
    state: AgentState,
    route: ChatRoute,
    retriever,
    tools: ToolRegistry | None,
    skill_prompt: str = "",
    skill_runtime: SkillRuntime | None = None,
    trace: AgentTrace | None = None,
) -> AsyncGenerator[AgentEvent, None]:
    """按已决定的短路径产出事件，并把最终文本写进 state.answer。"""
    if route.kind is RouteKind.TOOLS and route.tool_names:
        pipeline = AgentPipeline(llm, options, retriever=None, tools=tools, trace=trace)
        if tools is not None:
            pipeline.executor = ToolExecutor(tools)
        pipeline.max_tool_rounds = _TOOL_ROUND_CAP
        pipeline.skill_prompt_injection = skill_prompt
        pipeline.skill_runtime = skill_runtime
        pipeline.skill_path = "fast.tools"
        policy = ExecutionPolicy(allowed_tool_names=route.tool_names)
        yield AgentEvent("stage", "行动")
        async with aclosing(pipeline._iter_action_loop(state, policy)) as action_events:
            async for event in action_events:
                yield event
        state.answer = state.draft or ""
        if skill_runtime:
            available = [t.name for t in tools.all() if t.name in route.tool_names] if tools else []
            note = skill_runtime.notice("fast.tools", available)
            if note:
                state.answer = f"{note}\n\n{state.answer}"
                state.draft = state.answer
        yield AgentEvent("stage", "响应")
        for piece in _chunk_tokens(state.answer):
            yield AgentEvent("token", piece)
        yield AgentEvent("done", state.answer)
        return

    extra = ""
    notice = ""
    disclosure = ""
    terminal = None
    if route.kind is RouteKind.RAG:
        yield AgentEvent("stage", "检索")
        snippet = ""
        rag_text = ""
        outcome: RetrievalOutcome | None = None
        if retriever is not None:
            try:
                query = state.retrieval_query if state.retrieval_query is not None else state.user_input
                snippet = await retriever.retrieve(query, state.plan or "")
            except Exception:  # noqa: BLE001 — 检索故障不应中断对话
                logger.exception("短路径检索失败")
                snippet = ""
                # 检索器自身没有收敛异常（不是 HybridRetriever）。这里必须判为
                # unavailable，不能让「没跑成」退化成「查过了、没有」。
                outcome = RetrievalOutcome(status=RetrievalStatus.UNAVAILABLE)
            else:
                last = getattr(retriever, "last_outcome", None)
                outcome = last if isinstance(last, RetrievalOutcome) else None
            # RAG-029：检索结果必须过一遍按块预算的 Context Builder，不能把
            # retrieve() 的原始字符串整段塞进提示词。短路径是知识库问答的默认
            # 路径，绕过它等于预算与「来源 == 实际证据」在这条路径上全部失效。
            # unavailable 时不信任残留 last_hits（P2-01）：走记忆-only 降级并清空来源。
            evidence_trusted = not (
                outcome is not None and outcome.status == RetrievalStatus.UNAVAILABLE
            )
            rag_text = assemble_retrieval(
                retriever, snippet, trust_structured=evidence_trusted
            ).text
            if outcome is not None:
                state.retrieval_status = outcome.status.value
                # 检索状态要求是给模型的指令，必须进 system，不能塞进
                # 「知识库」段里当资料——资料会被当成不可信文本而可能被忽略。
                notice = outcome.directive()
                disclosure = outcome.disclosure_prefix()
                state.retrieval_disclosure = disclosure
                terminal = outcome.terminal_reply()
        logger.info(
            "rag_fast_path_retrieval status=%s",
            state.retrieval_status or "unknown",
        )
        # 全低分：拒答语本身就是结论，直接吐给用户，不交给模型改写。
        if terminal is not None:
            note = skill_runtime.notice(route.kind.value) if skill_runtime else ""
            if note:
                terminal = f"{note}\n\n{terminal}"
            state.answer = terminal
            state.draft = terminal
            for piece in _chunk_tokens(terminal):
                yield AgentEvent("token", piece)
            yield AgentEvent("done", terminal)
            return
        # 检索失败（unavailable）不能写成「没有可用的检索结果」：
        # 那是把「没跑成」伪装成「查过了、没有」，用户看不出区别。
        if (rag_text or "").strip():
            extra = f"## 知识库\n{rag_text}"
        elif (snippet or "").strip():
            # 检索有结果，但没有一块完整装进预算。这不是「没有资料」，
            # 说成「没有可用的检索结果」会让用户以为知识库里本来就没有。
            extra = f"## 知识库\n{CONTEXT_BUDGET_EXHAUSTED}"
        elif state.retrieval_status == "unavailable":
            extra = "## 知识库\n本次检索未完成，没有取得任何知识库资料。"
        else:
            extra = "## 知识库\n没有可用的检索结果。"

    if skill_runtime:
        note = skill_runtime.notice(route.kind.value)
        if note:
            state.retrieval_disclosure = "\n\n".join(filter(None, [note, state.retrieval_disclosure]))
    system = _DIRECT_SYSTEM + ("\n\n" + notice if notice else "")
    system = skill_runtime.prompt(system, route.kind.value) if skill_runtime else _system(system, skill_prompt)
    async with aclosing(_stream_reply(
        llm,
        options,
        state,
        system,
        _user_block(state, extra),
        trace,
    )) as reply_events:
        async for event in reply_events:
            yield event
