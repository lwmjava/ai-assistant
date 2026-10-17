"""RAG-040：版本绑定章节摘要。

覆盖五类失败边界（任务卡交付要求）：
1. 数值边界：空章节不发请求；超长章节切批；max_calls 耗尽即停。
2. 失败路径：provider 抛错/超时/坏输出→章节 failed、原文检索不受影响；部分失败；重入幂等。
3. 权限与租户：跨租户 / 软删 / 非当前版摘要读取被拒；授权继承。
4. 身份与绑定：摘要绑定 content_hash / chunk_plan 版本 / 模型 / Prompt，支撑原文 ID 回查。
5. 回滚与幂等：默认关闭配置存在；重跑命中已 ready 绑定即跳过；不做物理清理。

所有 LLM 调用走注入的合成 provider（无真实收费调用）；DB 用 tmp_path 独立 SQLite。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from uuid import uuid4

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.config import settings
from app.llm.base import ChatMessage, LLMOptions, LLMProvider
from app.models.rag import (
    Document,
    DocumentChunk,
    SectionSummary,
    SectionSummaryJob,
    SectionSummaryJobStatus,
    SectionSummaryStatus,
)
from app.models.user import User


# ── 合成 provider（无真实网络/费用）─────────────────────────────────────────
class ScriptedProvider(LLMProvider):
    """按脚本返回回复；可抛错、可统计调用次数。"""

    def __init__(self, replies: Sequence[str] | None = None, *, exc: Exception | None = None) -> None:
        self.model = "scripted-summary-model"
        self._replies = list(replies or [])
        self._exc = exc
        self.calls = 0
        self.seen_messages: list[list[ChatMessage]] = []

    async def chat(
        self, messages: list[ChatMessage], options: LLMOptions | None = None
    ) -> str:
        self.calls += 1
        self.seen_messages.append(messages)
        if self._exc is not None:
            raise self._exc
        if self._replies:
            return self._replies.pop(0)
        return "章节摘要：要点已概括。"

    async def stream_chat(
        self, messages: list[ChatMessage], options: LLMOptions | None = None
    ) -> AsyncIterator[str]:
        text = await self.chat(messages, options)
        yield text


def _user(user_id: str, tenant_id: str, role: str = "member") -> User:
    return User(
        id=user_id, tenant_id=tenant_id, username=user_id,
        hashed_password="", role=role, token_version=0, is_active=True,
    )


def _isolated_session(tmp_path: Path) -> Session:
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'rag040.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _make_doc(session: Session, *, tenant_id: str, content_hash: str = "hash-v1") -> Document:
    doc = Document(
        id=f"doc-{uuid4().hex[:8]}",
        tenant_id=tenant_id,
        user_id="owner",
        title="章节文档",
        source="chapter.md",
        content_hash=content_hash,
        is_current=True,
        chunk_plan=json.dumps({"version": "auto-routing-v0.1", "strategy": "parent_child",
                               "routing_reason": "test", "chunk_params": {}}),
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def _parent_chunk(session: Session, doc: Document, *, content: str = "父块完整正文。") -> DocumentChunk:
    parent = DocumentChunk(
        id=f"par-{uuid4().hex[:8]}",
        tenant_id=doc.tenant_id,
        document_id=doc.id,
        content=content,
        source="chapter.md",
        chunk_metadata='{"kind":"parent"}',
    )
    session.add(parent)
    session.commit()
    session.refresh(parent)
    return parent


# 延迟导入被测模块，便于先确认导入即失败（模块不存在）。
def _import_module():
    from app.rag import section_summaries as mod
    return mod


# ── 正例：创建并运行任务，产出绑定摘要 ─────────────────────────────────────
async def test_create_and_run_job_produces_bound_summary(tmp_path: Path) -> None:
    mod = _import_module()
    session = _isolated_session(tmp_path)
    tenant = f"t-{uuid4().hex[:8]}"
    owner = _user("owner", tenant)
    try:
        doc = _make_doc(session, tenant_id=tenant)
        parent = _parent_chunk(session, doc, content="这是一个大章节的完整正文内容。")
        provider = ScriptedProvider(replies=["这是概括后的章节摘要。"])
        job = mod.create_summary_job(session, owner, doc.id)
        await mod.run_summary_job(session, job, provider)
        summary = session.exec(select(SectionSummary).where(SectionSummary.document_id == doc.id)).first()
        assert summary is not None
        assert summary.status == SectionSummaryStatus.READY.value
        assert summary.chunk_id == parent.id
        # 绑定元数据齐全
        assert summary.source_version_hash == doc.content_hash
        assert summary.chunk_plan_version == "auto-routing-v0.1"
        assert summary.model == "scripted-summary-model"
        assert summary.prompt_version == mod.PROMPT_VERSION
        # 支撑原文 ID 回查
        source_ids = json.loads(summary.source_chunk_ids or "[]")
        assert parent.id in source_ids
        # 任务状态
        refreshed = session.get(SectionSummaryJob, job.id)
        assert refreshed.status == SectionSummaryJobStatus.SUCCESS.value
        assert refreshed.chapters_ready == 1
    finally:
        session.close()


# ── 边界 1：数值边界 ─────────────────────────────────────────────────────
async def test_empty_chapter_makes_no_call_and_no_row(tmp_path: Path) -> None:
    mod = _import_module()
    session = _isolated_session(tmp_path)
    tenant = f"t-{uuid4().hex[:8]}"
    owner = _user("owner", tenant)
    try:
        doc = _make_doc(session, tenant_id=tenant)
        _parent_chunk(session, doc, content="")  # 空章节
        provider = ScriptedProvider()
        job = mod.create_summary_job(session, owner, doc.id)
        await mod.run_summary_job(session, job, provider)
        assert provider.calls == 0, "空章节不得发请求"
        rows = session.exec(select(SectionSummary).where(SectionSummary.document_id == doc.id)).all()
        assert rows == []
    finally:
        session.close()


async def test_oversized_chapter_is_batched(tmp_path: Path) -> None:
    mod = _import_module()
    session = _isolated_session(tmp_path)
    tenant = f"t-{uuid4().hex[:8]}"
    owner = _user("owner", tenant)
    try:
        doc = _make_doc(session, tenant_id=tenant)
        # 约 208 字符，按 200/批恰切成 2 批，再合并成 1 条。
        long_content = "章节正文片段。" * 40
        assert 200 < len(long_content) <= 400
        _parent_chunk(session, doc, content=long_content)
        provider = ScriptedProvider(replies=["批1摘要", "批2摘要", "合并后的总摘要"])
        job = mod.create_summary_job(session, owner, doc.id)
        await mod.run_summary_job(session, job, provider, max_chars_per_batch=200, max_calls=10)
        # 两批 + 一次合并 = 3 次调用
        assert provider.calls == 3
        summary = session.exec(select(SectionSummary).where(SectionSummary.document_id == doc.id)).first()
        assert summary is not None and summary.status == SectionSummaryStatus.READY.value
    finally:
        session.close()


async def test_max_calls_exhausted_stops_calling(tmp_path: Path) -> None:
    mod = _import_module()
    session = _isolated_session(tmp_path)
    tenant = f"t-{uuid4().hex[:8]}"
    owner = _user("owner", tenant)
    try:
        doc = _make_doc(session, tenant_id=tenant)
        long_content = "正文。" * 200  # 需要多批
        _parent_chunk(session, doc, content=long_content)
        provider = ScriptedProvider(replies=["批摘要"] * 20)
        job = mod.create_summary_job(session, owner, doc.id)
        await mod.run_summary_job(session, job, provider, max_chars_per_batch=100, max_calls=2)
        assert provider.calls <= 2, "达到 max_calls 必须立即停止，不得发第 3 次"
    finally:
        session.close()


# ── 边界 2：失败路径 ──────────────────────────────────────────────────────
async def test_provider_failure_marks_failed_and_keeps_original_chunks(tmp_path: Path) -> None:
    mod = _import_module()
    session = _isolated_session(tmp_path)
    tenant = f"t-{uuid4().hex[:8]}"
    owner = _user("owner", tenant)
    try:
        doc = _make_doc(session, tenant_id=tenant)
        parent = _parent_chunk(session, doc, content="正常章节正文。")
        provider = ScriptedProvider(exc=TimeoutError("boom"))
        job = mod.create_summary_job(session, owner, doc.id)
        await mod.run_summary_job(session, job, provider)
        # 摘要行记为 failed，但原文块原样保留
        summary = session.exec(select(SectionSummary).where(SectionSummary.document_id == doc.id)).first()
        assert summary is not None and summary.status == SectionSummaryStatus.FAILED.value
        assert session.get(DocumentChunk, parent.id) is not None, "原文块不得被删除或改写"
        refreshed = session.get(SectionSummaryJob, job.id)
        assert refreshed.status == SectionSummaryJobStatus.FAILED.value
    finally:
        session.close()


async def test_partial_failure_keeps_successful_chapters(tmp_path: Path) -> None:
    mod = _import_module()
    session = _isolated_session(tmp_path)
    tenant = f"t-{uuid4().hex[:8]}"
    owner = _user("owner", tenant)
    try:
        doc = _make_doc(session, tenant_id=tenant)
        ok_parent = _parent_chunk(session, doc, content="成功章节。")
        bad_parent = _parent_chunk(session, doc, content="失败章节。")
        # 第一次调用成功，第二次抛错。
        provider = ScriptedProvider(replies=["成功摘要"])
        original_chat = provider.chat

        async def flaky(messages, options=None):  # type: ignore[no-untyped-def]
            if provider.calls == 0:
                return await original_chat(messages, options)
            raise RuntimeError("provider down")

        provider.chat = flaky  # type: ignore[method-assign]
        job = mod.create_summary_job(session, owner, doc.id)
        await mod.run_summary_job(session, job, provider)
        refreshed = session.get(SectionSummaryJob, job.id)
        assert refreshed.status == SectionSummaryJobStatus.PARTIAL.value
        ok_row = session.exec(
            select(SectionSummary).where(SectionSummary.chunk_id == ok_parent.id)
        ).first()
        bad_row = session.exec(
            select(SectionSummary).where(SectionSummary.chunk_id == bad_parent.id)
        ).first()
        assert ok_row is not None and ok_row.status == SectionSummaryStatus.READY.value
        assert bad_row is not None and bad_row.status == SectionSummaryStatus.FAILED.value
    finally:
        session.close()


async def test_rerun_is_idempotent_when_ready_summary_matches_binding(tmp_path: Path) -> None:
    mod = _import_module()
    session = _isolated_session(tmp_path)
    tenant = f"t-{uuid4().hex[:8]}"
    owner = _user("owner", tenant)
    try:
        doc = _make_doc(session, tenant_id=tenant)
        _parent_chunk(session, doc, content="幂等章节正文。")
        provider = ScriptedProvider(replies=["摘要A", "摘要B"])
        job = mod.create_summary_job(session, owner, doc.id)
        await mod.run_summary_job(session, job, provider)
        calls_after_first = provider.calls
        # 重跑同一文档：绑定一致的 ready 摘要应被复用，不重复调用。
        job2 = mod.create_summary_job(session, owner, doc.id)
        await mod.run_summary_job(session, job2, provider)
        assert provider.calls == calls_after_first, "重跑不得对绑定一致的 ready 摘要重复发请求"
    finally:
        session.close()


# ── 边界 3：权限与租户 ────────────────────────────────────────────────────
async def test_cross_tenant_read_rejected(tmp_path: Path) -> None:
    mod = _import_module()
    session = _isolated_session(tmp_path)
    tenant = f"t-{uuid4().hex[:8]}"
    owner = _user("owner", tenant)
    try:
        doc = _make_doc(session, tenant_id=tenant)
        _parent_chunk(session, doc, content="跨租户章节。")
        provider = ScriptedProvider(replies=["摘要"])
        job = mod.create_summary_job(session, owner, doc.id)
        await mod.run_summary_job(session, job, provider)
        summary = session.exec(select(SectionSummary).where(SectionSummary.document_id == doc.id)).first()
        assert summary is not None
        outsider = _user("outsider", f"other-{uuid4().hex[:8]}")
        with pytest.raises(PermissionError):
            mod.get_readable_summary(session, summary.id, outsider)
    finally:
        session.close()


async def test_soft_deleted_doc_summary_rejected(tmp_path: Path) -> None:
    mod = _import_module()
    session = _isolated_session(tmp_path)
    tenant = f"t-{uuid4().hex[:8]}"
    owner = _user("owner", tenant)
    try:
        doc = _make_doc(session, tenant_id=tenant)
        _parent_chunk(session, doc, content="软删章节。")
        provider = ScriptedProvider(replies=["摘要"])
        job = mod.create_summary_job(session, owner, doc.id)
        await mod.run_summary_job(session, job, provider)
        summary = session.exec(select(SectionSummary).where(SectionSummary.document_id == doc.id)).first()
        # 源文档软删：摘要读取立即拒绝，但行仍在（不物理清理）。
        from datetime import datetime
        doc.deleted_at = datetime(2026, 1, 1)
        session.add(doc)
        session.commit()
        with pytest.raises(PermissionError):
            mod.get_readable_summary(session, summary.id, owner)
        assert session.get(SectionSummary, summary.id) is not None, "不做物理清理"
    finally:
        session.close()


async def test_replaced_non_current_doc_summary_rejected(tmp_path: Path) -> None:
    mod = _import_module()
    session = _isolated_session(tmp_path)
    tenant = f"t-{uuid4().hex[:8]}"
    owner = _user("owner", tenant)
    try:
        doc = _make_doc(session, tenant_id=tenant)
        _parent_chunk(session, doc, content="被替换章节。")
        provider = ScriptedProvider(replies=["摘要"])
        job = mod.create_summary_job(session, owner, doc.id)
        await mod.run_summary_job(session, job, provider)
        summary = session.exec(select(SectionSummary).where(SectionSummary.document_id == doc.id)).first()
        doc.is_current = False
        session.add(doc)
        session.commit()
        with pytest.raises(PermissionError):
            mod.get_readable_summary(session, summary.id, owner)
    finally:
        session.close()


async def test_in_place_content_hash_change_rejects_old_summary(tmp_path: Path) -> None:
    """P2-1 回归：同一 document row 原地改 content_hash（is_current 仍 True、不软删、
    同租户）——即 `_reindex_document_in_place` 的真实路径——旧 hash 绑定的摘要必须不可读。"""
    mod = _import_module()
    session = _isolated_session(tmp_path)
    tenant = f"t-{uuid4().hex[:8]}"
    owner = _user("owner", tenant)
    try:
        doc = _make_doc(session, tenant_id=tenant, content_hash="hash-v1")
        _parent_chunk(session, doc, content="原地重建前的章节正文。")
        provider = ScriptedProvider(replies=["旧摘要"])
        job = mod.create_summary_job(session, owner, doc.id)
        await mod.run_summary_job(session, job, provider)
        summary = session.exec(select(SectionSummary).where(SectionSummary.document_id == doc.id)).first()
        # hash 一致时可读
        assert mod.get_readable_summary(session, summary.id, owner).id == summary.id
        # 原地重解析：同 row、is_current 仍 True、不软删，只改 content_hash。
        doc.content_hash = "hash-v2-reindexed"
        session.add(doc)
        session.commit()
        with pytest.raises(PermissionError):
            mod.get_readable_summary(session, summary.id, owner)
        # 摘要行仍在（不物理清理）
        assert session.get(SectionSummary, summary.id) is not None
    finally:
        session.close()


async def test_context_budget_guard_blocks_before_provider_call(tmp_path: Path) -> None:
    """O-4 回归：注入能力后，RAG-028 预算 Guard 在发请求前拦截（输出预留超限），
    provider 零调用，章节 failed(reason=context_budget_exceeded)。"""
    from app.llm.capabilities import GenerationCapability

    mod = _import_module()
    session = _isolated_session(tmp_path)
    tenant = f"t-{uuid4().hex[:8]}"
    owner = _user("owner", tenant)
    try:
        doc = _make_doc(session, tenant_id=tenant)
        _parent_chunk(session, doc, content="预算拦截章节正文。")
        # max_output_tokens=100 < 默认输出预留 256 → OUTPUT_RESERVE_EXCEEDED。
        cap = GenerationCapability(
            deployment="test", model="scripted-summary-model",
            context_window=1000, max_output_tokens=100,
            counter=lambda messages: 10, counting_method="utf8-bytes",
            safety_margin=0, source="test", verified_on="2026-10-09", version="test-v1",
        )
        provider = ScriptedProvider(replies=["不应被使用的摘要"])
        job = mod.create_summary_job(session, owner, doc.id)
        await mod.run_summary_job(session, job, provider, capability=cap)
        assert provider.calls == 0, "预算 Guard 必须在发请求前拦截"
        summary = session.exec(select(SectionSummary).where(SectionSummary.document_id == doc.id)).first()
        assert summary is not None and summary.status == SectionSummaryStatus.FAILED.value
        assert summary.error == mod.REASON_CONTEXT_BUDGET
    finally:
        session.close()


# ── 边界 4：身份与绑定 ─────────────────────────────────────────────────────
async def test_source_chunk_ids_point_to_real_parent(tmp_path: Path) -> None:
    mod = _import_module()
    session = _isolated_session(tmp_path)
    tenant = f"t-{uuid4().hex[:8]}"
    owner = _user("owner", tenant)
    try:
        doc = _make_doc(session, tenant_id=tenant)
        parent = _parent_chunk(session, doc, content="回查章节正文。")
        child = DocumentChunk(
            id=f"chd-{uuid4().hex[:8]}", tenant_id=tenant, document_id=doc.id,
            content="子块正文", parent_id=parent.id, chunk_metadata='{"kind":"child"}',
        )
        session.add(child)
        session.commit()
        provider = ScriptedProvider(replies=["摘要"])
        job = mod.create_summary_job(session, owner, doc.id)
        await mod.run_summary_job(session, job, provider)
        summary = session.exec(select(SectionSummary).where(SectionSummary.document_id == doc.id)).first()
        ids = json.loads(summary.source_chunk_ids or "[]")
        assert parent.id in ids and child.id in ids, "支撑原文 ID 应含父块与子块，可回查"
    finally:
        session.close()


# ── 边界 5：回滚与幂等 ────────────────────────────────────────────────────
def test_default_off_config_flag_exists() -> None:
    assert settings.RAG_SECTION_SUMMARY_ENABLED is False, "章节摘要必须默认关闭"
