"""LangGraph Supervisor 子编排（局部借用）。

本项目「行动」阶段默认由自研 `AgentPipeline` 承载（见 `app/agents/pipeline.py`）。
当配置 ``AGENT_ORCHESTRATION=langgraph`` 时，本模块在之上叠加一层 **LangGraph
Supervisor**：由 supervisor 节点决定「先调研」还是「直接撰写」，在多个 worker 之间
循环协作，直至产出最终回答。

设计边界（避免与自研路径耦合）：
- LangGraph 仅覆盖「多 Agent 协作」这一层，不重写五阶段管线；
- ``langgraph`` 为**可选依赖**，仅在真正构造 `SupervisorGraph` 时懒加载，
  import 失败会抛出带安装指引的 `ImportError`，不影响自研（self）路径；
- 检索在图执行之前由 ``_retrieve`` 统一做一次，调研 worker 只做一次模型调用、
  不进入行动循环；检索状态与披露要求与自研管线共用同一套契约。
"""

import json
import logging
from typing import TypedDict

from app.agents.pipeline import AgentEvent, AgentState
from app.agents.tools.base import ToolRegistry
from app.llm.base import ChatMessage, ChatRole, LLMOptions, LLMProvider
from app.rag.retrieval_status import RetrievalOutcome, RetrievalStatus

logger = logging.getLogger(__name__)

_WORKERS = ("research", "draft")
_ROUTES = (*_WORKERS, "FINISH")
# 流式回放的单次 token 事件字符数：过小会放大事件开销，过大则失去增量意义。
_STREAM_CHUNK = 24
_SUMMARY_LIMIT = 500
_EMPTY_SUMMARY = "没有文本结果"

_SUPERVISOR_SYSTEM = (
    "你是编排调度器。根据「用户目标」「调研记录」判断下一步动作。\n"
    "可选动作：\n"
    "- research：信息不足，需要调用工具/检索进一步调研；\n"
    "- draft：信息已充足，可以撰写最终回答；\n"
    "- FINISH：回答已成型，结束。\n"
    "只输出动作名（research / draft / FINISH），不要附加其它内容。"
)

_RESEARCH_SYSTEM = (
    "你是调研员。基于「用户目标」与已有调研，写一段精炼的调研记录。\n"
    "你没有工具可用，不要声称已经调用过工具或已经检索过知识库。"
)

_DRAFT_SYSTEM = (
    "你是撰写者。综合「用户目标」「调研记录」「已掌握上下文」，产出面向用户的最终回答。\n"
    "要求：直接回应用户、语言与用户一致、结构清晰易读。\n"
    "上下文中若没有知识库资料，不得声称结论来自知识库或公司内部制度。"
)


def _append_delegation(state: dict, name: str, result: str) -> list[dict[str, str]]:
    """复制已有分派记录再追加当前一条，避免后一轮覆盖前一轮。"""
    copied: list[dict[str, str]] = []
    for item in state.get("delegations") or []:
        copied.append({"name": str(item.get("name", "")), "result": str(item.get("result", ""))})
    copied.append({"name": name, "result": result})
    return copied


def _public_summary(result: str) -> str:
    """对外摘要先脱敏再截断。没有文本时用固定短句。"""
    raw = result or ""
    if not raw.strip():
        return _EMPTY_SUMMARY
    from app.security.log_sanitizer import LogSanitizer

    cleaned = LogSanitizer().sanitize(raw)
    if len(cleaned) > _SUMMARY_LIMIT:
        return cleaned[:_SUMMARY_LIMIT]
    return cleaned


class SupervisorState(TypedDict, total=False):
    """LangGraph 编排状态。

    仅承载「多 Agent 协作」所需的字段；与 `AgentState` 在边界处相互转换。
    """

    user_input: str
    plan: str
    context: str  # 检索/前期上下文
    research: str  # 调研 worker 累积的发现
    draft: str
    next: str
    revisions: int  # 已完成的调研轮数，用于收敛循环
    delegations: list[dict[str, str]]  # 本轮图执行内的分派记录，每次写回完整列表


class SupervisorGraph:
    """基于 LangGraph 的 Supervisor 子编排器。

    对外暴露与 `AgentPipeline` 一致的 `run(state)` / `run_stream(state)` 契约，
    以便 `chat_service` 按配置切换实现。
    """

    def __init__(
        self,
        llm: LLMProvider,
        options: LLMOptions | None = None,
        retriever=None,  # Retriever 协议；为 None 时不注入检索
        tools: ToolRegistry | None = None,
        max_revisions: int = 2,
    ) -> None:
        self.llm = llm
        self.options = options or LLMOptions()
        self.retriever = retriever
        self.tools = tools
        self.max_revisions = max_revisions
        self._last_outcome: RetrievalOutcome | None = None
        self._graph = self._build()

    # ── 懒加载 LangGraph ──
    @staticmethod
    def _require_langgraph():
        try:
            from langgraph.graph import END, StateGraph
        except ImportError as exc:  # 仅在真正需要时才暴露缺失依赖
            raise ImportError(
                "langgraph 未安装。Supervisor 子编排需要它；"
                '请运行 `pip install -e ".[langgraph]"` 后再启用 '
                "AGENT_ORCHESTRATION=langgraph。"
            ) from exc
        return END, StateGraph

    def _build(self):
        end, state_graph = self._require_langgraph()
        builder = state_graph(SupervisorState)
        builder.add_node("supervisor", self._node_supervisor)
        builder.add_node("research", self._node_research)
        builder.add_node("draft", self._node_draft)
        builder.add_edge("research", "supervisor")
        builder.add_edge("draft", end)
        builder.add_conditional_edges(
            "supervisor",
            self._route,
            {name: name for name in _WORKERS} | {"FINISH": end},
        )
        builder.set_entry_point("supervisor")
        return builder.compile()

    # ── 节点实现 ──
    async def _node_supervisor(self, state: dict) -> dict:
        user_input = state.get("user_input", "")
        plan = state.get("plan", "")
        research = state.get("research", "")
        prompt = (
            f"## 用户目标\n{user_input}\n\n"
            f"## 回答计划\n{plan}\n\n"
            f"## 调研记录\n{research or '（尚无调研记录）'}\n\n"
            "请输出下一步动作（research / draft / FINISH）。"
        )
        decision = await self.llm.chat(
            [
                ChatMessage(role=ChatRole.SYSTEM, content=_SUPERVISOR_SYSTEM),
                ChatMessage(role=ChatRole.USER, content=prompt),
            ],
            self.options,
        )
        action = decision.strip().split()[0] if decision.strip() else "draft"
        if action not in _ROUTES:
            action = "draft"
        return {"next": action}

    async def _node_research(self, state: dict) -> dict:
        """一轮调研只调用一次模型，不进入行动循环。"""
        prompt = (
            f"## 用户目标\n{state.get('user_input', '')}\n\n"
            f"## 已有调研\n{state.get('research') or '（尚无调研记录）'}\n\n"
            "请写一段调研记录。"
        )
        system = _RESEARCH_SYSTEM
        notice = state.get("retrieval_notice", "")
        if notice:
            system = f"{system}\n\n{notice}"
        draft = await self.llm.chat(
            [
                ChatMessage(role=ChatRole.SYSTEM, content=system),
                ChatMessage(role=ChatRole.USER, content=prompt),
            ],
            self.options,
        )
        text = (draft or "").strip()
        prior = state.get("research", "")
        updated = (prior + "\n" + text).strip() if prior else text
        return {
            "research": updated,
            "revisions": state.get("revisions", 0) + 1,
            "delegations": _append_delegation(state, "research", text),
        }

    async def _node_draft(self, state: dict) -> dict:
        user_input = state.get("user_input", "")
        plan = state.get("plan", "")
        research = state.get("research", "")
        context = state.get("context", "")
        prompt = (
            f"## 用户目标\n{user_input}\n\n"
            f"## 回答计划\n{plan}\n\n"
            f"## 已掌握上下文\n{context or '（无）'}\n\n"
            f"## 调研记录\n{research}\n\n"
            "请产出最终回答。"
        )
        system = _DRAFT_SYSTEM
        notice = state.get("retrieval_notice", "")
        if notice:
            system = f"{system}\n\n{notice}"
        answer = await self.llm.chat(
            [
                ChatMessage(role=ChatRole.SYSTEM, content=system),
                ChatMessage(role=ChatRole.USER, content=prompt),
            ],
            self.options,
        )
        text = answer.strip()
        return {
            "draft": text,
            "delegations": _append_delegation(state, "draft", text),
        }

    def _route(self, state: dict) -> str:
        """调研未达上限时才继续调研，其余一律进入一次撰写。

        撰写节点直接结束，不再回到调度，因此模型反复输出 draft 也只会撰写一次。
        """
        action = state.get("next", "draft")
        if action == "research" and state.get("revisions", 0) < self.max_revisions:
            return "research"
        if action == "research":
            logger.info("Supervisor 调研轮数已达上限 %s，强制转入撰写", self.max_revisions)
        return "draft"

    # ── 对外契约（与 AgentPipeline 对齐）──
    async def _retrieve(self, state: AgentState) -> None:
        """图执行前先检索一次，与自研管线「检索在协作之前」的顺序一致。

        Supervisor 此前只保存 retriever 却从不调用，导致 MULTI 路由下知识问答
        既不检索、也不产生任何检索状态，模型可以凭自身知识冒充知识库结论。
        """
        if self.retriever is None:
            return
        snippet = ""
        outcome: RetrievalOutcome | None = None
        try:
            snippet = await self.retriever.retrieve(state.user_input, state.plan)
        except Exception:  # noqa: BLE001 — 检索故障不应变成编排级通用失败
            logger.exception("Supervisor 检索失败，收敛为 unavailable")
            outcome = RetrievalOutcome(status=RetrievalStatus.UNAVAILABLE)
        else:
            last = getattr(self.retriever, "last_outcome", None)
            outcome = last if isinstance(last, RetrievalOutcome) else None
        if outcome is None:
            return
        self._last_outcome = outcome
        state.retrieval_status = outcome.status.value
        state.retrieval_notice = outcome.directive()
        state.retrieval_disclosure = outcome.disclosure_prefix()
        if snippet.strip():
            state.context = (
                f"{state.context}\n\n## 知识库检索结果\n{snippet}" if state.context else snippet
            )

    async def run(self, state: AgentState) -> AgentState:
        """以 Supervisor 方式执行一次协作，填充并返回 AgentState。"""
        await self._retrieve(state)
        # 全低分是终态拒答：不进图，直接给出拒答语。
        if self._last_outcome is not None:
            terminal = self._last_outcome.terminal_reply()
            if terminal is not None:
                state.answer = terminal
                state.draft = terminal
                return state
        graph_state = {
            "user_input": state.user_input,
            "plan": state.plan,
            "context": state.context,
            "research": "",
            "draft": "",
            "next": "research",
            "revisions": 0,
            "delegations": [],
            # 检索非 ok 时给模型的披露要求。必须走 system 侧：放进「上下文」
            # 会被当成资料，而资料可能被模型当不可信文本忽略掉。
            "retrieval_notice": state.retrieval_notice,
        }
        try:
            final = await self._graph.ainvoke(graph_state)
        except Exception as exc:  # noqa: BLE001 — 编排级兜底
            logger.exception("Supervisor 编排执行失败")
            state.error = str(exc)
            if not state.answer:
                state.answer = "抱歉，多 Agent 协作处理时出现问题，请稍后重试。"
            return state
        state.draft = final.get("draft", "")
        state.answer = state.draft
        # 披露语由应用层确定性前置：不依赖模型服从系统提示。
        if state.retrieval_disclosure:
            state.answer = f"{state.retrieval_disclosure}\n\n{state.draft}"
        state.delegations = [
            {"name": str(item.get("name", "")), "result": str(item.get("result", ""))}
            for item in (final.get("delegations") or [])
        ]
        return state

    async def run_stream(self, state: AgentState):
        """流式执行：以 AgentEvent 广播进度，再分块回放正文 token。

        先由 Supervisor 编排出完整草稿，再按固定长度切分为多个 token 事件回放。
        若整段答案只发一个 token 事件，客户端无法增量渲染、也失去流式的意义。
        逐节点真正的流式可后续改为 ``graph.astream_events``。
        """
        result = await self.run(state)
        yield AgentEvent("stage", "Supervisor 协作")
        # 图已经跑完。这里发出的是完成后的摘要，不是节点开始时的进度。
        if not result.error:
            for item in result.delegations:
                payload = {
                    "v": 1,
                    "name": item["name"],
                    "status": "done",
                    "summary": _public_summary(item["result"]),
                }
                yield AgentEvent("subtask", json.dumps(payload, ensure_ascii=False))
        answer = result.answer or ""
        for start in range(0, len(answer), _STREAM_CHUNK):
            yield AgentEvent("token", answer[start : start + _STREAM_CHUNK])
        yield AgentEvent("done", answer)
