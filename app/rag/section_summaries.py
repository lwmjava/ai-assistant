"""RAG-040 版本绑定章节摘要。

章节摘要是**可选、版本绑定的派生数据**（ADR-0005 §3/§9）：

- 原文块仍是事实与引用依据；摘要不进检索向量、不另建摘要检索系统、不替代原文。
- 每条摘要绑定源文档版本（``Document.content_hash``）、切分计划版本与模型/Prompt/协议版本。
- 读取**继承源文档授权**：源版本软删 / 被替换（非当前）/ 跨租户时立即不可读；
  本模块不自行物理删除摘要（沿源文档保留政策，例外另行批准）。
- 所有 LLM 调用经有界计数器（单任务 ``max_calls``，费用护栏）与 RAG-028 上下文预算 Guard；
  预算不足以发下一次请求即停。LLM 不可用 / 超时 / 坏输出只把该章节标记为 failed，
  **绝不影响原文块与原文检索路径**。
- 默认关闭（``RAG_SECTION_SUMMARY_ENABLED=False``）。

本模块不直接持有密钥；``ChapterSummarizer`` 接收注入的 ``LLMProvider``，
便于用合成 provider 做隔离验证（本卡无真实收费调用授权）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from sqlmodel import Session, select

from app.core.config import settings
from app.llm.base import ChatMessage, ChatRole, LLMOptions, LLMProvider
from app.llm.budget import ContextBudgetError, enforce_generation_budget
from app.llm.capabilities import GenerationCapability
from app.models.rag import (
    Document,
    DocumentChunk,
    SectionSummary,
    SectionSummaryJob,
    SectionSummaryJobStatus,
    SectionSummaryStatus,
)
from app.models.user import User
from app.rag.access import can_read_document, can_write_document

logger = logging.getLogger(__name__)

PROMPT_VERSION = "section-summary-prompt-v0.1"
PROTOCOL_VERSION = "section-summary-protocol-v0.1"

# 失败原因码（稳定字符串，进 job.error / summary.error 与评测报告）。
REASON_BUDGET = "call_budget_exhausted"
REASON_TIMEOUT = "provider_timeout"
REASON_PROVIDER_ERROR = "provider_error"
REASON_CONTEXT_BUDGET = "context_budget_exceeded"
REASON_EMPTY_OUTPUT = "empty_output"
REASON_NO_PARENTS = "no_parent_chunks"

_SYSTEM_PROMPT = (
    "你是知识库章节摘要助手。请用简洁中文概括给定章节的核心要点，"
    "不要编造原文没有的事实，不要输出解释或 Markdown，只输出摘要正文。"
)


def _chunk_plan_version(raw: str | None) -> str:
    """从版本化切分计划 JSON 取版本号；缺失/损坏按 legacy 处理，不抛。"""
    if not raw:
        return "legacy"
    try:
        return str(json.loads(raw).get("version") or "legacy")
    except (ValueError, TypeError):
        return "legacy"


def _is_parent_chunk(chunk: DocumentChunk) -> bool:
    """按 chunk_metadata 判定父块（章节）。解析失败一律不当父块。"""
    raw = chunk.chunk_metadata or ""
    try:
        return bool(raw) and json.loads(raw).get("kind") == "parent"
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class ChapterSummaryOutcome:
    """单章节摘要结果。``kind``: ready / empty / failed。"""

    kind: str
    text: str = ""
    reason: str = ""


class ChapterSummarizer:
    """对单任务有界的章节摘要器。每个任务应新建一个实例（独立计数器）。"""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        max_calls: int,
        max_chars_per_batch: int,
        output_tokens: int,
        timeout: float,
        capability: GenerationCapability | None = None,
    ) -> None:
        self._provider = provider
        self._max_calls = max(1, int(max_calls))
        self._max_chars = max(1, int(max_chars_per_batch))
        self._output_tokens = max(1, int(output_tokens))
        self._timeout = float(timeout)
        self._capability = capability
        self._calls = 0

    @property
    def calls_used(self) -> int:
        return self._calls

    def metadata_snapshot(self) -> dict[str, object]:
        """不含正文/密钥的可记录描述。"""
        return {
            "section_summary_model": getattr(self._provider, "model", "unknown"),
            "section_summary_prompt_version": PROMPT_VERSION,
            "section_summary_protocol_version": PROTOCOL_VERSION,
            "section_summary_max_calls": self._max_calls,
        }

    def _split_batches(self, text: str) -> list[str]:
        """把章节正文切成不超过单批预算的片段（按字符窗，不重叠）。"""
        if len(text) <= self._max_chars:
            return [text]
        return [text[i : i + self._max_chars] for i in range(0, len(text), self._max_chars)]

    async def _call(self, user_text: str) -> ChapterSummaryOutcome:
        """发一次摘要请求；预算/超时/错误一律降级为 failed，不向上抛。"""
        if self._calls >= self._max_calls:
            return ChapterSummaryOutcome(kind="failed", reason=REASON_BUDGET)
        messages = [
            ChatMessage(role=ChatRole.SYSTEM, content=_SYSTEM_PROMPT),
            ChatMessage(role=ChatRole.USER, content=user_text),
        ]
        if self._capability is not None:
            try:
                enforce_generation_budget(
                    self._capability, messages, self._output_tokens,
                    settings.LLM_BUDGET_SAFETY_MARGIN,
                )
            except ContextBudgetError:
                return ChapterSummaryOutcome(kind="failed", reason=REASON_CONTEXT_BUDGET)
        options = LLMOptions(
            temperature=0.0, max_tokens=self._output_tokens, timeout=self._timeout,
        )
        self._calls += 1
        try:
            raw = await self._provider.chat(messages, options)
        except TimeoutError:
            logger.warning("section_summary_timeout")
            return ChapterSummaryOutcome(kind="failed", reason=REASON_TIMEOUT)
        except Exception:  # noqa: BLE001 — 网络/解析错误一律降级，不影响原文
            logger.warning("section_summary_provider_error")
            return ChapterSummaryOutcome(kind="failed", reason=REASON_PROVIDER_ERROR)
        text = (raw or "").strip()
        if not text:
            return ChapterSummaryOutcome(kind="failed", reason=REASON_EMPTY_OUTPUT)
        return ChapterSummaryOutcome(kind="ready", text=text)

    async def summarize(self, text: str) -> ChapterSummaryOutcome:
        """概括一个章节；空章节不发请求；超长切批后合并。"""
        body = (text or "").strip()
        if not body:
            return ChapterSummaryOutcome(kind="empty")
        batches = self._split_batches(body)
        partials: list[str] = []
        for batch in batches:
            outcome = await self._call(batch)
            if outcome.kind != "ready":
                return outcome
            partials.append(outcome.text)
        if len(partials) == 1:
            return ChapterSummaryOutcome(kind="ready", text=partials[0])
        # 多批：合并为一条章节摘要（再发一次，仍受 max_calls 约束）。
        merged = await self._call("请把以下各批摘要合并成一条连贯的章节摘要：\n" + "\n\n".join(partials))
        return merged


def create_summary_job(session: Session, user: User, document_id: str) -> SectionSummaryJob:
    """为某文档当前版本创建章节摘要任务（pending）。"""
    doc = session.get(Document, document_id)
    if doc is None or not can_write_document(doc, user):
        raise PermissionError("文档不存在或无权操作")
    job = SectionSummaryJob(
        tenant_id=doc.tenant_id,
        document_id=doc.id,
        status=SectionSummaryJobStatus.PENDING.value,
        source_version_hash=doc.content_hash or "",
        chunk_plan_version=_chunk_plan_version(doc.chunk_plan),
        max_calls=settings.RAG_SECTION_SUMMARY_MAX_CALLS,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def _existing_ready(
    session: Session, *, doc: Document, parent_id: str, model: str
) -> SectionSummary | None:
    """按绑定键找已 ready 的摘要；命中即复用，避免重复副作用。"""
    stmt = select(SectionSummary).where(
        SectionSummary.document_id == doc.id,
        SectionSummary.chunk_id == parent_id,
        SectionSummary.source_version_hash == (doc.content_hash or ""),
        SectionSummary.chunk_plan_version == _chunk_plan_version(doc.chunk_plan),
        SectionSummary.model == model,
        SectionSummary.prompt_version == PROMPT_VERSION,
        SectionSummary.status == SectionSummaryStatus.READY.value,
    )
    return session.exec(stmt).first()


async def run_summary_job(
    session: Session,
    job: SectionSummaryJob,
    provider: LLMProvider,
    *,
    max_calls: int | None = None,
    max_chars_per_batch: int | None = None,
    output_tokens: int | None = None,
    timeout: float | None = None,
    capability: GenerationCapability | None = None,
) -> SectionSummaryJob:
    """执行摘要任务：逐父块生成；任何失败都不触碰原文块与检索路径。"""
    job.status = SectionSummaryJobStatus.RUNNING.value
    job.attempt_count += 1
    job.model = getattr(provider, "model", "")
    job.prompt_version = PROMPT_VERSION
    job.protocol_version = PROTOCOL_VERSION
    session.add(job)
    session.commit()
    session.refresh(job)

    doc = session.get(Document, job.document_id)
    if doc is None or doc.deleted_at is not None:
        job.status = SectionSummaryJobStatus.FAILED.value
        job.error = "源文档不存在或已软删"
        session.add(job)
        session.commit()
        session.refresh(job)
        return job

    parents = [
        chunk
        for chunk in session.exec(
            select(DocumentChunk).where(DocumentChunk.document_id == doc.id)
        ).all()
        if _is_parent_chunk(chunk)
    ]
    job.chapters_total = len(parents)
    if not parents:
        job.status = SectionSummaryJobStatus.FAILED.value
        job.error = REASON_NO_PARENTS
        session.add(job)
        session.commit()
        session.refresh(job)
        return job

    summarizer = ChapterSummarizer(
        provider,
        max_calls=max_calls or settings.RAG_SECTION_SUMMARY_MAX_CALLS,
        max_chars_per_batch=max_chars_per_batch or settings.RAG_SECTION_SUMMARY_MAX_CHARS_PER_BATCH,
        output_tokens=output_tokens or settings.RAG_SECTION_SUMMARY_MAX_OUTPUT_TOKENS,
        timeout=timeout or settings.RAG_SECTION_SUMMARY_TIMEOUT_SECONDS,
        capability=capability,
    )

    ready = 0
    failed = 0
    for parent in parents:
        if not (parent.content or "").strip():
            continue  # 空章节：不发请求、不建行（数值边界）
        existing = _existing_ready(session, doc=doc, parent_id=parent.id, model=job.model)
        if existing is not None:
            ready += 1
            continue
        outcome = await summarizer.summarize(parent.content or "")
        children = session.exec(
            select(DocumentChunk).where(DocumentChunk.parent_id == parent.id)
        ).all()
        source_ids = [parent.id] + [child.id for child in children]
        if outcome.kind == "ready":
            session.add(SectionSummary(
                tenant_id=doc.tenant_id,
                document_id=doc.id,
                chunk_id=parent.id,
                source_version_hash=doc.content_hash or "",
                chunk_plan_version=_chunk_plan_version(doc.chunk_plan),
                model=job.model,
                prompt_version=PROMPT_VERSION,
                protocol_version=PROTOCOL_VERSION,
                summary_text=outcome.text,
                source_chunk_ids=json.dumps(source_ids, ensure_ascii=False),
                status=SectionSummaryStatus.READY.value,
            ))
            ready += 1
        else:
            session.add(SectionSummary(
                tenant_id=doc.tenant_id,
                document_id=doc.id,
                chunk_id=parent.id,
                source_version_hash=doc.content_hash or "",
                chunk_plan_version=_chunk_plan_version(doc.chunk_plan),
                model=job.model,
                prompt_version=PROMPT_VERSION,
                protocol_version=PROTOCOL_VERSION,
                source_chunk_ids=json.dumps(source_ids, ensure_ascii=False),
                status=SectionSummaryStatus.FAILED.value,
                error=outcome.reason,
            ))
            failed += 1

    job.calls_used = summarizer.calls_used
    job.chapters_ready = ready
    job.chapters_failed = failed
    if failed == 0 and ready > 0:
        job.status = SectionSummaryJobStatus.SUCCESS.value
    elif failed > 0 and ready > 0:
        job.status = SectionSummaryJobStatus.PARTIAL.value
    else:
        job.status = SectionSummaryJobStatus.FAILED.value
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def get_readable_summary(session: Session, summary_id: str, user: User) -> SectionSummary:
    """读取一条摘要；授权继承源文档，且版本绑定必须与当前源一致。

    拒绝条件（任一即 ``PermissionError``，且**不物理删除**摘要行）：
    - 摘要不存在；
    - 源文档不可读：软删 / 非当前版 / 跨租户（复用 ``can_read_document``）；
    - **版本绑定不新鲜**：``summary.source_version_hash != doc.content_hash`` 或
      ``chunk_plan_version`` 不一致。``_reindex_document_in_place`` 会在同一 document
      row 上原地改 ``content_hash`` 且保持 ``is_current=True``，此时旧 hash 绑定的
      摘要（chunk_id 已指向被替换的父块、内容不再匹配）必须不可读。
    """
    summary = session.get(SectionSummary, summary_id)
    if summary is None:
        raise PermissionError("摘要不存在")
    doc = session.get(Document, summary.document_id)
    if doc is None or not can_read_document(doc, user):
        raise PermissionError("摘要不存在或无权访问")
    if summary.source_version_hash != (doc.content_hash or ""):
        raise PermissionError("摘要绑定的源版本已变更，不可读")
    if summary.chunk_plan_version != _chunk_plan_version(doc.chunk_plan):
        raise PermissionError("摘要绑定的切分计划版本已变更，不可读")
    return summary
