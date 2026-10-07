"""RAG-037 检索状态与最终拒答契约。

四状态（ok / no_hit / below_threshold / unavailable）的判定顺序、少返回计数，
以及「同步管线 / 短路径 / HTTP 检索面」三个入口在检索故障时的真实行为。

证据要求是故障注入后入口的实际表现，不是测试条数。
"""

from __future__ import annotations

import pytest

from app.agents.fast_path import iter_fast_path
from app.agents.pipeline import AgentPipeline, AgentState
from app.agents.route import route_message
from app.core.config import settings
from app.llm.base import LLMOptions, LLMProvider
from app.rag.retrieval_status import (
    DISCLOSURE_PREFIX,
    RetrievalOutcome,
    RetrievalStatus,
    classify,
)
from app.rag.retriever import HybridRetriever
from app.rag.vectorstore.base import ChunkResult


class _Backend:
    """可控故障的检索后端。"""

    name = "fake"

    def __init__(self, hits: list[ChunkResult] | None = None, error: Exception | None = None):
        self._hits = hits if hits is not None else []
        self._error = error

    async def retrieve(
        self,
        query: str,
        *,
        tenant_id: str,
        top_k: int,
        read_scope: object = None,
    ) -> list[ChunkResult]:
        if self._error is not None:
            raise self._error
        return self._hits


def _hit(similarity: float, content: str = "x") -> ChunkResult:
    return ChunkResult(
        id=content,
        content=content,
        source="doc-1",
        document_id="doc-1",
        score=0.01,
        similarity=similarity,
    )


def _retriever(backend: _Backend) -> HybridRetriever:
    return HybridRetriever(backend, tenant_id="t", top_k=5)


class _CaptureLLM(LLMProvider):
    """记录送进模型的全部文本，不真的生成。"""

    model = "capture"

    def __init__(self, text: str = "答案") -> None:
        self.text = text
        self.prompts: list[str] = []
        self.users: list[str] = []
        self.systems: list[str] = []

    async def chat(self, messages, options=None) -> str:
        self.prompts.append(messages[-1].content)
        for msg in messages:
            if msg.role.value == "system":
                self.systems.append(msg.content)
            else:
                self.users.append(msg.content)
        return self.text

    async def stream_chat(self, messages, options=None):
        yield await self.chat(messages, options)


# ── 1. 状态判定 ────────────────────────────────────────────


def test_classify_order_is_unique() -> None:
    """异常优先于零候选，零候选优先于全低分。"""
    assert classify(error_type="RuntimeError") is RetrievalStatus.UNAVAILABLE
    assert classify(error_type="", candidates=0, kept=0) is RetrievalStatus.NO_HIT
    assert classify(error_type="", candidates=3, kept=0) is RetrievalStatus.BELOW_THRESHOLD
    assert classify(error_type="", candidates=3, kept=2) is RetrievalStatus.OK


async def test_ok_with_partial_drop_counts_dropped_only() -> None:
    """少返回只减不补：保留 2 条、剔除 1 条，状态仍是 ok。"""
    r = _retriever(_Backend([_hit(0.9, "甲条"), _hit(0.8, "乙条"), _hit(0.1, "丙条")]))
    out = await r.retrieve("q", "")
    assert r.last_status is RetrievalStatus.OK
    assert r.last_outcome.dropped_by_threshold == 1
    assert r.last_outcome.is_partial is True
    assert "丙条" not in out
    assert [h.id for h in r.last_hits] == ["甲条", "乙条"]
    # 少返回不是异常，不得产生任何披露要求
    assert r.last_outcome.notice == ""


async def test_no_hit_has_no_hits_and_no_directive_block() -> None:
    r = _retriever(_Backend([]))
    out = await r.retrieve("q", "")
    assert r.last_status is RetrievalStatus.NO_HIT
    assert out == ""
    assert r.last_outcome.dropped_by_threshold == 0
    assert r.last_outcome.notice == settings.RAG_NO_HIT_NOTICE


async def test_below_threshold_keeps_refusal_text_and_status() -> None:
    r = _retriever(_Backend([_hit(0.2, "a"), _hit(0.1, "b")]))
    out = await r.retrieve("q", "")
    assert r.last_status is RetrievalStatus.BELOW_THRESHOLD
    assert out == settings.RAG_REFUSE_MESSAGE
    assert r.last_hits == []
    assert r.last_outcome.notice == settings.RAG_REFUSE_MESSAGE


async def test_unavailable_does_not_raise_and_records_error_type() -> None:
    """检索故障收敛为状态，不再向上抛、不再让请求整体失败。"""
    r = _retriever(_Backend(error=RuntimeError("嵌入服务不可用")))
    out = await r.retrieve("q", "")
    assert r.last_status is RetrievalStatus.UNAVAILABLE
    assert r.last_outcome.error_type == "RuntimeError"
    # 不得伪造空知识，也不得返回拒答语冒充「查过了没有」
    assert out == ""
    assert r.last_outcome.notice == settings.RAG_UNAVAILABLE_NOTICE


async def test_notice_switch_off_rolls_back_without_hiding_status() -> None:
    """关闭开关只回退披露注入，状态本身仍然可见。"""
    r = _retriever(_Backend(error=RuntimeError("boom")))
    object.__setattr__(settings, "RAG_STATUS_NOTICE_ENABLED", False)
    try:
        await r.retrieve("q", "")
        assert r.last_status is RetrievalStatus.UNAVAILABLE
        assert r.last_outcome.notice == ""
        assert r.last_outcome.directive() == ""
    finally:
        object.__setattr__(settings, "RAG_STATUS_NOTICE_ENABLED", True)


def test_directive_is_instruction_not_untrusted_data() -> None:
    """披露要求是指令，不能带不可信资料围栏。"""
    outcome = RetrievalOutcome(status=RetrievalStatus.UNAVAILABLE)
    directive = outcome.directive()
    assert directive.startswith(DISCLOSURE_PREFIX)
    assert "[UNTRUSTED_SOURCE]" not in directive
    assert RetrievalOutcome(status=RetrievalStatus.OK).directive() == ""


# ── 2. 同步入口：自研管线 ───────────────────────────────────


async def test_pipeline_survives_retrieval_failure_and_discloses() -> None:
    """改造前：异常冒到管线级兜底，请求失败。改造后：完成且强制披露。"""
    llm = _CaptureLLM()
    pipeline = AgentPipeline(llm, LLMOptions(), retriever=_retriever(_Backend(error=RuntimeError("x"))))
    state = await pipeline.run(AgentState(user_input="年假怎么算"))
    assert state.retrieval_status == "unavailable"
    # 披露要求进了「行动」与「响应」两个阶段的提示
    assert settings.RAG_UNAVAILABLE_NOTICE in "".join(llm.prompts)
    assert state.answer == "答案"


async def test_pipeline_low_score_carries_refusal_into_final_stage() -> None:
    """低分最终回复：拒答要求必须出现在最终响应阶段，而不只是检索返回文本。"""
    llm = _CaptureLLM()
    pipeline = AgentPipeline(
        llm, LLMOptions(), retriever=_retriever(_Backend([_hit(0.2, "a")]))
    )
    state = await pipeline.run(AgentState(user_input="年假怎么算"))
    assert state.retrieval_status == "below_threshold"
    respond_prompts = [p for p in llm.prompts if "审查意见" in p]
    assert respond_prompts, "最终响应阶段未产生提示"
    assert settings.RAG_REFUSE_MESSAGE in respond_prompts[-1]


async def test_pipeline_ok_has_no_disclosure() -> None:
    llm = _CaptureLLM()
    pipeline = AgentPipeline(llm, LLMOptions(), retriever=_retriever(_Backend([_hit(0.9, "a")])))
    state = await pipeline.run(AgentState(user_input="年假怎么算"))
    assert state.retrieval_status == "ok"
    assert state.retrieval_notice == ""
    assert DISCLOSURE_PREFIX not in "".join(llm.prompts)


# ── 3. 短路径入口 ──────────────────────────────────────────


async def test_fast_path_failure_is_not_presented_as_no_results() -> None:
    """改造前：故障被吞成「没有可用的检索结果」，模型直接冒充知识回答。"""
    llm = _CaptureLLM("直接说明")
    state = AgentState(user_input="查一下年假")
    retriever = _retriever(_Backend(error=RuntimeError("检索不可用")))
    async for _event in iter_fast_path(
        llm, LLMOptions(), state, route_message(state.user_input), retriever, None
    ):
        pass
    assert state.retrieval_status == "unavailable"
    assert "没有可用的检索结果" not in llm.users[0]
    assert "本次检索未完成" in llm.users[0]
    # 披露要求是 system 指令，不是塞在「知识库」资料段里
    assert settings.RAG_UNAVAILABLE_NOTICE in llm.systems[0]


async def test_fast_path_no_hit_still_says_no_results() -> None:
    """零候选与故障必须区分开：零候选才说「没有可用的检索结果」。"""
    llm = _CaptureLLM("直接说明")
    state = AgentState(user_input="查一下年假")
    retriever = _retriever(_Backend([]))
    async for _event in iter_fast_path(
        llm, LLMOptions(), state, route_message(state.user_input), retriever, None
    ):
        pass
    assert state.retrieval_status == "no_hit"
    assert "没有可用的检索结果" in llm.users[0]
    assert settings.RAG_NO_HIT_NOTICE in llm.systems[0]


async def test_fast_path_ok_injects_snippet_without_disclosure() -> None:
    llm = _CaptureLLM("年假 5 天")
    state = AgentState(user_input="根据知识库回答年假")
    retriever = _retriever(_Backend([_hit(0.9, "年假 5 天")]))
    async for _event in iter_fast_path(
        llm, LLMOptions(), state, route_message(state.user_input), retriever, None
    ):
        pass
    assert state.retrieval_status == "ok"
    assert "年假 5 天" in llm.users[0]
    assert DISCLOSURE_PREFIX not in llm.systems[0]


async def test_fast_path_foreign_retriever_exception_still_unavailable() -> None:
    """不是 HybridRetriever 的检索器抛异常时，也不能退化成「查过了没有」。"""

    class _Raising:
        async def retrieve(self, query: str, plan: str) -> str:
            raise RuntimeError("外部检索器炸了")

    llm = _CaptureLLM("直接说明")
    state = AgentState(user_input="查一下年假")
    async for _event in iter_fast_path(
        llm, LLMOptions(), state, route_message(state.user_input), _Raising(), None
    ):
        pass
    assert state.retrieval_status == "unavailable"
    assert settings.RAG_UNAVAILABLE_NOTICE in llm.systems[0]


# ── 4. HTTP 检索面 ─────────────────────────────────────────


@pytest.mark.anyio
async def test_http_search_exposes_status_and_effective_backend() -> None:
    """后端工厂静默降级必须可观察：响应头给出实际生效后端与状态。"""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.api.deps import get_current_user, get_db
    from app.api.routes.rag import router
    from app.core.security import Role
    from app.models.user import User
    from app.rag.retrieval_status import RetrievalStatus

    sent = {"backend": None, "called": 0}

    class _RAGServiceStub:
        last_backend_name = "native"

        def __init__(self, session, tenant_id, reader=None) -> None:
            self.tenant_id = tenant_id

        async def search(self, query, top_k, backend=None):
            sent["backend"] = backend
            sent["called"] += 1
            return []

    import app.api.routes.rag as rag_route

    original = rag_route.RAGService
    rag_route.RAGService = _RAGServiceStub  # type: ignore[assignment]
    try:
        app = FastAPI()
        app.include_router(router)
        user = User(
            id="u-1",
            username="u1",
            tenant_id="t-1",
            role=Role.SYSTEM_ADMIN.value,
            hashed_password="x",
        )
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_db] = lambda: None
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/rag/search", json={"query": "年假", "backend": "langchain"})
        assert resp.status_code == 200
        # 请求 langchain，实际生效被降级为 native —— 这个事实必须外露
        assert resp.headers["X-RAG-Backend"] == "native"
        assert resp.headers["X-Retrieval-Status"] == RetrievalStatus.NO_HIT.value
        assert sent["backend"] == "langchain"
    finally:
        rag_route.RAGService = original  # type: ignore[assignment]
