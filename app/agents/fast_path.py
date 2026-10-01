"""简单问题、点名工具和单次检索的短路径。

这些路径不进入 Supervisor，也不跑理解、规划、反思。
"""

import logging
from collections.abc import AsyncIterator

from app.agents.pipeline import AgentEvent, AgentPipeline, AgentState, ExecutionPolicy
from app.agents.route import ChatRoute, RouteKind
from app.agents.tools.base import ToolRegistry
from app.llm.base import ChatMessage, ChatRole, LLMOptions, LLMProvider

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
        dialogue = "\n".join(f"{_role_name(item.role)}：{item.content}" for item in recent)
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
) -> AsyncIterator[AgentEvent]:
    yield AgentEvent("stage", "响应")
    messages = [
        ChatMessage(role=ChatRole.SYSTEM, content=system),
        ChatMessage(role=ChatRole.USER, content=user),
    ]
    parts: list[str] = []
    async for delta in llm.stream_chat(messages, options):
        if not delta:
            continue
        parts.append(delta)
        yield AgentEvent("token", delta)
    state.answer = "".join(parts)
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
) -> AsyncIterator[AgentEvent]:
    """按已决定的短路径产出事件，并把最终文本写进 state.answer。"""
    if route.kind is RouteKind.TOOLS and route.tool_names:
        pipeline = AgentPipeline(llm, options, retriever=None, tools=tools)
        pipeline.max_tool_rounds = _TOOL_ROUND_CAP
        pipeline.skill_prompt_injection = skill_prompt
        policy = ExecutionPolicy(allowed_tool_names=route.tool_names)
        yield AgentEvent("stage", "行动")
        async for event in pipeline._iter_action_loop(state, policy):
            yield event
        state.answer = state.draft or ""
        yield AgentEvent("stage", "响应")
        for piece in _chunk_tokens(state.answer):
            yield AgentEvent("token", piece)
        yield AgentEvent("done", state.answer)
        return

    extra = ""
    if route.kind is RouteKind.RAG:
        yield AgentEvent("stage", "检索")
        snippet = ""
        if retriever is not None:
            try:
                snippet = await retriever.retrieve(state.user_input, state.plan or "")
            except Exception:
                logger.exception("检索失败，改为直接回答")
                snippet = ""
        if (snippet or "").strip():
            extra = f"## 知识库\n{snippet}"
        else:
            extra = "## 知识库\n没有可用的检索结果。"

    async for event in _stream_reply(
        llm,
        options,
        state,
        _system(_DIRECT_SYSTEM, skill_prompt),
        _user_block(state, extra),
    ):
        yield event
