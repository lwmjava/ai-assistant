"""RAG-029 按块上下文组装与真实来源。

每条断言都指向一个具体行为（不是「跑通了」）：围栏闭合、代码块不成半截、
selected 与文本里的 [资料 N] 一一对应、被挤掉的块不出现在来源里、记忆按条裁剪。
"""

from __future__ import annotations

import re

import pytest

from app.core.config import settings
from app.rag.context_builder import (
    FENCE_CLOSE,
    REASON_BUDGET,
    REASON_BUDGET_TRUNCATED,
    build_context,
    build_from_rag_text,
    render_rag_section,
    safe_truncate,
)
from app.rag.context_merge import merge_memory_and_rag
from app.rag.retriever import HybridRetriever, format_context
from app.rag.vectorstore.base import ChunkResult

_MARKER = re.compile(r"\[资料 (\d+)\]")


def _hit(cid: str, content: str, source: str = "手册.pdf") -> ChunkResult:
    return ChunkResult(
        id=cid,
        content=content,
        source=source,
        document_id="d1",
        score=0.5,
        similarity=0.9,
    )


def _markers(text: str) -> list[int]:
    return [int(n) for n in _MARKER.findall(text)]


# ── 1. 长 RAG 上下文被截断后围栏必须闭合 ──────────────────────


def test_long_rag_truncated_but_fence_still_closed(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RAG_CONTEXT_CHARS", 400)
    hits = [_hit("c1", "A" * 300), _hit("c2", "B" * 300), _hit("c3", "C" * 300)]
    payload = build_context("", hits)
    assert payload.text.rstrip().endswith(FENCE_CLOSE)
    assert payload.fence_closed is True
    assert len(payload.text) <= 400
    assert payload.selected_ids() == ["c1"]  # 只有首块装得下
    assert [h.id for h in payload.dropped] == ["c2", "c3"]


def test_every_budget_produces_closed_fence(monkeypatch) -> None:
    """扫描多个预算值：只要文本里出现开标记，就必须以闭合标记结尾。"""
    hits = [_hit(f"c{i}", f"资料正文 {i}。" * 40) for i in range(1, 6)]
    for budget in (120, 260, 400, 800, 1600, 6000):
        monkeypatch.setattr(settings, "RAG_CONTEXT_CHARS", budget)
        payload = build_context("", hits)
        if "[UNTRUSTED_SOURCE]" in payload.text:
            assert payload.text.rstrip().endswith(FENCE_CLOSE), f"budget={budget} 围栏未闭合"
        assert payload.fence_closed is True
        assert len(payload.text) <= budget or not payload.selected


# ── 2. 块内 ``` 代码围栏不被切成一半 ───────────────────────────


def test_first_block_truncation_keeps_code_fence_balanced(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RAG_CONTEXT_CHARS", 300)
    code = "```python\n" + "\n".join(f"line_{i} = {i}" for i in range(60)) + "\n```"
    hits = [_hit("c1", code)]
    payload = build_context("", hits)
    assert payload.text.count("```") % 2 == 0, "代码围栏被切成一半"
    assert payload.text.rstrip().endswith(FENCE_CLOSE)
    assert len(payload.text) <= 300
    assert payload.drop_reasons.get("c1") == REASON_BUDGET_TRUNCATED
    assert payload.selected_ids() == ["c1"]


def test_oversized_code_block_is_dropped_whole_when_not_first(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RAG_CONTEXT_CHARS", 500)
    code = "```python\n" + "\n".join(f"line_{i} = {i}" for i in range(60)) + "\n```"
    hits = [_hit("c1", "短资料。"), _hit("c2", code)]
    payload = build_context("", hits)
    assert payload.text.count("```") % 2 == 0
    assert payload.selected_ids() == ["c1"]
    assert payload.drop_reasons.get("c2") == REASON_BUDGET
    assert code not in payload.text


def test_safe_truncate_never_leaves_odd_fences() -> None:
    sample = "前言。\n```python\nx = 1\ny = 2\nz = 3\n```\n尾巴。"
    for limit in range(1, len(sample) + 1):
        out = safe_truncate(sample, limit)
        assert len(out) <= limit
        assert out.count("```") % 2 == 0, f"limit={limit} 留下奇数个围栏: {out!r}"


# ── 3. selected 与文本中的 [资料 N] 数量与顺序一一对应 ──────────


def test_selected_matches_rendered_markers_one_to_one(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RAG_CONTEXT_CHARS", 500)
    hits = [_hit(f"c{i}", "资料正文。" * 30) for i in range(1, 6)]
    payload = build_context("", hits)
    nums = _markers(payload.text)
    assert len(nums) == len(payload.selected)
    assert nums == list(range(1, len(payload.selected) + 1))
    # 顺序一致：第 N 个标记对应的就是 selected[N-1]
    for idx, (num, hit) in enumerate(zip(nums, payload.selected), 1):
        assert num == idx
        assert f"[资料 {num}]" in payload.text
        assert hit.content[:12] in payload.text


def test_marker_count_equals_selected_count_across_budgets(monkeypatch) -> None:
    hits = [_hit(f"c{i}", "正文内容。" * 20) for i in range(1, 7)]
    for budget in (150, 300, 600, 1200, 5000):
        monkeypatch.setattr(settings, "RAG_CONTEXT_CHARS", budget)
        payload = build_context("", hits)
        assert len(_markers(payload.text)) == len(payload.selected), f"budget={budget}"


# ── 4. 被预算挤掉的块不出现在 selected / 来源里 ────────────────


def test_dropped_chunk_absent_from_selected() -> None:
    hits = [_hit("c1", "短"), _hit("c2", "Y" * 4000), _hit("c3", "更小的一块")]
    payload = build_context("", hits, rag_budget=500)
    assert payload.selected_ids() == ["c1"]
    assert [h.id for h in payload.dropped] == ["c2", "c3"]
    assert payload.drop_reasons["c2"] == REASON_BUDGET
    # 不做补位：c3 更小也不得顶替 c2
    assert "更小的一块" not in payload.text


class _FakeSession:
    def get(self, *_args, **_kw):
        return None


class _FakeRetriever:
    """模拟组装后的检索器：last_hits 有 3 块，实际只选入 1 块。"""

    last_hits: list
    last_selected: list | None

    def __init__(self) -> None:
        self.last_hits = [_hit("c1", "短"), _hit("c2", "Y" * 4000), _hit("c3", "更小的一块")]
        self.last_selected = [self.last_hits[0]]


def test_reply_sources_prefers_last_selected_over_last_hits() -> None:
    from app.services.chat_service import ChatService

    out = ChatService._reply_sources(_FakeSession(), _FakeRetriever())
    assert [s["chunk_id"] for s in out] == ["c1"]


def test_reply_sources_prefers_last_selected_when_nothing_selected() -> None:
    """一块都没选入时来源必须为空，不能回落 last_hits 冒充。"""
    from app.services.chat_service import ChatService

    retriever = _FakeRetriever()
    retriever.last_selected = []
    assert ChatService._reply_sources(_FakeSession(), retriever) == []


def test_reply_sources_falls_back_to_last_hits_when_not_assembled() -> None:
    """还没组装过 payload（如短路径）时回落 last_hits，行为与改造前一致。"""
    from app.services.chat_service import ChatService

    retriever = _FakeRetriever()
    retriever.last_selected = None
    out = ChatService._reply_sources(_FakeSession(), retriever)
    assert [s["chunk_id"] for s in out] == ["c1", "c2", "c3"]


# ── 5. 记忆优先且长历史按条裁剪，保留最新条目，无半条 ────────────


def test_memory_trim_keeps_newest_entries_and_never_half_entry(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RAG_MEMORY_CONTEXT_CHARS", 20)
    memory = "用户问过退款时限。\n\n用户问过发票抬头。\n\n用户问过套餐升级。"
    payload = build_context(memory, [])
    assert "套餐升级" in payload.text
    assert "退款时限" not in payload.text
    assert payload.memory_truncated is True
    assert payload.memory_dropped_entries >= 1
    # 无半条：裁剪后的记忆以完整句子结尾，没有省略标记
    body = payload.text.split("\n", 1)[1]
    assert "…" not in body
    assert body.endswith("。")


def test_memory_single_oversized_entry_dropped_whole(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RAG_MEMORY_CONTEXT_CHARS", 10)
    payload = build_context("这是一条远超预算的长记忆。" * 3, [])
    assert payload.memory_truncated is True
    assert payload.memory_dropped_entries == 1
    assert "长记忆" not in payload.text


def test_memory_not_truncated_when_within_budget(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RAG_MEMORY_CONTEXT_CHARS", 2000)
    memory = "用户问过退款时限。\n\n用户问过发票抬头。"
    payload = build_context(memory, [])
    assert payload.memory_truncated is False
    assert payload.memory_dropped_entries == 0
    assert memory in payload.text


# ── 6. 记忆 + 长 RAG 合并后不超预算 ───────────────────────────


def test_memory_plus_long_rag_stays_within_budgets(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RAG_MEMORY_CONTEXT_CHARS", 200)
    monkeypatch.setattr(settings, "RAG_CONTEXT_CHARS", 400)
    memory = "\n\n".join(f"历史条目 {i}: 用户提到过主题 {i}。" for i in range(20))
    hits = [_hit(f"c{i}", "X" * 300) for i in range(1, 5)]
    payload = build_context(memory, hits)
    head = "## 会话记忆\n"
    assert payload.text.startswith(head)
    memory_part = payload.text[len(head) :].split("\n\n[UNTRUSTED_SOURCE]")[0]
    assert len(memory_part) <= 200
    assert payload.text.rstrip().endswith(FENCE_CLOSE)
    assert len(payload.text) <= len(head) + 200 + 2 + 400
    assert payload.selected_ids() == ["c1"]


# ── 7. 只有字符串的降级路径，围栏仍闭合 ────────────────────────


def test_degraded_string_path_keeps_fence_closed(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RAG_CONTEXT_CHARS", 200)
    rag_text = format_context([_hit("c1", "A" * 300), _hit("c2", "B" * 300)])
    payload = build_from_rag_text("", rag_text)
    assert payload.text.rstrip().endswith(FENCE_CLOSE)
    assert payload.fence_closed is True
    assert len(payload.text) <= 200
    assert payload.selected == []  # 拿不到结构化命中就不声明来源


def test_merge_memory_and_rag_keeps_fence_closed(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RAG_CONTEXT_CHARS", 250)
    monkeypatch.setattr(settings, "RAG_MEMORY_CONTEXT_CHARS", 100)
    rag_text = render_rag_section([_hit("c1", "A" * 300), _hit("c2", "B" * 300)])
    merged = merge_memory_and_rag("用户问过退款时限。", rag_text)
    assert merged.rstrip().endswith(FENCE_CLOSE)
    assert "[/UNTRUSTED_SOURCE]" in merged
    assert len(merged) < len(rag_text)


def test_degraded_path_drops_rag_rather_than_stray_close_tag(monkeypatch) -> None:
    """空间不足以留下开标记时整段丢弃，不产出没有开标记的闭合标记。"""
    monkeypatch.setattr(settings, "RAG_CONTEXT_CHARS", 30)
    rag_text = format_context([_hit("c1", "A" * 300)])
    payload = build_from_rag_text("", rag_text)
    assert payload.text == ""
    assert payload.fence_closed is True


# ── 8. 端到端：HybridRetriever + AgentPipeline ─────────────────


class _FakeBackend:
    def __init__(self, hits: list[ChunkResult]) -> None:
        self._hits = hits

    async def retrieve(
        self,
        query: str,
        *,
        tenant_id: str,
        top_k: int,
        read_scope: object = None,
    ) -> list[ChunkResult]:
        return self._hits


class _EchoLLM:
    """只回一句固定草稿的假 LLM，够跑通管线。"""

    model = "fake"

    async def chat(self, messages, options=None, **_kw):
        from app.llm.base import ChatMessage, ChatRole

        return ChatMessage(role=ChatRole.ASSISTANT, content="草稿")

    async def stream(self, messages, options=None, **_kw):
        yield "草稿"


@pytest.mark.asyncio
async def test_pipeline_last_selected_matches_final_context(monkeypatch) -> None:
    from app.agents.pipeline import AgentPipeline, AgentState

    monkeypatch.setattr(settings, "RAG_CONTEXT_CHARS", 500)
    hits = [_hit(f"c{i}", "资料正文。" * 30) for i in range(1, 5)]
    retriever = HybridRetriever(_FakeBackend(hits), tenant_id="t", top_k=5)
    pipeline = AgentPipeline(_EchoLLM(), retriever=retriever)
    state = AgentState(user_input="年假有几天", history=[])
    state.context = "用户问过年假。"
    await pipeline._fill_retrieval(state)

    assert retriever.last_selected is not None
    ids = [h.id for h in retriever.last_selected]
    assert ids == [f"c{i}" for i in range(1, len(ids) + 1)]
    assert len(ids) < len(hits)  # 预算确实挤掉了后面的块
    # 最终 context 里的 [资料 N] 与 last_selected 严格一致
    assert _markers(state.context) == list(range(1, len(ids) + 1))
    assert state.context.rstrip().endswith(FENCE_CLOSE)
    assert "用户问过年假。" in state.context
    # 被挤掉的块既不在 context 里，也不在 last_selected 里
    for dropped in hits[len(ids) :]:
        assert dropped.id not in ids
        assert f"[资料 {hits.index(dropped) + 1}]" not in state.context


@pytest.mark.asyncio
async def test_pipeline_degraded_string_retriever_keeps_fence(monkeypatch) -> None:
    from app.agents.pipeline import AgentPipeline, AgentState

    monkeypatch.setattr(settings, "RAG_CONTEXT_CHARS", 200)

    class _StringRetriever:
        def __init__(self, text: str) -> None:
            self.text = text

        async def retrieve(self, query: str, plan: str) -> str:
            return self.text

    rag_text = format_context([_hit("c1", "A" * 300), _hit("c2", "B" * 300)])
    retriever = _StringRetriever(rag_text)
    pipeline = AgentPipeline(_EchoLLM(), retriever=retriever)
    state = AgentState(user_input="年假有几天", history=[])
    await pipeline._fill_retrieval(state)
    assert state.context.rstrip().endswith(FENCE_CLOSE)
    assert len(state.context) <= 200


@pytest.mark.asyncio
async def test_pipeline_reply_sources_excludes_budget_dropped(monkeypatch) -> None:
    """端到端：对外来源取自 last_selected，被预算挤掉的块不出现。"""
    from app.agents.pipeline import AgentPipeline, AgentState
    from app.services.chat_service import ChatService

    monkeypatch.setattr(settings, "RAG_CONTEXT_CHARS", 500)
    hits = [_hit(f"c{i}", "资料正文。" * 30) for i in range(1, 5)]
    retriever = HybridRetriever(_FakeBackend(hits), tenant_id="t", top_k=5)
    pipeline = AgentPipeline(_EchoLLM(), retriever=retriever)
    state = AgentState(user_input="年假有几天", history=[])
    await pipeline._fill_retrieval(state)

    sources = ChatService._reply_sources(_FakeSession(), retriever)
    assert [s["chunk_id"] for s in sources] == [h.id for h in retriever.last_selected]
    assert len(sources) < len(retriever.last_hits)
