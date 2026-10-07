"""RAG-037 检索状态与最终拒答契约。

四状态（ok / no_hit / below_threshold / unavailable）的判定顺序、少返回计数，
以及「同步管线 / 短路径 / HTTP 检索面」三个入口在检索故障时的真实行为。

证据要求是故障注入后入口的实际表现，不是测试条数。
"""

from __future__ import annotations

from uuid import uuid4

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


class _NullEmbedding:
    """不真正算向量的嵌入替身；Milvus 用例在到达嵌入前就会失败。"""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 8 for _ in texts]


class _IgnoreNoticeLLM(LLMProvider):
    """审查 F-02 用的对抗 LLM：完全忽略披露指令，照常冒充知识回答。"""

    model = "ignore-notice"

    def __init__(self) -> None:
        self.calls = 0
        self.prompts: list[str] = []

    async def chat(self, messages, options=None) -> str:
        self.calls += 1
        self.prompts.append(messages[-1].content)
        return "根据知识库，员工年假固定为 5 天。"

    async def stream_chat(self, messages, options=None):
        self.calls += 1
        yield "根据知识库，"
        yield "员工年假固定为 5 天。"


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
    """改造前：异常冒到管线级兜底，请求失败。改造后：完成且强制披露。

    披露不依赖模型服从：即使模型完全忽略指令，最终回复开头也是确定的。
    """
    llm = _CaptureLLM()
    pipeline = AgentPipeline(llm, LLMOptions(), retriever=_retriever(_Backend(error=RuntimeError("x"))))
    state = await pipeline.run(AgentState(user_input="年假怎么算"))
    assert state.retrieval_status == "unavailable"
    # 披露要求进了「行动」与「响应」两个阶段的提示
    assert settings.RAG_UNAVAILABLE_NOTICE in "".join(llm.prompts)
    # 应用层确定性前置，不靠模型自觉
    assert state.answer.startswith(settings.RAG_UNAVAILABLE_REPLY)
    assert "答案" in state.answer


async def test_pipeline_final_reply_is_deterministic_even_if_llm_ignores() -> None:
    """审查 F-02 反例：模型忽略披露指令时，最终回复仍不得冒充知识回答。"""
    llm = _IgnoreNoticeLLM()
    pipeline = AgentPipeline(llm, LLMOptions(), retriever=_retriever(_Backend(error=RuntimeError("x"))))
    state = await pipeline.run(AgentState(user_input="年假怎么算"))
    assert state.answer.startswith(settings.RAG_UNAVAILABLE_REPLY)
    assert "根据知识库" in state.answer  # 模型正文仍在，但已被明确标注


async def test_pipeline_low_score_terminal_reply_is_the_refusal_text() -> None:
    """全低分是终态拒答：回复就是拒答语本身，不交给模型改写。"""
    llm = _IgnoreNoticeLLM()
    pipeline = AgentPipeline(
        llm, LLMOptions(), retriever=_retriever(_Backend([_hit(0.2, "a")]))
    )
    state = await pipeline.run(AgentState(user_input="年假怎么算"))
    assert state.retrieval_status == "below_threshold"
    assert state.answer == settings.RAG_REFUSE_MESSAGE
    # 终态短路：检索之后的行动 / 反思 / 响应三个阶段都不再调用模型
    assert not any("请按系统要求撰写回答草稿" in p for p in llm.prompts)
    assert not any("审查意见" in p for p in llm.prompts)


async def test_pipeline_stream_terminal_reply_and_disclosure() -> None:
    """流式同样确定性：终态直接吐拒答语，故障先吐披露语再接模型正文。"""
    llm = _IgnoreNoticeLLM()
    pipeline = AgentPipeline(
        llm, LLMOptions(), retriever=_retriever(_Backend([_hit(0.2, "a")]))
    )
    events = [event async for event in pipeline.run_stream(AgentState(user_input="年假怎么算"))]
    tokens = "".join(e.data for e in events if e.type == "token")
    assert tokens == settings.RAG_REFUSE_MESSAGE
    assert events[-1].type == "done" and events[-1].data == settings.RAG_REFUSE_MESSAGE

    failing = AgentPipeline(
        llm, LLMOptions(), retriever=_retriever(_Backend(error=RuntimeError("x")))
    )
    events2 = [event async for event in failing.run_stream(AgentState(user_input="年假怎么算"))]
    tokens2 = "".join(e.data for e in events2 if e.type == "token")
    assert tokens2.startswith(settings.RAG_UNAVAILABLE_REPLY)
    assert "根据知识库" in tokens2


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


# ── 4. 向量库与外部检索器的故障语义 ────────────────────────


async def test_milvus_search_failure_is_unavailable_not_no_hit() -> None:
    """审查 F-01：Milvus 搜索异常不得被吞成空列表，否则故障冒充无命中。"""
    from app.rag.backend.native import NativeRagBackend
    from app.rag.vectorstore.milvus import MilvusUnavailableError, MilvusVectorStore

    class _ExplodingCollection:
        def search(self, *_args, **_kwargs):  # noqa: ANN002, ANN003
            raise RuntimeError("milvus-search-down")

    store = MilvusVectorStore.__new__(MilvusVectorStore)
    store.session = None
    store._collection = _ExplodingCollection()  # type: ignore[attr-defined]
    store._connect = lambda: _ExplodingCollection()  # type: ignore[method-assign]

    backend = NativeRagBackend(_NullEmbedding(), store, lambda text: [text])
    retriever = HybridRetriever(backend, tenant_id="t", top_k=5)
    with pytest.raises(MilvusUnavailableError):
        await backend.retrieve("q", tenant_id="t", top_k=5)
    # 异常冒泡到检索器后被收敛为 unavailable，而不是 no_hit
    out = await retriever.retrieve("q", "")
    assert retriever.last_status is RetrievalStatus.UNAVAILABLE
    assert out == ""


async def test_none_backend_result_is_unavailable() -> None:
    """审查 F-04：后端违约返回 None 时归为故障，不保留误导性的旧状态。"""

    class _NoneBackend:
        name = "none"

        async def retrieve(self, query, *, tenant_id, top_k, read_scope=None):  # noqa: ANN001
            return None

    r = HybridRetriever(_NoneBackend(), tenant_id="t", top_k=5)  # type: ignore[arg-type]
    out = await r.retrieve("q", "")
    assert r.last_status is RetrievalStatus.UNAVAILABLE
    assert r.last_outcome.error_type == "TypeError"
    assert out == ""


async def test_pipeline_foreign_retriever_exception_is_unavailable() -> None:
    """审查 F-04：自研管线对外部 Retriever 异常也要收敛，而不是通用失败。"""

    class _Raising:
        async def retrieve(self, query: str, plan: str) -> str:
            raise RuntimeError("外部检索器炸了")

    llm = _IgnoreNoticeLLM()
    pipeline = AgentPipeline(llm, LLMOptions(), retriever=_Raising())  # type: ignore[arg-type]
    state = await pipeline.run(AgentState(user_input="年假怎么算"))
    assert state.retrieval_status == "unavailable"
    assert state.error is None or "外部检索器炸了" not in (state.error or "")
    assert state.answer.startswith(settings.RAG_UNAVAILABLE_REPLY)


# ── 5. Supervisor 入口 ─────────────────────────────────────


async def test_supervisor_retrieves_and_propagates_status() -> None:
    """审查 F-03：MULTI 路由此前完全不检索，也不产生任何状态。"""
    pytest.importorskip("langgraph")
    from app.agents.supervisor import SupervisorGraph

    retriever = _retriever(_Backend(error=RuntimeError("检索不可用")))
    graph = SupervisorGraph(_IgnoreNoticeLLM(), LLMOptions(), retriever=retriever)
    state = await graph.run(AgentState(user_input="年假怎么算"))
    assert state.retrieval_status == "unavailable"
    assert state.answer.startswith(settings.RAG_UNAVAILABLE_REPLY)


async def test_supervisor_low_score_is_terminal_refusal() -> None:
    pytest.importorskip("langgraph")
    from app.agents.supervisor import SupervisorGraph

    retriever = _retriever(_Backend([_hit(0.2, "a")]))
    graph = SupervisorGraph(_IgnoreNoticeLLM(), LLMOptions(), retriever=retriever)
    state = await graph.run(AgentState(user_input="年假怎么算"))
    assert state.retrieval_status == "below_threshold"
    assert state.answer == settings.RAG_REFUSE_MESSAGE


async def test_notice_off_keeps_short_path_status_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """审查 F-06：开关只关 directive，短路径的状态文案不随之回退。"""
    object.__setattr__(settings, "RAG_STATUS_NOTICE_ENABLED", False)
    try:
        llm = _CaptureLLM("直接说明")
        state = AgentState(user_input="查一下年假")
        retriever = _retriever(_Backend(error=RuntimeError("检索不可用")))
        async for _event in iter_fast_path(
            llm, LLMOptions(), state, route_message(state.user_input), retriever, None
        ):
            pass
        assert state.retrieval_status == "unavailable"
        # directive 关掉了
        assert settings.RAG_UNAVAILABLE_NOTICE not in llm.systems[0]
        # 但状态文案仍在（比旧行为更安全，只是「完整回退」的说法不成立）
        assert "本次检索未完成" in llm.users[0]
    finally:
        object.__setattr__(settings, "RAG_STATUS_NOTICE_ENABLED", True)


async def test_chat_service_stream_persists_disclosure() -> None:
    """审查 F-02：`ChatService.chat_stream` 的落库正文也必须带披露，不只是 prompt。"""
    from sqlmodel import Session, SQLModel, select

    from app.core.database import engine, init_db
    from app.core.security import Role
    from app.models.conversation import Message
    from app.models.user import User
    from app.services.chat_service import ChatService

    SQLModel.metadata.create_all(engine)
    init_db()
    tenant = f"rag037-{uuid4().hex[:8]}"
    with Session(engine) as session:
        user = User(
            id=f"u-{uuid4().hex[:8]}",
            username=f"u{uuid4().hex[:6]}",
            tenant_id=tenant,
            role=Role.TENANT_ADMIN.value,
            hashed_password="x",
            token_version=0,
            is_active=True,
        )
        session.add(user)
        session.commit()
        service = ChatService(_IgnoreNoticeLLM())
        service._build_retriever = lambda _s, _u: _retriever(  # type: ignore[method-assign]
            _Backend(error=RuntimeError("检索不可用"))
        )
        events = []
        async for event in service.chat_stream(session, user, "年假怎么算"):
            events.append(event)
        assert any(e.type == "done" for e in events), [e.type for e in events]
        done = next(e for e in events if e.type == "done")
        assert done.data.startswith(settings.RAG_UNAVAILABLE_REPLY)
        rows = session.exec(select(Message).where(Message.role == "assistant")).all()
        mine = [r for r in rows if r.content.startswith(settings.RAG_UNAVAILABLE_REPLY)]
        assert mine, f"流式结束必须落库且带披露；实际 {len(rows)} 条助手消息"


# ── 6. HTTP 检索面 ─────────────────────────────────────────


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


@pytest.mark.anyio
async def test_http_search_failure_is_503_with_unavailable_header() -> None:
    """审查 F-10：检索故障不得降级为空列表，也不得只给裸 500。"""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    import app.api.routes.rag as rag_route
    from app.api.deps import get_current_user, get_db
    from app.api.routes.rag import router
    from app.core.security import Role
    from app.models.user import User

    class _Exploding:
        last_backend_name = "milvus"

        def __init__(self, session, tenant_id, reader=None) -> None:
            self.tenant_id = tenant_id

        async def search(self, query, top_k, backend=None):
            raise RuntimeError("milvus 不可用")

    original = rag_route.RAGService
    rag_route.RAGService = _Exploding  # type: ignore[assignment]
    try:
        app = FastAPI()
        app.include_router(router)
        user = User(
            id="u-2",
            username="u2",
            tenant_id="t-2",
            role=Role.SYSTEM_ADMIN.value,
            hashed_password="x",
        )
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_db] = lambda: None
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/rag/search", json={"query": "年假"})
        assert resp.status_code == 503
        assert resp.headers["X-Retrieval-Status"] == "unavailable"
        assert resp.headers["X-RAG-Backend"] == "milvus"
        assert resp.json()["detail"]
    finally:
        rag_route.RAGService = original  # type: ignore[assignment]
