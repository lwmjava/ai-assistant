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
from app.llm.base import LLMOptions, LLMProvider

logger = logging.getLogger(__name__)

_WORKERS = ("research", "draft")
_ROUTES = (*_WORKERS, "FINISH")

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
                {"role": "system", "content": _SUPERVISOR_SYSTEM},
                {"role": "user", "content": prompt},
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
        return {"research": updated}

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
                {"role": "system", "content": _DRAFT_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            self.options,
        )
        return {"draft": answer.strip()}

    @staticmethod
    def _route(state: dict) -> str:
        return state.get("next", "draft")

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
        """流式执行：以 AgentEvent 广播进度，最终吐出 token。"""
        # LangGraph 侧的逐节点流式可后续改为 graph.astream_events，
        # 这里先以「先编排出草稿，再逐字回放」保持与管线一致的事件协议。
        result = await self.run(state)
        yield AgentEvent("stage", "Supervisor 协作")
        if result.answer:
            yield AgentEvent("token", result.answer)
        yield AgentEvent("done", result.answer)
