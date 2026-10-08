"""RAG-029 默认 AgentPipeline 与共享组装入口对齐（2026-10-08 补修）。

2026-10-08 独立复审发现默认管线仍维护第二份 list-only 组装，与 ``assemble_retrieval``
漂移，产生三条阻断反例（H-01/H-02/H-03）。本文件把它们钉成正式回归：

- H-01：``last_hits`` 为 tuple（整块证据超预算）必须整块丢弃，不得把半块塞进上下文；
- H-02：结构化命中非空但一块都装不下时，模型指令与最终同步/流式回复都必须
  确定性披露「超预算」，不得伪装成「检索无命中」；
- H-03：降级路径必须清空上一轮残留的 ``last_selected``，否则对外来源会越轮泄露。

测试用真实默认入口 ``AgentPipeline._fill_retrieval``（及完整 ``run`` / ``run_stream``），
不直接调 ``build_context``——否则守护不到管线这第二份组装逻辑。
"""

from __future__ import annotations

import pytest

from app.agents.pipeline import AgentPipeline, AgentState
from app.core.config import settings
from app.rag.context_builder import CONTEXT_BUDGET_EXHAUSTED
from app.rag.retriever import format_context
from app.rag.vectorstore.base import ChunkResult


def _hit(cid: str, content: str, source: str = "手册.pdf") -> ChunkResult:
    return ChunkResult(
        id=cid,
        content=content,
        source=source,
        document_id="d1",
        score=0.5,
        similarity=0.9,
    )


class _ProtocolRetriever:
    """按协议返回整段字符串、并以 ``last_hits`` 暴露结构化命中的可复用 Retriever。

    可复用意味着跨请求持有 ``last_selected``：上一轮若留下旧值，本轮降级后必须被
    清空，否则 ``ChatService._reply_sources`` 会把它当成本轮来源（H-03）。
    """

    def __init__(self, hits) -> None:
        self.last_hits = hits
        self.last_selected: list[ChunkResult] | None = None

    async def retrieve(self, query: str, plan: str) -> str:
        return format_context(list(self.last_hits))


class _EchoLLM:
    """跑通完整管线用的假 LLM：任意阶段都回一句固定草稿。"""

    model = "echo"

    async def chat(self, messages, options=None, **_kw) -> str:
        # 协议约定 chat 返回 str（见 app.llm.base.LLMProvider）；
        # preflight 会对返回值做字符串解析，返回 ChatMessage 会触发 AttributeError。
        return "最终回答正文"

    async def stream_chat(self, messages, options=None, **_kw):
        yield "最终回答正文"


# ── H-01：tuple 命中整块丢弃，不得出现半块 ────────────────────────


@pytest.mark.asyncio
async def test_pipeline_tuple_hits_dropped_whole_not_truncated(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RAG_CONTEXT_CHARS", 300)
    block = "START" + "X" * 500 + "TAIL"
    retriever = _ProtocolRetriever((_hit("c1", block),))
    state = AgentState(user_input="问题", history=[])
    await AgentPipeline(object(), retriever=retriever)._fill_retrieval(state)

    # 整块超预算：上下文里不得出现任何半截证据（既没有 [资料 1]，也没有 TAIL 缺失的块）
    assert "[资料 1]" not in state.context, repr(state.context)
    assert "TAIL" not in state.context
    assert "START" not in state.context
    assert retriever.last_selected == [], repr(retriever.last_selected)


# ── H-02：命中非空但一块都装不下时，模型指令 + 最终回复都披露超预算 ──


@pytest.mark.asyncio
async def test_pipeline_budget_exhausted_disclosed_to_model(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RAG_CONTEXT_CHARS", 300)
    retriever = _ProtocolRetriever([_hit("c1", "X" * 500)])
    state = AgentState(user_input="问题", history=[])
    await AgentPipeline(object(), retriever=retriever)._fill_retrieval(state)

    assert retriever.last_selected == []
    disclosed = state.context + state.retrieval_notice + state.retrieval_disclosure
    assert CONTEXT_BUDGET_EXHAUSTED in disclosed, repr(disclosed)


@pytest.mark.asyncio
async def test_pipeline_budget_exhausted_in_final_sync_reply(monkeypatch) -> None:
    """同步 ``run`` 的最终回答必须确定性前置超预算披露。"""
    monkeypatch.setattr(settings, "RAG_CONTEXT_CHARS", 300)
    retriever = _ProtocolRetriever([_hit("c1", "X" * 500)])
    pipeline = AgentPipeline(_EchoLLM(), retriever=retriever)
    state = AgentState(user_input="问题", history=[])
    await pipeline.run(state)

    assert retriever.last_selected == []
    assert CONTEXT_BUDGET_EXHAUSTED in state.answer, repr(state.answer)


@pytest.mark.asyncio
async def test_pipeline_budget_exhausted_in_final_stream_reply(monkeypatch) -> None:
    """流式 ``run_stream`` 的 token 序列里必须先吐出超预算披露。"""
    monkeypatch.setattr(settings, "RAG_CONTEXT_CHARS", 300)
    retriever = _ProtocolRetriever([_hit("c1", "X" * 500)])
    pipeline = AgentPipeline(_EchoLLM(), retriever=retriever)
    state = AgentState(user_input="问题", history=[])
    tokens: list[str] = []
    async for event in pipeline.run_stream(state):
        if event.type == "token":
            tokens.append(event.data)

    assert retriever.last_selected == []
    streamed = "".join(tokens)
    assert CONTEXT_BUDGET_EXHAUSTED in streamed, repr(streamed)
    # 披露语必须在回答正文之前（确定性前置）
    assert streamed.index(CONTEXT_BUDGET_EXHAUSTED) < streamed.index("最终回答正文")


# ── H-03：降级路径清空上一轮残留的 selected ──────────────────────


@pytest.mark.asyncio
async def test_pipeline_degraded_resets_stale_selected(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RAG_CONTEXT_CHARS", 300)
    retriever = _ProtocolRetriever([])
    retriever.last_selected = [_hit("old", "上一轮残留证据")]
    state = AgentState(user_input="问题", history=[])
    await AgentPipeline(object(), retriever=retriever)._fill_retrieval(state)

    assert retriever.last_selected == [], repr(retriever.last_selected)


# ── 失败边界清单（2026-10-08 补修）：五类各至少一条反例 ──────────


class _FakeSession:
    def get(self, *_args, **_kw):
        return None


def _sources(retriever) -> list[str]:
    from app.services.chat_service import ChatService

    return [s["chunk_id"] for s in ChatService._reply_sources(_FakeSession(), retriever)]


# ── 类别 1：数值边界（极小预算 / 首块超预算 / 空命中） ──────────────


@pytest.mark.asyncio
async def test_pipeline_tiny_budget_room_zero_drops_and_discloses(monkeypatch) -> None:
    """预算极小（只够前导说明、装不下任何块）：整块丢弃并明确披露超预算。"""
    from app.rag.context_builder import _FENCE_OVERHEAD

    monkeypatch.setattr(settings, "RAG_CONTEXT_CHARS", _FENCE_OVERHEAD)
    retriever = _ProtocolRetriever([_hit("c1", "任意一块证据")])
    state = AgentState(user_input="问题", history=[])
    await AgentPipeline(object(), retriever=retriever)._fill_retrieval(state)

    assert retriever.last_selected == []
    assert "[资料 1]" not in state.context
    assert CONTEXT_BUDGET_EXHAUSTED in state.retrieval_disclosure
    assert _sources(retriever) == []


@pytest.mark.asyncio
async def test_pipeline_empty_hits_is_not_budget_exhausted(monkeypatch) -> None:
    """真·零命中不是「超预算」：不得套用超预算披露，也不得残留来源。"""
    monkeypatch.setattr(settings, "RAG_CONTEXT_CHARS", 300)
    retriever = _ProtocolRetriever([])
    state = AgentState(user_input="问题", history=[])
    await AgentPipeline(object(), retriever=retriever)._fill_retrieval(state)

    assert retriever.last_selected == []
    assert CONTEXT_BUDGET_EXHAUSTED not in state.context
    assert CONTEXT_BUDGET_EXHAUSTED not in state.retrieval_disclosure
    assert _sources(retriever) == []


# ── 类别 2：失败路径（检索异常收敛 unavailable / 字符串降级 / 同步流式） ──


class _RaisingRetriever:
    """retrieve 抛异常的自定义检索器：管线必须收敛为 unavailable，不向上炸。"""

    def __init__(self) -> None:
        self.last_hits: list = []
        self.last_selected: list | None = None

    async def retrieve(self, query: str, plan: str) -> str:
        raise RuntimeError("向量库连接中断")


@pytest.mark.asyncio
async def test_pipeline_retriever_exception_converges_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RAG_CONTEXT_CHARS", 300)
    retriever = _RaisingRetriever()  # retrieve 抛异常，且不带任何 last_hits
    state = AgentState(user_input="问题", history=[])
    await AgentPipeline(object(), retriever=retriever)._fill_retrieval(state)

    assert state.retrieval_status == "unavailable"
    # 异常路径清空 selected，不得用 stale last_hits 冒充来源
    assert retriever.last_selected == []
    assert _sources(retriever) == []
    # unavailable 已有自己的披露，不被「超预算」覆盖
    assert CONTEXT_BUDGET_EXHAUSTED not in state.context
    assert CONTEXT_BUDGET_EXHAUSTED not in state.retrieval_disclosure


@pytest.mark.asyncio
async def test_pipeline_string_only_retriever_fence_closed_empty_sources(monkeypatch) -> None:
    """只有字符串的自定义 Retriever：围栏闭合、来源声明为空（不夸大）。"""
    from app.rag.context_builder import FENCE_CLOSE
    from app.rag.retriever import format_context

    monkeypatch.setattr(settings, "RAG_CONTEXT_CHARS", 6000)

    class _StringRetriever:
        def __init__(self, text: str) -> None:
            self.text = text
            self.last_selected: list | None = None

        async def retrieve(self, query: str, plan: str) -> str:
            return self.text

    retriever = _StringRetriever(format_context([_hit("c1", "短资料")]))
    state = AgentState(user_input="问题", history=[])
    await AgentPipeline(object(), retriever=retriever)._fill_retrieval(state)

    assert state.context.rstrip().endswith(FENCE_CLOSE)
    assert retriever.last_selected == []
    assert _sources(retriever) == []


# ── 类别 3：权限与租户（来源不越租户泄露，只反映本次实际选入） ──────


@pytest.mark.asyncio
async def test_pipeline_sources_only_selected_no_other_tenant_leak(monkeypatch) -> None:
    """预算挤掉的块（哪怕来自其它文档/租户）不得出现在对外来源里。"""
    monkeypatch.setattr(settings, "RAG_CONTEXT_CHARS", 300)
    hits = [_hit("mine", "本租户选入块。" * 10), _hit("other", "他租户块。" * 60)]
    retriever = _ProtocolRetriever(hits)
    state = AgentState(user_input="问题", history=[])
    await AgentPipeline(object(), retriever=retriever)._fill_retrieval(state)

    ids = [h.id for h in retriever.last_selected]
    assert ids == ["mine"]  # 只选入第一块，第二块超预算被挤掉
    assert _sources(retriever) == ["mine"]
    assert "other" not in _sources(retriever)


# ── 类别 4：身份与绑定（[资料 N] ↔ selected ↔ 来源三方一致） ──────


@pytest.mark.asyncio
async def test_pipeline_marker_selected_sources_three_way_consistent(monkeypatch) -> None:
    """部分选入时：上下文里的 [资料 N]、last_selected、对外来源三者严格一致。"""
    import re

    from app.rag.retriever import HybridRetriever

    marker = re.compile(r"\[资料 (\d+)\]")
    monkeypatch.setattr(settings, "RAG_CONTEXT_CHARS", 500)
    hits = [_hit(f"c{i}", "资料正文。" * 30) for i in range(1, 5)]

    class _Backend:
        async def retrieve(self, query, *, tenant_id, top_k, read_scope=None):
            return hits

    retriever = HybridRetriever(_Backend(), tenant_id="t", top_k=5)
    state = AgentState(user_input="问题", history=[])
    await AgentPipeline(object(), retriever=retriever)._fill_retrieval(state)

    selected_ids = [h.id for h in retriever.last_selected]
    markers = [int(n) for n in marker.findall(state.context)]
    assert markers == list(range(1, len(selected_ids) + 1))
    assert selected_ids == [f"c{i}" for i in range(1, len(selected_ids) + 1)]
    assert _sources(retriever) == selected_ids


# ── 类别 5：回滚与幂等（降级/失败后旧 selected 不残留，重试无重复副作用） ──


@pytest.mark.asyncio
async def test_pipeline_retry_idempotent_no_stale_or_double_disclosure(monkeypatch) -> None:
    """对同一 Retriever 连续两次 fill：第二次结果稳定，不残留、不重复加披露。"""
    monkeypatch.setattr(settings, "RAG_CONTEXT_CHARS", 300)
    retriever = _ProtocolRetriever([_hit("c1", "X" * 500)])  # 一块都装不下
    state = AgentState(user_input="问题", history=[])
    await AgentPipeline(object(), retriever=retriever)._fill_retrieval(state)
    assert retriever.last_selected == []
    first_disclosure = state.retrieval_disclosure

    state2 = AgentState(user_input="问题", history=[])
    await AgentPipeline(object(), retriever=retriever)._fill_retrieval(state2)
    assert retriever.last_selected == []  # 幂等：仍为空
    # 同一披露文案不被叠加成两份
    assert state2.retrieval_disclosure == first_disclosure
    assert state2.retrieval_disclosure.count(CONTEXT_BUDGET_EXHAUSTED) == 1
    assert _sources(retriever) == []


@pytest.mark.asyncio
async def test_pipeline_failed_then_reachable_clears_previous_selected(monkeypatch) -> None:
    """上一轮残留 selected → 本轮降级后必须清空（回滚不残留旧证据）。"""
    monkeypatch.setattr(settings, "RAG_CONTEXT_CHARS", 300)
    retriever = _ProtocolRetriever([])
    retriever.last_selected = [_hit("prev", "上一轮选入")]
    state = AgentState(user_input="问题", history=[])
    await AgentPipeline(object(), retriever=retriever)._fill_retrieval(state)

    assert retriever.last_selected == []
    assert _sources(retriever) == []


# ── P2-01：异常路径不得把残留 last_hits 当本轮证据（CE-04） ─────────


class _RecordingLLM:
    """记录模型每次实际收到的 user 文本，用于断言陈旧证据是否进入 payload。"""

    model = "recording"

    def __init__(self) -> None:
        self.all_user: list[str] = []

    async def chat(self, messages, options=None, **_kw) -> str:
        self.all_user.append(messages[-1].content)
        return "最终回答正文"

    async def stream_chat(self, messages, options=None, **_kw):
        self.all_user.append(messages[-1].content)
        yield "最终回答正文"


class _TwoRoundStaleRetriever:
    """可复用、跨请求持有 last_hits 的自定义 Retriever。

    第 1 轮成功选入 [stale]；第 2 轮 ``retrieve`` 抛异常但**不清空** ``last_hits``
    （模拟有状态残留）。异常收敛为 unavailable 后，管线绝不能把残留命中当本轮证据。
    """

    SENTINEL = "SENTINEL_STALE_EVIDENCE"

    def __init__(self) -> None:
        self.last_hits: list = []
        self.last_selected: list | None = None
        self._round = 0

    async def retrieve(self, query: str, plan: str) -> str:
        self._round += 1
        if self._round == 1:
            from app.rag.retrieval_status import RetrievalOutcome, RetrievalStatus

            hit = _hit("stale", f"{self.SENTINEL} 上一轮真实证据正文")
            self.last_hits = [hit]
            # 与 HybridRetriever 契约一致：成功检索后写入 last_outcome（否则 Supervisor 会提前返回）
            self.last_outcome = RetrievalOutcome(
                status=RetrievalStatus.OK, hits=[hit], backend="stale-fixture"
            )
            return format_context([hit])
        raise RuntimeError("向量库连接中断")


@pytest.mark.asyncio
async def test_pipeline_unavailable_ignores_residual_hits(monkeypatch) -> None:
    """P2-01/CE-04：unavailable 时 last_selected/来源必须为空，陈旧证据不进模型 payload。"""
    monkeypatch.setattr(settings, "RAG_CONTEXT_CHARS", 300)
    retriever = _TwoRoundStaleRetriever()

    # 第 1 轮：成功选入 stale
    r1 = _RecordingLLM()
    await AgentPipeline(r1, retriever=retriever).run(AgentState(user_input="第一问", history=[]))
    assert [h.id for h in retriever.last_selected] == ["stale"]

    # 第 2 轮：retrieve 抛异常，但 last_hits 残留 stale
    r2 = _RecordingLLM()
    state2 = AgentState(user_input="第二问", history=[])
    await AgentPipeline(r2, retriever=retriever).run(state2)

    assert state2.retrieval_status == "unavailable"
    assert retriever.last_selected == [], repr(retriever.last_selected)
    assert _sources(retriever) == []
    # 陈旧证据既不在 state.context（act 阶段外部上下文），也不在模型实际收到的任何消息里
    assert _TwoRoundStaleRetriever.SENTINEL not in state2.context
    assert _TwoRoundStaleRetriever.SENTINEL not in "\n".join(r2.all_user)
    # unavailable 用自己的披露语义，不被「超预算」覆盖
    assert CONTEXT_BUDGET_EXHAUSTED not in state2.retrieval_disclosure
    assert "知识库检索服务当前不可用" in state2.retrieval_disclosure


@pytest.mark.asyncio
async def test_fast_path_unavailable_ignores_residual_hits(monkeypatch) -> None:
    """Fast Path 同一缺陷类：异常但残留 last_hits 时不组装陈旧证据、来源为空。"""
    from app.agents.fast_path import iter_fast_path
    from app.agents.route import ChatRoute, RouteKind
    from app.llm.base import LLMOptions

    monkeypatch.setattr(settings, "RAG_CONTEXT_CHARS", 300)
    retriever = _TwoRoundStaleRetriever()

    r1 = _RecordingLLM()
    async for _ev in iter_fast_path(
        r1, LLMOptions(), AgentState(user_input="第一问", history=[]),
        ChatRoute(kind=RouteKind.RAG), retriever, None,
    ):
        pass
    assert [h.id for h in retriever.last_selected] == ["stale"]

    r2 = _RecordingLLM()
    async for _ev in iter_fast_path(
        r2, LLMOptions(), AgentState(user_input="第二问", history=[]),
        ChatRoute(kind=RouteKind.RAG), retriever, None,
    ):
        pass
    assert retriever.last_selected == [], repr(retriever.last_selected)
    assert _sources(retriever) == []
    assert _TwoRoundStaleRetriever.SENTINEL not in "\n".join(r2.all_user)


@pytest.mark.asyncio
async def test_supervisor_unavailable_ignores_residual_hits(monkeypatch) -> None:
    """Supervisor 同一缺陷类：异常但残留 last_hits 时不组装陈旧证据、来源为空。"""
    from app.agents.supervisor import SupervisorGraph

    monkeypatch.setattr(settings, "RAG_CONTEXT_CHARS", 300)
    retriever = _TwoRoundStaleRetriever()

    r1 = _RecordingLLM()
    await SupervisorGraph(r1, retriever=retriever)._retrieve(AgentState(user_input="第一问", history=[]))
    assert [h.id for h in retriever.last_selected] == ["stale"]

    r2 = _RecordingLLM()
    state2 = AgentState(user_input="第二问", history=[])
    await SupervisorGraph(r2, retriever=retriever)._retrieve(state2)
    assert retriever.last_selected == [], repr(retriever.last_selected)
    assert _sources(retriever) == []
    assert _TwoRoundStaleRetriever.SENTINEL not in state2.context

