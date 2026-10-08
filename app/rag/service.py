"""RAG 服务：串联摄取、检索与文档管理，并对接 Agent 管线。

职责：
- 文档摄取：文本分块 → 批量嵌入 → 持久化为 Document / DocumentChunk；
- 混合检索：将查询转为向量与词项，调用向量库融合检索；
- 文档生命周期：列表 / 详情 / 删除（级联删除分块与向量索引）；
- 生成管线可用的检索钩子（``make_retriever``）。

检索面默认同租户当前版本可读。
控制面：成员仅自己的当前版；租户管理员看本租户全部版本；系统管理员可跨租户。
"""

import asyncio
import json
import logging
import os
import re
import uuid
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import Literal, TypedDict

from sqlalchemy import tuple_
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.json_logging import JsonLogFormatter
from app.models.rag import (
    Document,
    DocumentChunk,
    DocumentIngestionSnapshot,
    EmbeddingIndex,
    ImportJob,
    IndexStatus,
)
from app.models.user import User
from app.rag.access import (
    ReadScope,
    can_control_document,
    can_read_document,
    can_write_document,
    is_kb_admin,
    read_scope_for,
    restrict_list_to_uploader,
)
from app.rag.backend.base import RagBackend
from app.rag.backend.factory import get_rag_backend, normalize_rag_backend
from app.rag.chunking.base import Chunk, ChunkParams
from app.rag.chunking.factory import (
    get_chunking_strategy,
    resolve_strategy_with_decision,
)
from app.rag.chunking.plan import load_plan, params_from_plan, validate_params
from app.rag.chunking.routing import ROUTING_VERSION, RoutingDecision, route, signals_from_parsed
from app.rag.cleaning import CleaningResult, clean_document, text_hash
from app.rag.document_parsers.base import ParsedDocument
from app.rag.document_storage import delete_source_file, resolve_source_file_path
from app.rag.effective_date import retrieval_window
from app.rag.embeddings.base import EmbeddingProvider
from app.rag.embeddings.factory import get_embedding_provider
from app.rag.embeddings.mock import tokenize
from app.rag.import_trace import ImportTraceError
from app.rag.index_identity import (
    IndexIdentityError,
    identity_from_provider,
    identity_matches_row,
)
from app.rag.index_registry import (
    active_index,
    resolve_read_index_for,
    resolve_write_index,
)
from app.rag.retrieval_guard import drop_injected_chunks
from app.rag.retriever import HybridRetriever
from app.rag.vectorstore.base import ChunkResult, VectorStore, l2_normalize
from app.rag.vectorstore.factory import get_vector_store
from app.rag.vectorstore.local import LocalVectorStore, visible_chunks_with_status
from app.rag.vectorstore.milvus import MilvusVectorStore
from app.services.quota import source_file_size


def demote_other_current_versions(session: Session, version_group_id: str, *, keep_id: str) -> None:
    """同一版本组只保留 keep_id 为当前版。调用方负责提交。"""
    rows = session.exec(
        select(Document).where(
            Document.version_group_id == version_group_id,
            col(Document.is_current).is_(True),
            Document.id != keep_id,
        )
    ).all()
    for row in rows:
        row.is_current = False
        row.version_state = "replaced"
        session.add(row)


logger = logging.getLogger(__name__)

Tokenizer = Callable[[str], list[str]]


class ChunkVectorizationState(TypedDict):
    embedding_status: Literal["vectorized", "not_vectorized", "unknown"]
    embedding_skip_reason: str | None
    oversized: bool


class DocumentVectorizationSummary(TypedDict):
    vectorization_status: Literal["vectorized", "partial", "not_vectorized", "unknown"]
    vectorized_chunk_count: int
    not_vectorized_chunk_count: int
    unknown_chunk_count: int
    embedding_skip_reason_counts: dict[str, int]


def chunk_vectorization_state(has_embedding: bool, raw_metadata: str | None) -> ChunkVectorizationState:
    """Expose stable state and allowlisted reasons, never arbitrary metadata."""
    metadata: dict = {}
    if raw_metadata:
        try:
            parsed = json.loads(raw_metadata)
            if isinstance(parsed, dict):
                metadata = parsed
        except (TypeError, json.JSONDecodeError):
            pass
    oversized = metadata.get("oversized") is True
    if has_embedding:
        return {"embedding_status": "vectorized", "embedding_skip_reason": None, "oversized": oversized}
    reason = metadata.get("embedding_skip_reason")
    if isinstance(reason, str) and reason:
        safe_reason = (
            reason
            if reason in {"input_limit_exceeded", "input_limit_unverified", "input_count_unverified"}
            else "unknown_reason"
        )
        return {"embedding_status": "not_vectorized", "embedding_skip_reason": safe_reason, "oversized": oversized}
    if metadata.get("embedding_status") == "not_vectorized":
        return {"embedding_status": "not_vectorized", "embedding_skip_reason": "unknown_reason", "oversized": oversized}
    return {"embedding_status": "unknown", "embedding_skip_reason": None, "oversized": oversized}


def sources_from_hits(session: Session, hits: list[ChunkResult]) -> list[dict]:
    """把检索命中转成来源列表。页码和段落只取分块元数据里已有的值。"""
    seen: set[tuple[str, int | None, str | None, str]] = set()
    sources: list[dict] = []
    for hit in hits:
        row = session.get(DocumentChunk, hit.id)
        filename = (hit.source or (row.source if row else None) or "").strip()
        if not filename and row is not None:
            document = session.get(Document, row.document_id)
            filename = (document.title or "").strip() if document else ""
        if not filename:
            continue
        metadata = _chunk_metadata(row)
        page = _source_page(metadata.get("page"))
        section = _source_section(metadata.get("section_path"))
        key = (filename, page, section, hit.id)
        if key in seen:
            continue
        seen.add(key)
        sources.append(
            {
                "filename": filename,
                "page": page,
                "section": section,
                "chunk_id": hit.id,
                "document_id": hit.document_id,
                "excerpt": _source_excerpt(hit.content),
            }
        )
    return sources


def _strip_markdown(content: str) -> str:
    """把 markdown 源码清洗为可读纯文本：去标题/表格管道/分隔行/引用/列表/粗体/行内代码。"""
    out: list[str] = []
    for line in content.splitlines():
        s = line
        if re.match(r'^\s*\|?[\s:\-|]+\|?\s*$', s):
            continue
        s = re.sub(r'^\s{0,3}#{1,6}\s+', '', s)
        s = re.sub(r'^\s*>\s?', '', s)
        s = re.sub(r'^\s*([-*+]|\d+[.)])\s+', '', s)
        if '|' in s:
            cells = [c.strip() for c in s.split('|') if c.strip()]
            cells = [c for c in cells if not re.fullmatch(r'[\s:\-]+', c)]
            s = ' · '.join(cells)
        s = re.sub(r'\*\*([^*]+)\*\*', r'\1', s)
        s = re.sub(r'`([^`]+)`', r'\1', s)
        if s.strip():
            out.append(s.strip())
    text = ' '.join(out)
    return ' '.join(text.split())


def _source_excerpt(content: str, limit: int = 240) -> str:
    """摘录原文：清洗 markdown 后折叠空白并截断到 limit，供引用原文展示。"""
    text = _strip_markdown(content)
    return text[:limit] if len(text) > limit else text


def _chunk_metadata(row: DocumentChunk | None) -> dict:
    if row is None or not row.chunk_metadata:
        return {}
    try:
        parsed = json.loads(row.chunk_metadata)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _source_page(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 1:
        return None
    return value


def _source_section(value: object) -> str | None:
    if not isinstance(value, list):
        return None
    parts = [str(part).strip() for part in value if str(part).strip()]
    if not parts:
        return None
    return "/".join(parts)


class RAGService:
    """检索增强生成服务（会话级，绑定一个数据库会话与租户）。"""

    # 类级默认值：未提供鉴权主体时沿用仅按租户过滤的历史行为。
    # 以 ``__new__`` 绕过构造的调用路径也能取到该属性，不会因缺属性而崩溃。
    _read_scope: ReadScope | None = None
    # 上一次 search 实际生效的后端名。后端工厂会静默降级（缺依赖时
    # langchain/llamaindex → native），调用方需要能看到真实生效值。
    last_backend_name: str = ""

    def __init__(
        self,
        session: Session,
        tenant_id: str,
        embedding_provider: EmbeddingProvider | None = None,
        vector_store: VectorStore | None = None,
        *,
        tokenizer: Tokenizer | None = None,
        backend: RagBackend | None = None,
        reader: User | None = None,
        write_index_id: str | None = None,
    ) -> None:
        self.session = session
        self.tenant_id = tenant_id
        # 检索面的鉴权主体。默认不传（None）时等同同租户全可读（历史行为），
        # 不构成 uploader 隔离；面向终端用户的入口必须显式传入 reader。
        self._read_scope = read_scope_for(reader)
        if self._read_scope is not None:
            self._read_scope.assert_subject_tenant(tenant_id)
        self._embedding = embedding_provider or get_embedding_provider()
        self._vector_store = vector_store or get_vector_store(session)
        self._tokenizer = tokenizer or tokenize
        self._default_backend_name = settings.RAG_BACKEND
        self._backend = backend or get_rag_backend(
            self._embedding,
            self._vector_store,
            backend=self._default_backend_name,
            tokenizer=self._tokenizer,
            rrf_k=settings.RAG_HYBRID_RRF_K,
        )
        # 本次写入归属的索引；惰性解析，读取面不需要它。
        self._index: EmbeddingIndex | None = None
        # 显式写目标：重建期间写 preparing 索引（旧 active 继续服务）。
        # None 表示写当前 active 索引。
        self._write_index_id = write_index_id
        self._reindex_external_attempt: tuple[str, str, list[str]] | None = None

    def _resolve_backend(self, backend: str | None = None) -> RagBackend:
        """按请求级覆盖或默认配置返回后端实例。"""
        if backend is None:
            return self._backend
        normalized = normalize_rag_backend(backend)
        if normalized == self._backend.name:
            return self._backend
        return get_rag_backend(
            self._embedding,
            self._vector_store,
            backend=normalized,
            tokenizer=self._tokenizer,
            rrf_k=settings.RAG_HYBRID_RRF_K,
        )

    # ── 摄取 ────────────────────────────────────────
    def _build_chunk_params(self, chunk_params: dict | None = None) -> ChunkParams:
        """构造切分参数，合并全局默认值与请求级覆盖。"""
        overrides = dict(chunk_params or {})
        if "input_policy" in overrides:
            raise ValueError("input_policy is server controlled")
        values = {"chunk_size": settings.RAG_CHUNK_SIZE, "chunk_overlap": settings.RAG_CHUNK_OVERLAP, **overrides}
        return ChunkParams(**values, input_policy=self._embedding.input_policy)

    @staticmethod
    def _build_chunk_plan(decision: RoutingDecision, params: ChunkParams) -> dict:
        """构造版本化切分计划 JSON：策略名 + 有效参数 + 路由原因。"""
        from dataclasses import asdict

        values = {k: v for k, v in asdict(params).items() if k != "input_policy"}
        validate_params(values, complete=True)
        return {
            "version": decision.version,
            "strategy": decision.strategy,
            "routing_reason": decision.reason,
            "chunk_params": values,
        }

    @staticmethod
    def _load_chunk_plan(raw: str | None) -> dict | None:
        """仅NULL计划走兼容；损坏或未知版本拒绝且保留旧数据。"""
        return load_plan(raw)

    def _params_from_plan(self, plan: dict) -> ChunkParams:
        """从持久化计划重建 ChunkParams；input_policy 由服务端重新注入。"""
        return params_from_plan(plan, self._embedding.input_policy)

    @staticmethod
    def _attach_routing_metadata(chunks: list[Chunk], decision: RoutingDecision) -> None:
        """把路由决策写入每个 chunk 的 metadata，便于审计与重放。"""
        for chunk in chunks:
            chunk.metadata["routing_version"] = decision.version
            chunk.metadata["routing_reason"] = decision.reason
            signals = decision.signals
            chunk.metadata["routing_signals"] = {
                "has_blocks": signals.has_blocks,
                "has_bbox": signals.has_bbox,
                "has_headings": signals.has_headings,
                "has_structure_units": signals.has_structure_units,
                "char_count": signals.char_count,
            }

    @staticmethod
    def _decide_reindex_strategy(
        previous_strategies: list[str | None],
        parsed: ParsedDocument,
    ) -> RoutingDecision:
        """重解析决策：旧 chunks 策略一致则复用，否则走路由兼容路径。"""
        non_null = [s for s in previous_strategies if s]
        if non_null and len(set(non_null)) == 1:
            previous = non_null[0]
            return RoutingDecision(
                strategy=previous,
                reason="reuse_previous_strategy",
                signals=signals_from_parsed(parsed),
                version=ROUTING_VERSION,
            )
        signals = signals_from_parsed(parsed)
        configured = getattr(settings, "RAG_CHUNK_STRATEGY", "structured")
        long_threshold = max(1, settings.RAG_CHUNK_SIZE * 2)
        return route(
            signals,
            requested=None,
            configured_default=configured,
            long_threshold_chars=long_threshold,
        )

    async def _persist_document(
        self,
        *,
        title: str,
        source: str | None,
        user_id: str,
        chunk_objs: list,
        strategy_name: str,
        storage_path: str | None = None,
        source_kind: str | None = None,
        source_uri: str | None = None,
        content_hash: str | None = None,
        version_group_id: str | None = None,
        version_number: int = 1,
        previous_document_id: str | None = None,
        import_job_id: str | None = None,
        is_current: bool = True,
        version_state: str | None = None,
        effective_at: datetime | None = None,
        expires_at: datetime | None = None,
        cleaning: CleaningResult | None = None,
        chunk_plan: dict | None = None,
    ) -> Document:
        """将切分结果持久化为文档与分块。"""
        if not chunk_objs:
            raise ValueError("文本为空或无法切分为任何分块")

        # 先做嵌入再开写事务：避免网络调用期间长时间占用 SQLite 写锁，
        # 也避免「文档已提交、分块/向量失败」留下 chunk_count=0 的孤儿记录。
        try:
            embeddings = await self._embed_chunks(chunk_objs)
            source_bytes = source_file_size(storage_path) if storage_path else None
            document = Document(
                tenant_id=self.tenant_id,
                user_id=user_id,
                title=title,
                source=source,
                storage_path=storage_path,
                source_bytes=source_bytes,
                source_kind=source_kind or "file",
                source_uri=source_uri,
                content_hash=content_hash,
                version_group_id=version_group_id or uuid.uuid4().hex,
                version_number=version_number,
                previous_document_id=previous_document_id,
                import_job_id=import_job_id,
                is_current=is_current,
                version_state=version_state or ("published" if is_current else "replaced"),
                effective_at=effective_at,
                expires_at=expires_at,
                chunk_count=len(chunk_objs),
                chunk_plan=json.dumps(chunk_plan, ensure_ascii=False) if chunk_plan else None,
            )
            self.session.add(document)
            self.session.flush()
            if cleaning is not None:
                self.session.add(
                    DocumentIngestionSnapshot(
                        document_id=document.id,
                        tenant_id=self.tenant_id,
                        original_text=cleaning.original.text,
                        original_blocks=json.dumps(
                            [asdict(block) for block in cleaning.original.blocks], ensure_ascii=False
                        ),
                        cleaning_report=json.dumps(cleaning.report, ensure_ascii=False),
                    )
                )

            parent_id_map: dict[str, str] = {}
            persisted_rows: list[DocumentChunk] = []
            write_index = self._write_index()
            expected_dim = write_index.dim
            for chunk_index, (chunk, vector) in enumerate(zip(chunk_objs, embeddings, strict=True)):
                # 手动生成主键，便于父块落库后直接回填子块的 parent_id，无需逐块 flush。
                row_id = uuid.uuid4().hex
                parent_id = None
                if chunk.parent_id and chunk.parent_id in parent_id_map:
                    parent_id = parent_id_map[chunk.parent_id]
                metadata = dict(chunk.metadata or {})
                if cleaning is not None:
                    metadata["cleaning"] = cleaning.report
                # 写入侧固化 L2 归一化：检索侧点积=余弦成立的前提是入库向量已归一。
                # 维度与登记索引不符时直接拒绝，不把异维向量写进索引。
                normalized_vector: list[float] | None = None
                if vector is not None:
                    if len(vector) != expected_dim:
                        raise IndexIdentityError(
                            f"写入向量维度 {len(vector)} 与登记索引 {expected_dim} 不符"
                        )
                    normalized_vector = l2_normalize(vector)
                row = DocumentChunk(
                    id=row_id,
                    tenant_id=self.tenant_id,
                    document_id=document.id,
                    chunk_index=chunk_index,
                    content=chunk.text,
                    source=source,
                    embedding=(
                        json.dumps(normalized_vector, ensure_ascii=False)
                        if normalized_vector is not None else None
                    ),
                    tokens=json.dumps(self._tokenizer(chunk.text), ensure_ascii=False),
                    strategy=strategy_name,
                    chunk_metadata=json.dumps(metadata, ensure_ascii=False) if metadata else None,
                    parent_id=parent_id,
                    index_id=write_index.id,
                )
                if metadata.get("kind") == "parent":
                    parent_id_map[str(metadata.get("parent_key"))] = row_id
                self.session.add(row)
                persisted_rows.append(row)

            # 必须在提交之前写：外部向量库写失败要随事务一起回滚，否则会留下
            # 「SQL 里有分块、向量库里查不到」的半截状态。本地向量库的 add 是
            # 空操作（分块已随主库持久化），Milvus 实现才真正把向量同步进集合。
            await self._vector_store.add(persisted_rows)

            if document.is_current:
                demote_other_current_versions(self.session, document.version_group_id, keep_id=document.id)
            self.session.commit()
            self.session.refresh(document)
            return document
        except Exception:
            self.session.rollback()
            raise

    async def ingest_text(
        self,
        text: str,
        title: str,
        source: str | None,
        user_id: str,
        *,
        storage_path: str | None = None,
        backend: str | None = None,
        source_kind: str | None = None,
        source_uri: str | None = None,
        content_hash: str | None = None,
        version_group_id: str | None = None,
        version_number: int = 1,
        previous_document_id: str | None = None,
        import_job_id: str | None = None,
        is_current: bool = True,
        version_state: str | None = None,
        effective_at: datetime | None = None,
        expires_at: datetime | None = None,
        strategy: str | None = None,
        chunk_params: dict | None = None,
    ) -> Document:
        """摄取一段文本：切分、嵌入、落库，返回文档记录。

        ``strategy`` 为请求级切分策略名，``chunk_params`` 为策略专用参数，
        均可缺省；缺省时由 ``resolve_strategy_name`` 依据配置与文本特征路由。
        ``backend`` 参数为历史兼容保留，切分已统一走策略层，不再受其影响。
        """
        cleaning = clean_document(ParsedDocument(
            text=text, title=title, source=source or "text", extension="txt",
            content_type="text/plain",
        ))
        text = cleaning.document.text
        decision = resolve_strategy_with_decision(text, strategy)
        strategy_name = decision.strategy
        chunking = get_chunking_strategy(strategy_name, embedding=self._embedding)
        params = self._build_chunk_params(chunk_params)
        chunk_objs = await chunking.split(text, params=params)
        self._attach_routing_metadata(chunk_objs, decision)
        return await self._persist_document(
            title=title,
            source=source,
            user_id=user_id,
            chunk_objs=chunk_objs,
            strategy_name=strategy_name,
            storage_path=storage_path,
            source_kind=source_kind,
            source_uri=source_uri,
            content_hash=content_hash,
            version_group_id=version_group_id,
            version_number=version_number,
            previous_document_id=previous_document_id,
            import_job_id=import_job_id,
            is_current=is_current,
            version_state=version_state,
            effective_at=effective_at,
            expires_at=expires_at,
            cleaning=cleaning,
            chunk_plan=self._build_chunk_plan(decision, params),
        )

    async def ingest_parsed_document(
        self,
        parsed: ParsedDocument,
        *,
        user_id: str,
        title: str | None = None,
        source: str | None = None,
        storage_path: str | None = None,
        backend: str | None = None,
        source_kind: str | None = None,
        source_uri: str | None = None,
        content_hash: str | None = None,
        version_group_id: str | None = None,
        version_number: int = 1,
        previous_document_id: str | None = None,
        import_job_id: str | None = None,
        is_current: bool = True,
        effective_at: datetime | None = None,
        expires_at: datetime | None = None,
        strategy: str | None = None,
        chunk_params: dict | None = None,
    ) -> Document:
        """摄取解析结果；结构感知策略可直接消费 parser 保留的 blocks。"""
        cleaning = clean_document(parsed)
        original_hash = content_hash or text_hash(parsed.text)
        chosen_title = title or parsed.title
        chosen_source = parsed.source if source is None else source
        # 异步版本治理已有原始 hash 去重；同步上传补同来源/上传者去重。
        if (
            import_job_id is None and is_current and version_group_id is None
            and strategy is None and chunk_params is None
            and effective_at is None and expires_at is None
        ):
            existing = self.session.exec(select(Document).where(
                col(Document.tenant_id) == self.tenant_id,
                col(Document.user_id) == user_id,
                col(Document.source) == chosen_source,
                col(Document.content_hash) == original_hash,
                col(Document.deleted_at).is_(None),
                col(Document.is_current).is_(True),
            )).first()
            if existing is not None:
                if storage_path and storage_path != existing.storage_path:
                    if existing.storage_path is None:
                        existing.storage_path = storage_path
                        existing.source_bytes = source_file_size(storage_path)
                        self.session.add(existing)
                    else:
                        referenced_doc = self.session.exec(select(Document).where(
                            col(Document.storage_path) == storage_path
                        )).first()
                        referenced_job = self.session.exec(select(ImportJob).where(
                            col(ImportJob.storage_path) == storage_path
                        )).first()
                        new_path = resolve_source_file_path(storage_path)
                        old_path = resolve_source_file_path(existing.storage_path)
                        if (
                            referenced_doc is not None or referenced_job is not None
                            or new_path.parent != old_path.parent
                        ):
                            raise ValueError("重复上传源文件不是本租户未引用的暂存文件")
                        delete_source_file(storage_path)
                self.session.commit()
                return existing
        parsed = cleaning.document
        decision = resolve_strategy_with_decision(parsed.text, strategy, parsed=parsed)
        strategy_name = decision.strategy
        chunking = get_chunking_strategy(strategy_name, embedding=self._embedding)
        params = self._build_chunk_params(chunk_params)
        if (strategy_name in {"format_aware", "layout_aware"} and parsed.blocks
                and not decision.signals.is_text_parser):
            chunk_objs = await chunking.split_blocks(parsed.blocks, params=params)
        else:
            chunk_objs = await chunking.split(parsed.text, params=params)
        self._attach_routing_metadata(chunk_objs, decision)
        return await self._persist_document(
            title=chosen_title,
            source=chosen_source,
            user_id=user_id,
            chunk_objs=chunk_objs,
            strategy_name=strategy_name,
            storage_path=storage_path,
            source_kind=source_kind,
            source_uri=source_uri,
            content_hash=original_hash,
            version_group_id=version_group_id,
            version_number=version_number,
            previous_document_id=previous_document_id,
            import_job_id=import_job_id,
            is_current=is_current,
            effective_at=effective_at,
            expires_at=expires_at,
            cleaning=cleaning,
            chunk_plan=self._build_chunk_plan(decision, params),
        )

    async def ingest_file(self, path: str, title: str | None, user_id: str) -> Document:
        """摄取本地文本文件（支持 .txt / .md）。"""
        ext = os.path.splitext(path)[1].lower()
        if ext not in (".txt", ".md"):
            raise ValueError("仅支持 .txt / .md 文本文件摄取")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        source = title or os.path.basename(path)
        return await self.ingest_text(text, source, source, user_id)

    def _write_index(self) -> EmbeddingIndex:
        """本次写入所属的向量索引；身份不符时拒绝写入（ADR-0008）。

        ``write_index_id`` 给出时写该目标索引（重建 preparing 索引用），
        但仍要校验目标身份与当前 provider 一致——写进身份不符的索引
        等同于把新模型的向量塞进旧向量空间。

        结果按实例缓存：一次摄取只核对一次，不每个分块查一遍库。
        """
        if self._index is None:
            identity = identity_from_provider(self._embedding)
            if self._write_index_id is not None:
                target = self.session.get(EmbeddingIndex, self._write_index_id)
                if target is None:
                    raise IndexIdentityError(
                        f"写入目标索引 {self._write_index_id} 不存在"
                    )
                if not identity_matches_row(identity, target):
                    raise IndexIdentityError(
                        f"当前嵌入身份 {identity.key()} 与写入目标索引 {target.name} "
                        f"的身份 {target.identity_key} 不一致，拒绝写入"
                    )
                if target.status not in (IndexStatus.ACTIVE.value, IndexStatus.PREPARING.value):
                    raise IndexIdentityError("重建写入目标必须处于 active/preparing 状态")
                self._index = target
            else:
                self._index = resolve_write_index(
                    self.session, identity, commit=False
                )
        return self._index

    async def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        """调用嵌入接口；分批由提供商按厂商上限处理，这里不触碰数据库事务。"""
        vectors = await self._embedding.embed(texts)
        if len(vectors) != len(texts):
            raise RuntimeError(
                f"嵌入结果数量与文本数量不一致：texts={len(texts)} vectors={len(vectors)}"
            )
        return vectors

    async def _embed_chunks(self, chunks: list[Chunk]) -> list[list[float] | None]:
        """Keep excluded source rows, while sending only validated inputs.

        Positions are mapped explicitly: skipped chunks must not shift vectors
        or parent identifiers. No zero vectors are manufactured for exclusions.
        """
        policy = self._embedding.input_policy
        vectors: list[list[float] | None] = [None] * len(chunks)
        selected: list[int] = []
        for index, chunk in enumerate(chunks):
            reason = policy.check(chunk.text) if policy is not None else "input_limit_unverified"
            chunk.metadata["embedding_policy"] = (
                policy.metadata()
                if policy is not None
                else {
                    "version": "input-policy-v1",
                    "counting_method": "unverified",
                }
            )
            if reason:
                chunk.metadata["embedding_status"] = "not_vectorized"
                chunk.metadata["embedding_skip_reason"] = reason
                if reason == "input_limit_exceeded":
                    chunk.metadata["oversized"] = True
            else:
                selected.append(index)
        if selected:
            embedded = await self._embed_texts([chunks[index].text for index in selected])
            for index, vector in zip(selected, embedded, strict=True):
                vectors[index] = vector
                chunks[index].metadata["embedding_status"] = "vectorized"
                chunks[index].metadata.pop("embedding_skip_reason", None)
        logger.info(
            "rag_chunk_vectorization total=%s vectorized=%s not_vectorized=%s",
            len(chunks),
            len(selected),
            len(chunks) - len(selected),
        )
        return vectors

    # ── 检索 ────────────────────────────────────────
    async def search(
        self, query: str, top_k: int | None = None, *, backend: str | None = None
    ) -> list[ChunkResult]:
        """对查询做混合检索，命中子块时展开父块上下文并去重。"""
        top_k = top_k or settings.RAG_TOP_K
        rag_backend = self._resolve_backend(backend)
        requested_backend = (backend or self._default_backend_name or "native").strip().lower()
        if requested_backend not in ("native", "langchain", "llamaindex"):
            requested_backend = "unknown"
        # Uvicorn 的 handler 不挂在根 logger 上；仅 lastResort 时 INFO 不会输出。
        if not logger.hasHandlers():
            handler = logging.StreamHandler()
            handler.setFormatter(JsonLogFormatter())
            logger.addHandler(handler)
        self.last_backend_name = rag_backend.name
        logger.info(
            "rag_search_backend requested=%s effective=%s implementation=%s fallback=%s",
            requested_backend,
            rag_backend.name,
            type(rag_backend).__name__,
            str(requested_backend != rag_backend.name).lower(),
        )
        hits = await rag_backend.retrieve(
            query, tenant_id=self.tenant_id, top_k=top_k, read_scope=self._read_scope
        )
        # 读路径已经核对过身份；把生效索引带进父块复核，避免复核阶段再查一次库，
        # 也保证展开用的索引与召回用的索引是同一个。
        return await self._expand_parent_chunks(hits, query=query, index_id=self._read_index_id())

    def _read_index_id(self) -> str | None:
        """当前生效索引的 id；没有登记索引时返回 None（不按索引收窄）。

        只为 ``RAGService.__new__`` 这类不带 provider 的单元测桩保留：拿不到
        provider 就算不出查询身份。此时仍以已登记的 active 索引收窄——
        只要库里登记过索引，旧索引的 stale hit 一样进不来。
        """
        embedding = getattr(self, "_embedding", None)
        if embedding is None:
            index = active_index(self.session)
        else:
            index = resolve_read_index_for(
                self.session,
                identity_from_provider(embedding),
                tenant_id=self.tenant_id,
            )
        return index.id if index is not None else None

    async def _expand_parent_chunks(
        self,
        hits: list[ChunkResult],
        *,
        query: str = "",
        index_id: str | None = None,
    ) -> list[ChunkResult]:
        """批量校验命中与父块；真实命中优先，扩展父块继承评分。

        所有回查都限定在当前生效索引：retired 索引的 stale hit 不得在这里
        展开出旧父块，否则身份过滤在检索层做了、在展开层又被绕过去。
        ``index_id`` 由 :meth:`search` 传入（读路径已核对过身份，这里不重复查库）；
        为 None 表示当前没有登记索引，不按索引收窄。
        """
        if not hits:
            return []
        as_of, schedule_at = retrieval_window(query)

        def load_rows(
            ids: set[str], parent_links: set[tuple[str, str]] | None = None,
        ) -> tuple[dict[str, DocumentChunk], dict[str, str]]:
            if not ids:
                return {}, {}
            stmt = (
                select(DocumentChunk, Document)
                .join(Document, col(Document.id) == col(DocumentChunk.document_id))
                .where(
                    col(DocumentChunk.id).in_(ids),
                    col(DocumentChunk.tenant_id) == self.tenant_id,
                    col(Document.tenant_id) == self.tenant_id,
                    col(Document.deleted_at).is_(None),
                )
            )
            if index_id is not None:
                stmt = stmt.where(col(DocumentChunk.index_id) == index_id)
            # uploader 模式下父块与命中块一样按上传者收窄；父块与子块同文档，
            # 权限仍按文档归属判定，不因子块有权就放行他人父正文。
            if self._read_scope is not None and self._read_scope.uploader_id is not None:
                stmt = stmt.where(col(Document.user_id) == self._read_scope.uploader_id)
            if not settings.RAG_EFFECTIVE_DATE_FILTER:
                stmt = stmt.where(col(Document.is_current).is_(True))
            if parent_links is not None:
                stmt = stmt.where(
                    tuple_(col(DocumentChunk.id), col(DocumentChunk.document_id)).in_(parent_links)
                )
            pairs = self.session.exec(stmt.execution_options(populate_existing=True)).all()
            rows, statuses = visible_chunks_with_status(
                self.session, [row for row, doc in pairs], as_of, schedule_at,
                documents={doc.id: doc for row, doc in pairs},
            )
            return {row.id: row for row in rows}, statuses

        hit_rows, statuses = load_rows({hit.id for hit in hits})
        direct: dict[str, ChunkResult] = {}
        for hit in hits:
            row = hit_rows.get(hit.id)
            if row is None or row.document_id != hit.document_id or hit.id in direct:
                continue
            kind = _chunk_metadata(row).get("kind")
            direct[hit.id] = replace(
                hit, content=row.content, source=row.source, parent_id=row.parent_id,
                chunk_kind=(
                    "child" if row.parent_id else "parent" if kind == "parent" else "unknown"
                ),
                version_status=statuses[row.id], retrieval_origin="hit",
                expanded_from_chunk_id=None, score_inherited_from_chunk_id=None,
            )
        safe_hits = drop_injected_chunks(list(direct.values()), keep=len(direct))
        direct = {hit.id: hit for hit in safe_hits}
        parent_ids = {hit.parent_id for hit in safe_hits if hit.parent_id}
        parent_links = {
            (hit.parent_id, hit.document_id) for hit in safe_hits
            if hit.parent_id and hit.parent_id not in hit_rows
        }
        parent_rows, parent_statuses = load_rows(parent_ids - hit_rows.keys(), parent_links)
        parent_rows.update(hit_rows)
        statuses.update(parent_statuses)
        expanded: list[ChunkResult] = []
        seen: set[str] = set()
        for hit in safe_hits:
            if hit.id not in seen:
                seen.add(hit.id)
                expanded.append(hit)
            if hit.parent_id:
                parent_row = parent_rows.get(hit.parent_id)
                if (
                    parent_row is not None
                    and parent_row.document_id == hit.document_id
                    and parent_row.id not in seen
                    and parent_row.id not in direct
                ):
                    seen.add(parent_row.id)
                    expanded.append(
                        ChunkResult(
                            id=parent_row.id,
                            content=parent_row.content,
                            source=parent_row.source,
                            document_id=parent_row.document_id,
                            score=hit.score,
                            similarity=hit.similarity,  # 父块继承子块相似度
                            version_status=statuses[parent_row.id],
                            parent_id=parent_row.parent_id,
                            chunk_kind="parent",
                            retrieval_origin="parent_expansion",
                            expanded_from_chunk_id=hit.id,
                            score_inherited_from_chunk_id=hit.id,
                        )
                    )
        return drop_injected_chunks(expanded, keep=len(expanded))

    def make_retriever(self, top_k: int | None = None) -> HybridRetriever:
        """生成可注入 Agent 管线的混合检索器。"""
        return HybridRetriever(
            self._backend,
            self.tenant_id,
            top_k or settings.RAG_TOP_K,
            read_scope=self._read_scope,
        )

    # ── 文档管理 ────────────────────────────────────
    def _can_access(self, doc: Document, user: User) -> bool:
        """读权限。保留此名以兼容旧调用，语义见 ``can_read_document``。"""
        return can_read_document(doc, user)

    def list_documents(
        self,
        user: User,
        *,
        include_deleted: bool = False,
        version_state: str | None = None,
    ) -> list[Document]:
        """控制面列表。成员只看自己的未删当前版；管理员可看历史版。"""
        from app.core.security import Role

        if user.role_enum == Role.SYSTEM_ADMIN:
            stmt = select(Document)
        elif user.role_enum == Role.TENANT_ADMIN:
            stmt = select(Document).where(Document.tenant_id == user.tenant_id)
        else:
            stmt = select(Document).where(
                Document.tenant_id == user.tenant_id,
                Document.user_id == user.id,
                col(Document.is_current).is_(True),
            )
            if restrict_list_to_uploader(user):
                stmt = stmt.where(Document.user_id == user.id)
        if not (include_deleted and is_kb_admin(user)):
            stmt = stmt.where(col(Document.deleted_at).is_(None))
        if version_state and is_kb_admin(user):
            stmt = stmt.where(Document.version_state == version_state)
        stmt = stmt.order_by(col(Document.updated_at).desc())
        return list(self.session.exec(stmt).all())

    def get_document(self, document_id: str, user: User) -> Document | None:
        """按 ID 获取文档。控制面无权限时返回 None。成员看不到已软删文档。"""
        doc = self.session.get(Document, document_id)
        if doc is None or not can_control_document(doc, user):
            return None
        if doc.deleted_at is not None and not is_kb_admin(user):
            return None
        return doc

    def summarize_vectorization(self, documents: list[Document], user: User) -> dict[str, DocumentVectorizationSummary]:
        """Batch summaries for already-authorized documents, rechecking control access.

        Only IDs, vector presence and state metadata are read. Matching the
        document/tenant pair also excludes inconsistent cross-tenant chunk rows.
        """
        allowed = [
            doc
            for doc in documents
            if can_control_document(doc, user) and (doc.deleted_at is None or is_kb_admin(user))
        ]
        summaries: dict[str, DocumentVectorizationSummary] = {
            doc.id: {
                "vectorization_status": "unknown",
                "vectorized_chunk_count": 0,
                "not_vectorized_chunk_count": 0,
                "unknown_chunk_count": 0,
                "embedding_skip_reason_counts": {},
            }
            for doc in allowed
        }
        if not allowed:
            return summaries
        rows: list[tuple[str, bool, str | None]] = []
        # Two parameters per document; bound batches stay below SQLite's older
        # 999-variable limit while avoiding a separate query per document.
        for offset in range(0, len(allowed), 200):
            batch = allowed[offset : offset + 200]
            rows.extend(
                self.session.exec(
                    select(
                        col(DocumentChunk.document_id),
                        (col(DocumentChunk.embedding).is_not(None) & (col(DocumentChunk.embedding) != "")).label(
                            "has_embedding"
                        ),
                        col(DocumentChunk.chunk_metadata),
                    ).where(
                        tuple_(col(DocumentChunk.document_id), col(DocumentChunk.tenant_id)).in_(
                            [(doc.id, doc.tenant_id) for doc in batch]
                        )
                    )
                ).all()
            )
        for document_id, has_embedding, metadata in rows:
            summary = summaries[document_id]
            state = chunk_vectorization_state(bool(has_embedding), metadata)
            if state["embedding_status"] == "vectorized":
                summary["vectorized_chunk_count"] += 1
            elif state["embedding_status"] == "not_vectorized":
                summary["not_vectorized_chunk_count"] += 1
                reason = state["embedding_skip_reason"] or "unknown_reason"
                summary["embedding_skip_reason_counts"][reason] = (
                    summary["embedding_skip_reason_counts"].get(reason, 0) + 1
                )
            else:
                summary["unknown_chunk_count"] += 1
        for doc in allowed:
            summary = summaries[doc.id]
            observed_count = (
                summary["vectorized_chunk_count"]
                + summary["not_vectorized_chunk_count"]
                + summary["unknown_chunk_count"]
            )
            if doc.chunk_count != observed_count or doc.chunk_count < 0:
                summary["unknown_chunk_count"] += max(0, doc.chunk_count - observed_count)
                continue
            if summary["unknown_chunk_count"]:
                continue
            if summary["vectorized_chunk_count"] and summary["not_vectorized_chunk_count"]:
                summary["vectorization_status"] = "partial"
            elif summary["vectorized_chunk_count"]:
                summary["vectorization_status"] = "vectorized"
            elif summary["not_vectorized_chunk_count"]:
                summary["vectorization_status"] = "not_vectorized"
        return summaries

    def list_chunks(self, document_id: str, user: User) -> list[DocumentChunk]:
        """按文档列出分块（控制面权限校验；无权限或不存在返回空）。"""
        doc = self.get_document(document_id, user)
        if doc is None:
            return []
        stmt = (
            select(DocumentChunk)
            .where(col(DocumentChunk.document_id) == document_id, col(DocumentChunk.tenant_id) == doc.tenant_id)
            .order_by(col(DocumentChunk.chunk_index).asc())
        )
        return list(self.session.exec(stmt).all())

    def publish_document(self, document_id: str, user: User) -> Document | None:
        """把目标版本设为当前发布版，并在同一事务里替换同组旧当前版。"""
        doc = self.session.get(Document, document_id)
        if doc is None or not can_control_document(doc, user):
            return None
        if doc.deleted_at is not None or doc.version_state == "archived":
            return None
        if doc.is_current and doc.version_state == "published":
            return doc
        demote_other_current_versions(self.session, doc.version_group_id, keep_id=doc.id)
        doc.is_current = True
        doc.version_state = "published"
        self.session.add(doc)
        self.session.commit()
        self.session.refresh(doc)
        return doc

    def schedule_document(self, document_id: str, user: User, effective_at: datetime) -> Document | None:
        """标为待生效。不自动变成当前版。"""
        doc = self.session.get(Document, document_id)
        if doc is None or not can_control_document(doc, user) or doc.deleted_at is not None:
            return None
        if effective_at <= datetime.now(UTC):
            raise ValueError("待生效时间必须在未来")
        doc.is_current = False
        doc.version_state = "scheduled"
        doc.effective_at = effective_at
        self.session.add(doc)
        self.session.commit()
        self.session.refresh(doc)
        return doc

    def archive_document(self, document_id: str, user: User) -> Document | None:
        """归档并退出当前版。"""
        doc = self.session.get(Document, document_id)
        if doc is None or not can_control_document(doc, user) or doc.deleted_at is not None:
            return None
        doc.is_current = False
        doc.version_state = "archived"
        self.session.add(doc)
        self.session.commit()
        self.session.refresh(doc)
        return doc

    async def reindex_document_in_place(
        self,
        document: Document,
        parsed: ParsedDocument,
        *,
        content_hash: str,
    ) -> Document:
        """准备候选后替换；任何失败回滚，不能让调用方提交旧块删除。"""
        self._reindex_external_attempt = None
        try:
            return await self._reindex_document_in_place(document, parsed, content_hash=content_hash)
        except (Exception, asyncio.CancelledError) as exc:
            self.session.rollback()
            if self._reindex_external_attempt is not None:
                target_id, document_id, new_ids = self._reindex_external_attempt
                # 独立事务保存补偿证据，不能随业务SQL回滚而消失。旧active不改状态。
                with Session(self.session.get_bind()) as evidence:
                    target = evidence.get(EmbeddingIndex, target_id)
                    if target is not None and target.status != IndexStatus.ACTIVE.value:
                        target.status = IndexStatus.FAILED.value
                        target.notes = json.dumps({
                            "stage": "external_index_compensation",
                            "document_id": document_id,
                            "target_index_id": target_id,
                            "new_chunk_ids": new_ids,
                            "exception_type": type(exc).__name__,
                            "compensation_required": True,
                        }, ensure_ascii=False)
                        evidence.add(target)
                        evidence.commit()
                self._index = None
            raise

    async def _reindex_document_in_place(
        self,
        document: Document,
        parsed: ParsedDocument,
        *,
        content_hash: str,
    ) -> Document:
        """就地替换分块与向量，不改变 is_current 与版本组。"""
        if document.deleted_at is not None:
            raise ValueError("已删除的文档不能重建")
        cleaning = clean_document(parsed)
        parsed = cleaning.document
        # 先读旧 chunks 的策略；重解析复用同一计划，旧数据缺策略走路由兼容路径。
        old_chunks = self.session.exec(select(DocumentChunk).where(
            col(DocumentChunk.document_id) == document.id,
            col(DocumentChunk.tenant_id) == document.tenant_id,
        )).all()
        previous_strategies = [c.strategy for c in old_chunks]

        # 优先按版本化 chunk_plan 精确重放；缺 plan 时走旧策略/路由兼容路径。
        plan = self._load_chunk_plan(document.chunk_plan)
        if plan is not None:
            strategy_name = plan["strategy"]
            params = self._params_from_plan(plan)
            decision = RoutingDecision(
                strategy=strategy_name,
                reason="replay_chunk_plan",
                signals=signals_from_parsed(parsed),
                version=plan.get("version", ROUTING_VERSION),
            )
        else:
            decision = self._decide_reindex_strategy(previous_strategies, parsed)
            strategy_name = decision.strategy
            params = self._build_chunk_params(None)

        chunking = get_chunking_strategy(strategy_name, embedding=self._embedding)
        if (strategy_name in {"format_aware", "layout_aware"} and parsed.blocks
                and not decision.signals.is_text_parser):
            chunk_objs = await chunking.split_blocks(parsed.blocks, params=params)
        else:
            chunk_objs = await chunking.split(parsed.text, params=params)
        if not chunk_objs:
            raise ValueError("文本为空或无法切分为任何分块")
        self._attach_routing_metadata(chunk_objs, decision)
        # 重解析保留同一 chunk_plan（版本化计划不变），除非走兼容路径。
        if plan is None:
            plan = self._build_chunk_plan(decision, params)
        embeddings = await self._embed_chunks(chunk_objs)
        target = self._write_index()
        target_index_id = target.id
        identity = identity_from_provider(self._embedding)
        active = active_index(self.session, identity.backend)
        replacing_active = active is not None and active.id == target_index_id
        target_old_chunks = [chunk for chunk in old_chunks if chunk.index_id == target_index_id]
        try:
            compensation = json.loads(target.notes or "{}")
        except (ValueError, TypeError):
            compensation = {}
        pending_cleanup = (isinstance(compensation, dict)
                           and compensation.get("compensation_required") is True
                           and compensation.get("document_id") == document.id)
        parent_id_map: dict[str, str] = {}
        persisted_rows: list[DocumentChunk] = []
        expected_dim = target.dim
        for chunk_index, (piece, vector) in enumerate(zip(chunk_objs, embeddings, strict=True)):
            row_id = uuid.uuid4().hex
            parent_id = None
            if piece.parent_id and piece.parent_id in parent_id_map:
                parent_id = parent_id_map[piece.parent_id]
            metadata: dict = dict(piece.metadata or {})
            metadata["cleaning"] = cleaning.report
            # 写入侧固化 L2 归一化：与正常摄取路径同一契约，异维直接拒绝。
            normalized_vector: list[float] | None = None
            if vector is not None:
                if len(vector) != expected_dim:
                    raise IndexIdentityError(
                        f"写入向量维度 {len(vector)} 与登记索引 {expected_dim} 不符"
                    )
                normalized_vector = l2_normalize(vector)
            row = DocumentChunk(
                id=row_id,
                tenant_id=document.tenant_id,
                document_id=document.id,
                chunk_index=chunk_index,
                content=piece.text,
                source=document.source,
                embedding=json.dumps(normalized_vector, ensure_ascii=False) if normalized_vector is not None else None,
                tokens=json.dumps(self._tokenizer(piece.text), ensure_ascii=False),
                strategy=strategy_name,
                chunk_metadata=json.dumps(metadata, ensure_ascii=False) if metadata else None,
                parent_id=parent_id,
                index_id=target.id,
            )
            if metadata.get("kind") == "parent":
                parent_id_map[str(metadata.get("parent_key"))] = row_id
            persisted_rows.append(row)
        if isinstance(self._vector_store, MilvusVectorStore):
            self._vector_store.validate_add(persisted_rows, identity, target_index_id=target_index_id)
        # LocalVectorStore 与应用共用SQL事务，其delete接口会commit，不能在此调用。
        # 外部清理失败显式失败；不把异常吞掉后宣称索引替换完成。
        if not isinstance(self._vector_store, LocalVectorStore):
            if not replacing_active:
                if not isinstance(self._vector_store, MilvusVectorStore):
                    raise IndexIdentityError("该远端向量库未声明 preparing 目标写入/清理能力，拒绝重建")
                with Session(self.session.get_bind()) as registered:
                    if registered.get(EmbeddingIndex, target_index_id) is None:
                        raise IndexIdentityError("远端重建目标尚未提交登记；请先执行 prepare")
                self._reindex_external_attempt = (target_index_id, document.id, [])
            try:
                if isinstance(self._vector_store, MilvusVectorStore):
                    if replacing_active:
                        # 既有active就地重解析保持原删除接口；新目标分支才收紧失败契约。
                        await self._vector_store.delete_by_document(document.id, document.tenant_id)
                    elif target_old_chunks or pending_cleanup:
                        await self._vector_store.delete_by_document(
                            document.id, document.tenant_id,
                            target_index_id=target_index_id, identity=identity,
                        )
                else:
                    await self._vector_store.delete_by_document(document.id, document.tenant_id)
            except Exception as exc:  # noqa: BLE001 — 外部索引未知异常统一转成可追踪错误
                raise ImportTraceError(
                    f"外部索引清理失败，需补偿：document={document.id} 原因={type(exc).__name__}",
                    stage="external_index_compensation",
                    error_code=type(exc).__name__,
                ) from exc
        # 写目标不是当前 active 索引（重建期写 preparing 索引）时**不删旧分块**：
        # 旧索引必须继续可读，回退后旧数据也必须真实存在——就地替换会让回退
        # 只剩一个空的登记行。
        if replacing_active or target_old_chunks:
            # 即使就地替换，也只动目标索引名下的分块：其它索引（retired / 身份未知）
            # 的分块属于别人的向量空间，删除它们就是破坏回退能力。
            for chunk in target_old_chunks:
                self.session.delete(chunk)
            self.session.flush()
        for row in persisted_rows:
            self.session.add(row)
        document.content_hash = content_hash
        document.chunk_count = len(chunk_objs)
        document.chunk_plan = json.dumps(plan, ensure_ascii=False)
        snapshot = self.session.get(DocumentIngestionSnapshot, document.id)
        if snapshot is None:
            snapshot = DocumentIngestionSnapshot(
                document_id=document.id,
                tenant_id=document.tenant_id,
                original_text="",
                original_blocks="[]",
                cleaning_report="{}",
            )
        snapshot.original_text = cleaning.original.text
        snapshot.original_blocks = json.dumps([asdict(block) for block in cleaning.original.blocks], ensure_ascii=False)
        snapshot.cleaning_report = json.dumps(cleaning.report, ensure_ascii=False)
        self.session.add(snapshot)
        self.session.add(document)
        if self._reindex_external_attempt is not None:
            self._reindex_external_attempt = (target_index_id, document.id, [row.id for row in persisted_rows])
        if isinstance(self._vector_store, MilvusVectorStore):
            await self._vector_store.add(persisted_rows, identity=identity, target_index_id=target_index_id)
        else:
            await self._vector_store.add(persisted_rows)
        self.session.commit()
        self.session.refresh(document)
        return document

    async def delete_document(self, document_id: str, user: User) -> bool:
        """软删除：记下删除时间，保留分块、向量和源文件。"""
        doc = self.session.get(Document, document_id)
        if doc is None or not can_write_document(doc, user):
            return False
        doc.deleted_at = datetime.now(UTC)
        self.session.add(doc)
        self.session.commit()
        return True
