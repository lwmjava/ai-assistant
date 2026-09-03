"""LangGraph Supervisor 子编排（局部借用）。

本项目「行动」阶段默认由自研 `AgentPipeline` 承载（见 `app/agents/pipeline.py`）。
当配置 ``AGENT_ORCHESTRATION=langgraph`` 时，本模块在之上叠加一层 **LangGraph
Supervisor**：由 supervisor 节点决定「先调研」还是「直接撰写」，在多个 worker 之间
循环协作，直至产出最终回答。

设计边界（避免与自研路径耦合）：
- LangGraph 仅覆盖「多 Agent 协作」这一层，不重写五阶段管线；
- ``langgraph`` 为**可选依赖**，仅在真正构造 `SupervisorGraph` 时懒加载，
  import 失败会抛出带安装指引的 `ImportError`，不影响自研（self）路径；
- 调研 worker 直接复用自研 `AgentPipeline` 已有的「行动」工具循环（含检索与工具），
  避免在 LangGraph 侧再实现一遍，杜绝重复造轮子。
"""

import logging
from typing import Optional, TypedDict

from app.agents.pipeline import AgentEvent, AgentPipeline, AgentState
from app.agents.tools.base import ToolRegistry
from app.llm.base import ChatMessage, ChatRole, LLMOptions, LLMProvider

logger = logging.getLogger(__name__)

_WORKERS = ("research", "draft")
_ROUTES = (*_WORKERS, "FINISH")
# 流式回放的单次 token 事件字符数：过小会放大事件开销，过大则失去增量意义。
_STREAM_CHUNK = 24

_SUPERVISOR_SYSTEM = (
    "你是编排调度器。根据「用户目标」「调研记录」判断下一步动作。\n"
    "可选动作：\n"
    "- research：信息不足，需要调用工具/检索进一步调研；\n"
    "- draft：信息已充足，可以撰写最终回答；\n"
    "- FINISH：回答已成型，结束。\n"
    "只输出动作名（research / draft / FINISH），不要附加其它内容。"
)

_RESEARCH_SYSTEM = (
    "你是调研员。基于「用户目标」与「已掌握上下文」，调用可用工具/检索补齐关键信息。\n"
    "输出一段精炼的调研记录（事实与出处），供后续撰写使用。"
)

_DRAFT_SYSTEM = (
    "你是撰写者。综合「用户目标」「调研记录」「已掌握上下文」，产出面向用户的最终回答。\n"
    "要求：直接回应用户、语言与用户一致、结构清晰易读。"
)


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


class SupervisorGraph:
    """基于 LangGraph 的 Supervisor 子编排器。

    对外暴露与 `AgentPipeline` 一致的 `run(state)` / `run_stream(state)` 契约，
    以便 `chat_service` 按配置切换实现。
    """

    def __init__(
        self,
        llm: LLMProvider,
        options: Optional[LLMOptions] = None,
        retriever=None,  # Retriever 协议；为 None 时不注入检索
        tools: Optional[ToolRegistry] = None,
        max_revisions: int = 2,
    ) -> None:
        self.llm = llm
        self.options = options or LLMOptions()
        self.retriever = retriever
        self.tools = tools
        self.max_revisions = max_revisions
        # 复用自研管线的「行动」工具循环作为调研 worker，避免重复实现
        self._pipeline = AgentPipeline(llm, options, retriever, tools)
        self._graph = self._build()

    # ── 懒加载 LangGraph ──
    @staticmethod
    def _require_langgraph():
        try:
            from langgraph.graph import END, StateGraph
        except ImportError as exc:  # 仅在真正需要时才暴露缺失依赖
            raise ImportError(
                "langgraph 未安装。Supervisor 子编排需要它；"
                "请运行 `pip install \"ai-assistant[langgraph]\"` 后再启用 "
                "AGENT_ORCHESTRATION=langgraph。"
            ) from exc
        return END, StateGraph

    def _build(self):
        END, StateGraph = self._require_langgraph()
        builder = StateGraph(SupervisorState)
        builder.add_node("supervisor", self._node_supervisor)
        builder.add_node("research", self._node_research)
        builder.add_node("draft", self._node_draft)
        builder.add_edge("research", "supervisor")
        builder.add_edge("draft", "supervisor")
        builder.add_conditional_edges(
            "supervisor",
            self._route,
            {name: name for name in _WORKERS} | {"FINISH": END},
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
        # 复用自研「行动」工具循环产出调研记录（含工具/检索），而非在 LangGraph 侧重做
        agent_state = AgentState(
            user_input=state.get("user_input", ""),
            plan=state.get("plan", ""),
            context=state.get("context", ""),
        )
        draft = await self._pipeline._run_action_loop(agent_state)
        prior = state.get("research", "")
        updated = (prior + "\n" + draft).strip() if prior else draft
        return {"research": updated, "revisions": state.get("revisions", 0) + 1}

    async def _node_draft(self, state: dict) -> dict:
        user_input = state.get("user_input", "")
        plan = state.get("plan", "")
        research = state.get("research", "")
        prompt = (
            f"## 用户目标\n{user_input}\n\n"
            f"## 回答计划\n{plan}\n\n"
            f"## 调研记录\n{research}\n\n"
            "请产出最终回答。"
        )
        answer = await self.llm.chat(
            [
                ChatMessage(role=ChatRole.SYSTEM, content=_DRAFT_SYSTEM),
                ChatMessage(role=ChatRole.USER, content=prompt),
            ],
            self.options,
        )
        return {"draft": answer.strip()}

    def _route(self, state: dict) -> str:
        """决定下一步节点，并在调研轮数用尽时强制收敛。

        supervisor 与 research 构成回环，若模型持续输出 ``research`` 会无限循环，
        因此达到 ``max_revisions`` 后不再调研，直接转撰写。
        """
        action = state.get("next", "draft")
        if action == "research" and state.get("revisions", 0) >= self.max_revisions:
            logger.info(
                "Supervisor 调研轮数已达上限 %s，强制转入撰写", self.max_revisions
            )
            return "draft"
        return action

    # ── 对外契约（与 AgentPipeline 对齐）──
    async def run(self, state: AgentState) -> AgentState:
        """以 Supervisor 方式执行一次协作，填充并返回 AgentState。"""
        graph_state = {
            "user_input": state.user_input,
            "plan": state.plan,
            "context": state.context,
            "research": "",
            "draft": "",
            "next": "research",
            "revisions": 0,
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
        return state

    async def run_stream(self, state: AgentState):
        """流式执行：以 AgentEvent 广播进度，再分块回放正文 token。

        先由 Supervisor 编排出完整草稿，再按固定长度切分为多个 token 事件回放。
        若整段答案只发一个 token 事件，客户端无法增量渲染、也失去流式的意义。
        逐节点真正的流式可后续改为 ``graph.astream_events``。
        """
        result = await self.run(state)
        yield AgentEvent("stage", "Supervisor 协作")
        answer = result.answer or ""
        for start in range(0, len(answer), _STREAM_CHUNK):
            yield AgentEvent("token", answer[start : start + _STREAM_CHUNK])
        yield AgentEvent("done", answer)
