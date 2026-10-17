"""RAG 导入任务服务：任务创建、执行、重试、重解析与版本治理。"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TypedDict
from urllib.parse import urlparse
from weakref import WeakKeyDictionary

import httpx
from sqlalchemy import case, func, update
from sqlalchemy import select as sql_select
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.database import engine
from app.core.security import Role
from app.models.rag import (
    Document,
    DocumentIngestionSnapshot,
    ImportBatch,
    ImportBatchStatus,
    ImportJob,
    ImportJobStatus,
    ImportSourceType,
)
from app.models.user import User
from app.rag.cleaning import clean_document
from app.rag.document_parsers import (
    DocumentTextEmptyError,
    ParsedDocument,
    parse_uploaded_document,
)
from app.rag.document_storage import delete_source_file, read_source_file, save_source_file
from app.rag.import_trace import ImportTraceError
from app.rag.service import RAGService
from app.services.quota import QuotaExceededError, begin_source_quota

logger = logging.getLogger(__name__)
_IMPORT_JOB_ERROR_MAX_CHARS = 500
_IMPORT_JOB_TRACE_PREVIEW_CHARS = 240


@dataclass
class _ExecutionRegistry:
    active: dict[str, tuple[str, str, str]] = field(default_factory=dict)


_registry_lock = threading.RLock()
_registries: WeakKeyDictionary[Engine, _ExecutionRegistry] = WeakKeyDictionary()


def _registry() -> _ExecutionRegistry:
    with _registry_lock:
        return _registries.setdefault(engine, _ExecutionRegistry())


def _positive_budget(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1000:
        raise ValueError(f"{name} 必须是 1 至 1000 的整数")
    return value


def _validate_job_binding(session: Session, job: ImportJob) -> None:
    from app.rag.access import can_write_document
    from app.rag.document_storage import (
        _sanitize_segment,
        knowledge_storage_root,
        resolve_source_file_path,
    )

    actor = session.get(User, job.user_id)
    if actor is not None and (
        not actor.is_active or (actor.tenant_id != job.tenant_id
                                and actor.role != Role.SYSTEM_ADMIN.value)
    ):
        raise ValueError("导入任务身份绑定不一致")
    if job.batch_id:
        batch = session.get(ImportBatch, job.batch_id)
        if batch is None or batch.tenant_id != job.tenant_id or batch.user_id != job.user_id:
            raise ValueError("导入任务批次绑定不一致")
    if job.reparse_document_id:
        target = session.get(Document, job.reparse_document_id)
        if target is None or target.tenant_id != job.tenant_id or target.deleted_at is not None:
            raise ValueError("重解析目标绑定不一致或已删除")
        if actor is not None and not can_write_document(target, actor):
            raise ValueError("无权重解析目标文档")
    if not job.reparse_document_id:
        receipts = session.exec(select(Document).where(col(Document.import_job_id) == job.id)).all()
        expected_kind = "url" if job.source_uri else "file"
        for receipt in receipts:
            source_matches = (receipt.source_uri == job.source_uri if job.source_uri
                              else receipt.source == job.source_name)
            if (receipt.tenant_id != job.tenant_id or receipt.user_id != job.user_id
                    or receipt.source_kind != expected_kind or not source_matches
                    or receipt.deleted_at is not None):
                raise ValueError("导入发布凭据绑定不一致或文档已删除")
    if job.storage_path:
        root = knowledge_storage_root() / _sanitize_segment(job.tenant_id, default="unknown_tenant")
        try:
            resolve_source_file_path(job.storage_path).relative_to(root.resolve())
        except (ValueError, FileNotFoundError) as exc:
            raise ValueError("导入源文件租户绑定不一致") from exc


def _source_key(session: Session, job: ImportJob) -> tuple[str, str, str]:
    if job.reparse_document_id:
        target = session.get(Document, job.reparse_document_id)
        if target is not None:
            return (job.tenant_id, target.source_kind,
                    target.source_uri or target.source or "")
    return (job.tenant_id, "url" if job.source_uri else "file",
            job.source_uri or job.source_name or "")


def _published_receipt(session: Session, job: ImportJob) -> Document | None:
    if job.reparse_document_id:
        if job.status != ImportJobStatus.SUCCESS.value or job.document_id != job.reparse_document_id:
            return None
        doc = session.get(Document, job.document_id)
        if doc is not None and doc.tenant_id == job.tenant_id and doc.deleted_at is None:
            return doc
        return None
    docs = session.exec(select(Document).where(
        col(Document.import_job_id) == job.id, col(Document.tenant_id) == job.tenant_id,
        col(Document.user_id) == job.user_id,
    )).all()
    for doc in docs:
        expected_kind = "url" if job.source_uri else "file"
        if (doc.deleted_at is None and doc.source_kind == expected_kind
                and (doc.source_uri == job.source_uri if job.source_uri else doc.source == job.source_name)):
            return doc
    return None


def _committed_success_receipt(session: Session, job: ImportJob) -> Document | None:
    """Deduplication may commit success without assigning this job's marker to the document."""
    with session.no_autoflush:
        persisted = session.exec(select(ImportJob.status, ImportJob.document_id, ImportJob.content_hash).where(
            col(ImportJob.id) == job.id,
        )).first()
    if persisted is None or persisted[0] != ImportJobStatus.SUCCESS.value or not persisted[1] or not persisted[2]:
        return None
    doc = session.get(Document, persisted[1])
    if (doc is None or doc.deleted_at is not None or doc.tenant_id != job.tenant_id
            or doc.content_hash != persisted[2]):
        return None
    expected_kind = "url" if job.source_uri else "file"
    if doc.source_kind != expected_kind or (
        doc.source_uri != job.source_uri if job.source_uri else doc.source != job.source_name
    ):
        return None
    if job.reparse_document_id and doc.id != job.reparse_document_id:
        return None
    if doc.user_id != job.user_id:
        from app.rag.access import can_write_document

        actor = session.get(User, job.user_id)
        if actor is None or not can_write_document(doc, actor):
            return None
    return doc


def _finish_batch(session: Session, job: ImportJob) -> None:
    if job.batch_id:
        batch = session.get(ImportBatch, job.batch_id)
        if batch is not None and batch.tenant_id == job.tenant_id and batch.user_id == job.user_id:
            session.flush()
            _recompute_batch(session, batch)


def _batch_status(total: int, completed: int, successful: int, failed: int, running: int) -> str:
    """普通完成与恢复共用批次状态规则，未完成不能提前进入终态。"""
    if total == 0:
        return ImportBatchStatus.PENDING.value
    if completed < total:
        return ImportBatchStatus.RUNNING.value if completed or running else ImportBatchStatus.PENDING.value
    if failed == 0:
        return ImportBatchStatus.SUCCESS.value
    if successful > 0:
        return ImportBatchStatus.PARTIAL_SUCCESS.value
    return ImportBatchStatus.FAILED.value


def _reconcile_batches(session: Session) -> None:
    """一次SQL聚合修复持久汇总漂移，不重放任务或缩小缺子任务批次。"""
    statement = sql_select(
        ImportBatch,
        func.count(col(ImportJob.id)),
        func.sum(case((col(ImportJob.status).in_(("success", "failed")), 1), else_=0)),
        func.sum(case((col(ImportJob.status) == "success", 1), else_=0)),
        func.sum(case((col(ImportJob.status) == "failed", 1), else_=0)),
        func.sum(case((col(ImportJob.status) == "running", 1), else_=0)),
        func.min(col(ImportJob.tenant_id)), func.max(col(ImportJob.tenant_id)),
        func.min(col(ImportJob.user_id)), func.max(col(ImportJob.user_id)),
    ).outerjoin(ImportJob, col(ImportJob.batch_id) == col(ImportBatch.id)).group_by(col(ImportBatch.id))
    for batch, total, completed, successful, failed, running, tenant_min, tenant_max, user_min, user_max in (
        session.execute(statement).all()
    ):
        if total == 0 or total < batch.total_jobs:
            logger.warning("rag_import_batch_recovery_skipped batch=%s reason=missing_children", batch.id)
            continue
        if (tenant_min != batch.tenant_id or tenant_max != batch.tenant_id
                or user_min != batch.user_id or user_max != batch.user_id):
            logger.warning("rag_import_batch_recovery_skipped batch=%s reason=binding_mismatch", batch.id)
            continue
        status = _batch_status(total, completed, successful, failed, running)
        if (batch.status, batch.total_jobs, batch.completed_jobs, batch.successful_jobs, batch.failed_jobs) == (
            status, total, completed, successful, failed,
        ):
            continue
        batch.status = status
        batch.total_jobs = total
        batch.completed_jobs = completed
        batch.successful_jobs = successful
        batch.failed_jobs = failed
        session.add(batch)
        logger.info("rag_import_batch_reconciled batch=%s status=%s", batch.id, status)


def _recover_locked(session: Session, registry: _ExecutionRegistry) -> int:
    recovered = 0
    for job in session.exec(select(ImportJob).where(
        col(ImportJob.status) == ImportJobStatus.RUNNING.value,
    )).all():
        if job.id in registry.active:
            continue
        try:
            _validate_job_binding(session, job)
            doc = _published_receipt(session, job)
            if doc is not None:
                job.document_id, job.status, job.error = doc.id, ImportJobStatus.SUCCESS.value, None
            elif job.attempt_count >= job.max_attempts:
                job.status, job.error = ImportJobStatus.FAILED.value, "导入中断且重试次数已用尽"
            else:
                job.status, job.error = ImportJobStatus.PENDING.value, None
            exc = ImportTraceError("已恢复中断的导入任务", stage="import_recovery", error_code="interrupted_import")
        except ValueError as error:
            job.status, job.error = ImportJobStatus.FAILED.value, str(error)
            exc = ImportTraceError(str(error), stage="import_recovery", error_code="binding_mismatch")
        _record_job_trace(session, job, exc)
        session.add(job)
        recovered += 1
    session.flush()
    _reconcile_batches(session)
    session.commit()
    return recovered


def recover_interrupted_import_jobs() -> int:
    """恢复本进程无活跃执行者的 running；新进程启动时注册表为空。"""
    with _registry_lock, Session(engine, autoflush=False) as session:
        return _recover_locked(session, _registry())


class UrlFetchError(ImportTraceError):
    """URL 抓取失败的结构化异常。"""


def _url_filename(url: str, content_type: str | None) -> str:
    """为 URL 快照生成可解析的文件名。"""
    path_name = Path(urlparse(url).path).name
    if path_name:
        return path_name
    if content_type and "json" in content_type:
        return "remote.json"
    if content_type and "xml" in content_type:
        return "remote.xml"
    if content_type and "csv" in content_type:
        return "remote.csv"
    if content_type and "pdf" in content_type:
        return "remote.pdf"
    return "remote.html"


def _html_to_text(raw: bytes) -> tuple[str, str | None]:
    """将 HTML 粗提取为纯文本与标题。"""
    decoded = raw.decode("utf-8", errors="ignore")
    title_match = re.search(r"<title[^>]*>(.*?)</title>", decoded, flags=re.IGNORECASE | re.DOTALL)
    title = html.unescape(title_match.group(1)).strip() if title_match else None
    cleaned = re.sub(
        r"<(script|style|noscript)[^>]*>.*?</\1>",
        " ",
        decoded,
        flags=re.IGNORECASE | re.DOTALL,
    )
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    text = re.sub(r"\s+", " ", cleaned).strip()
    return text, title


def _parse_url_payload(raw: bytes, url: str, content_type: str | None) -> ParsedDocument:
    """解析远程内容；HTML 走内建提取，其余复用上传解析器。"""
    filename = _url_filename(url, content_type)
    ct = (content_type or "").lower()
    if "html" in ct or filename.lower().endswith((".html", ".htm")):
        text, title = _html_to_text(raw)
        if not text:
            raise DocumentTextEmptyError("网页中未提取到可用文本")
        return ParsedDocument(
            text=text,
            title=title or filename.rsplit(".", 1)[0],
            source=url,
            extension="html",
            content_type=content_type,
            metadata={"parser_name": "url_html", "used_ocr": False},
        )
    parsed = parse_uploaded_document(raw, filename, content_type)
    return ParsedDocument(
        text=parsed.text,
        title=parsed.title,
        source=url,
        extension=parsed.extension,
        content_type=parsed.content_type,
        metadata={**parsed.metadata, "parser_name": "url_remote"},
    )


async def _fetch_remote_content(url: str) -> tuple[bytes, str | None]:
    """抓取 URL 原始内容。"""
    command = f"GET {url}"
    try:
        async with httpx.AsyncClient(timeout=settings.RAG_IMPORT_FETCH_TIMEOUT) as client:
            response = await client.get(url, follow_redirects=True)
        response.raise_for_status()
        return response.content, response.headers.get("content-type")
    except httpx.HTTPStatusError as exc:
        body = exc.response.text if exc.response is not None else ""
        raise UrlFetchError(
            "URL 抓取失败：远端返回非成功状态码",
            stage="url_fetch",
            error_code="url_http_status_error",
            command=command,
            exit_code=exc.response.status_code if exc.response is not None else None,
            stderr=body or str(exc),
        ) from exc
    except httpx.TimeoutException as exc:
        raise UrlFetchError(
            "URL 抓取失败：请求超时",
            stage="url_fetch",
            error_code="url_timeout",
            command=command,
            stderr=str(exc),
        ) from exc
    except httpx.HTTPError as exc:
        raise UrlFetchError(
            "URL 抓取失败：网络或协议错误",
            stage="url_fetch",
            error_code="url_http_error",
            command=command,
            stderr=str(exc),
        ) from exc


def _normalize_url(url: str) -> str:
    """校验并标准化 URL。"""
    normalized = url.strip()
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("url 必须是有效的 http/https 地址")
    return normalized


def _clip_text(text: str | None, max_chars: int) -> str:
    """裁剪过长文本，避免任务摘要或预览过大。"""
    if not text:
        return ""
    normalized = re.sub(r"\s+", " ", text).strip()
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[:max_chars].rstrip()}..."


def _build_job_error_message(exc: Exception) -> str:
    """构建面向任务列表/前端的简短错误摘要。"""
    base = str(exc).strip() or exc.__class__.__name__
    stderr = _clip_text(getattr(exc, "stderr", None), _IMPORT_JOB_TRACE_PREVIEW_CHARS)
    stdout = _clip_text(getattr(exc, "stdout", None), _IMPORT_JOB_TRACE_PREVIEW_CHARS)
    details: list[str] = []
    if stderr:
        details.append(f"stderr: {stderr}")
    elif stdout:
        details.append(f"stdout: {stdout}")
    message = base if not details else f"{base} | {' | '.join(details)}"
    return _clip_text(message, _IMPORT_JOB_ERROR_MAX_CHARS)


def _record_job_trace(session: Session, job: ImportJob, exc: Exception) -> None:
    """为失败任务持久化完整追踪证据，便于定位与后续演进。"""
    from app.models.rag import ImportJobTrace

    trace = ImportJobTrace(
        job_id=job.id,
        tenant_id=job.tenant_id,
        attempt_count=job.attempt_count,
        stage=str(getattr(exc, "stage", "import_job") or "import_job"),
        error_code=getattr(exc, "error_code", None),
        exception_type=exc.__class__.__name__,
        message=str(exc),
        command=getattr(exc, "command", None),
        exit_code=getattr(exc, "exit_code", None),
        stdout=getattr(exc, "stdout", None),
        stderr=getattr(exc, "stderr", None),
    )
    session.add(trace)


def _uses_external_vector_store(session: Session) -> bool:
    """生效的向量库是否与主库共享事务。

    本地实现就是主库本身，与主库同一事务；外部实现（如 Milvus）不是，
    因此需要单独的补偿记录。
    """
    from app.rag.vectorstore.factory import get_vector_store
    from app.rag.vectorstore.local import LocalVectorStore

    return not isinstance(get_vector_store(session), LocalVectorStore)


# 待补偿的两类情形用 error_code 区分，便于后续按类型处理：
# - external_index_missing_chunks：主库已有分块，外部索引缺少对应向量（补齐即可）
# - external_index_deleted_main_db_failed：外部索引已不可逆删除，主库尚未提交（需重建）
EXTERNAL_INDEX_MISSING_CHUNKS = "external_index_missing_chunks"
EXTERNAL_INDEX_DELETED_MAIN_DB_FAILED = "external_index_deleted_main_db_failed"


def _record_external_index_compensation(
    session: Session,
    job: ImportJob,
    document_id: str,
    reason: str,
    *,
    error_code: str = EXTERNAL_INDEX_MISSING_CHUNKS,
) -> None:
    """登记外部索引与主库不一致的待补偿事项。

    向量库与应用主库不是同一个事务：可能出现「主库已提交而外部索引未同步」或
    「外部索引已变更而主库未提交」。这里只做记录，不假装跨库事务已生效，
    也不自动重建索引。
    """
    from app.models.rag import ImportJobTrace

    session.add(
        ImportJobTrace(
            job_id=job.id,
            tenant_id=job.tenant_id,
            attempt_count=job.attempt_count,
            stage="external_index_compensation",
            error_code=error_code,
            exception_type="ExternalIndexOutOfSync",
            message=f"外部索引待补偿：document={document_id}；{reason}",
        )
    )


def _recompute_batch(session: Session, batch: ImportBatch) -> None:
    """根据子任务状态回写批次统计。"""
    jobs = session.exec(select(ImportJob).where(col(ImportJob.batch_id) == batch.id)
                        .execution_options(populate_existing=True)).all()
    if any(job.tenant_id != batch.tenant_id or job.user_id != batch.user_id for job in jobs):
        logger.warning("rag_import_batch_update_skipped batch=%s reason=binding_mismatch", batch.id)
        return
    batch.total_jobs = max(batch.total_jobs, len(jobs))
    batch.completed_jobs = sum(
        1 for job in jobs if job.status in {ImportJobStatus.SUCCESS.value, ImportJobStatus.FAILED.value}
    )
    batch.successful_jobs = sum(1 for job in jobs if job.status == ImportJobStatus.SUCCESS.value)
    batch.failed_jobs = sum(1 for job in jobs if job.status == ImportJobStatus.FAILED.value)
    batch.status = _batch_status(
        batch.total_jobs, batch.completed_jobs, batch.successful_jobs, batch.failed_jobs,
        sum(job.status == ImportJobStatus.RUNNING.value for job in jobs),
    )
    session.add(batch)


def _validate_creation_batch(session: Session, user: User, batch_id: str | None) -> None:
    if batch_id is None:
        return
    with session.no_autoflush:
        batch = session.get(ImportBatch, batch_id)
    if batch is None or batch.tenant_id != user.tenant_id or batch.user_id != user.id:
        raise ValueError("导入任务批次绑定不一致")


def create_import_batch(
    session: Session,
    user: User,
    *,
    total_jobs: int,
    source_type: str,
    commit: bool = True,
) -> ImportBatch:
    """创建导入批次。commit 为假时只 flush，留给调用方和配额锁一起提交。"""
    batch = ImportBatch(
        tenant_id=user.tenant_id,
        user_id=user.id,
        total_jobs=total_jobs,
        source_type=source_type,
        status=ImportBatchStatus.PENDING.value,
    )
    session.add(batch)
    session.flush()
    if commit:
        session.commit()
        session.refresh(batch)
    return batch


def create_upload_import_job(
    session: Session,
    user: User,
    *,
    storage_path: str,
    filename: str,
    content_type: str | None,
    batch_id: str | None = None,
    commit: bool = True,
) -> ImportJob:
    """创建文件导入任务。commit 为假时不提交，便于整批和配额锁一起提交。"""
    _validate_creation_batch(session, user, batch_id)
    job = ImportJob(
        tenant_id=user.tenant_id,
        user_id=user.id,
        batch_id=batch_id,
        status=ImportJobStatus.PENDING.value,
        source_type=ImportSourceType.FILE.value,
        source_name=filename,
        storage_path=storage_path,
        content_type=content_type,
    )
    session.add(job)
    session.flush()
    if batch_id:
        batch = session.get(ImportBatch, batch_id)
        if batch is not None:
            _recompute_batch(session, batch)
    if commit:
        session.commit()
        session.refresh(job)
    return job


def create_url_import_job(
    session: Session,
    user: User,
    *,
    url: str,
    title: str | None = None,
    batch_id: str | None = None,
    backend: str | None = None,
) -> ImportJob:
    """创建 URL 导入任务。"""
    _validate_creation_batch(session, user, batch_id)
    job = ImportJob(
        tenant_id=user.tenant_id,
        user_id=user.id,
        batch_id=batch_id,
        status=ImportJobStatus.PENDING.value,
        source_type=ImportSourceType.URL.value,
        source_uri=_normalize_url(url),
        requested_title=title,
        backend=backend,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    if batch_id:
        batch = session.get(ImportBatch, batch_id)
        if batch is not None:
            _recompute_batch(session, batch)
            session.commit()
    return job


def create_reparse_job(session: Session, user: User, document_id: str) -> ImportJob:
    """为现有文档创建重解析任务。"""
    from app.rag.access import can_write_document

    doc = session.get(Document, document_id)
    if doc is None or not can_write_document(doc, user):
        raise ValueError("文档不存在或无权访问")
    job = ImportJob(
        tenant_id=doc.tenant_id,
        user_id=user.id,
        status=ImportJobStatus.PENDING.value,
        source_type=ImportSourceType.REPARSE.value,
        source_name=doc.source,
        source_uri=doc.source_uri,
        storage_path=doc.storage_path,
        requested_title=doc.title,
        reparse_document_id=doc.id,
        backend=None,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def retry_import_job(session: Session, user: User, job_id: str) -> ImportJob:
    """将失败任务重置为待执行。成员仅能重试自己的任务。"""
    from app.rag.access import is_kb_admin, same_tenant

    job = session.get(ImportJob, job_id)
    if job is None or not same_tenant(job.tenant_id, user):
        raise ValueError("任务不存在或无权访问")
    if not is_kb_admin(user) and job.user_id != user.id:
        raise ValueError("任务不存在或无权访问")
    if job.status != ImportJobStatus.FAILED.value:
        raise ValueError("仅失败任务支持重试")
    if job.attempt_count >= job.max_attempts:
        raise ValueError("导入重试次数已用尽")
    with _registry_lock:
        result = session.execute(update(ImportJob).where(
            col(ImportJob.id) == job.id,
            col(ImportJob.status) == ImportJobStatus.FAILED.value,
            col(ImportJob.attempt_count) < col(ImportJob.max_attempts),
        ).values(status=ImportJobStatus.PENDING.value, error=None))
        if getattr(result, "rowcount", 0) != 1:
            session.rollback()
            raise ValueError("仅失败且未耗尽次数的任务支持重试")
        session.refresh(job)
        _finish_batch(session, job)
        session.commit()
        session.refresh(job)
    return job


class Versioning(TypedDict):
    """去重或新版本判定。键固定，避免下标变成宽联合。"""

    deduplicated: bool
    version_group_id: str | None
    version_number: int
    previous_document_id: str | None


def _dedupe_or_version_existing(
    session: Session,
    *,
    tenant_id: str,
    source_kind: str,
    source_ref: str | None,
    content_hash: str,
    reparse_document_id: str | None,
    actor: User | None = None,
) -> tuple[Document | None, Versioning]:
    """判定是否去重或创建新版本。"""
    if reparse_document_id:
        current = session.get(Document, reparse_document_id)
        if current:
            # 显式重建需重新应用切分和索引逻辑，内容未变也不能按重复上传跳过。
            return None, {
                "deduplicated": False,
                "version_group_id": current.version_group_id,
                "version_number": current.version_number,
                "previous_document_id": current.previous_document_id,
            }

    stmt = select(Document).where(
        Document.tenant_id == tenant_id,
        col(Document.is_current).is_(True),
        col(Document.deleted_at).is_(None),
        Document.source_kind == source_kind,
    )
    if source_ref:
        if source_kind == ImportSourceType.URL.value:
            stmt = stmt.where(Document.source_uri == source_ref)
        else:
            stmt = stmt.where(Document.source == source_ref)
    current = session.exec(stmt.order_by(col(Document.updated_at).desc())).first()
    if current is not None and actor is not None:
        from app.rag.access import can_write_document

        if not can_write_document(current, actor):
            raise ValueError("无权复用或升级此来源的当前文档")
    if current and current.content_hash == content_hash:
        return current, {
            "deduplicated": True,
            "version_group_id": current.version_group_id,
            "version_number": current.version_number,
            "previous_document_id": current.previous_document_id,
        }
    if current:
        return None, {
            "deduplicated": False,
            "version_group_id": current.version_group_id,
            "version_number": current.version_number + 1,
            "previous_document_id": current.id,
        }
    return None, {
        "deduplicated": False,
        "version_group_id": None,
        "version_number": 1,
        "previous_document_id": None,
    }


def _drop_attempt_source(job: ImportJob, relative_path: str | None) -> None:
    """删除这一次新拉取的源文件。删不掉时任务不能记成成功。"""
    if not relative_path:
        return
    delete_source_file(relative_path)
    job.storage_path = None


async def _process_job(session: Session, job: ImportJob) -> None:
    """执行单个导入任务。"""
    _validate_job_binding(session, job)
    already = _published_receipt(session, job)
    if already is not None:
        job.document_id, job.status, job.error = already.id, ImportJobStatus.SUCCESS.value, None
        session.add(job)
        _finish_batch(session, job)
        session.commit()
        return

    new_source_path: str | None = None
    try:
        if job.reparse_document_id:
            reparse_target = session.get(Document, job.reparse_document_id)
            if reparse_target is None:
                raise ValueError("重解析目标文档不存在")
            if reparse_target.deleted_at is not None:
                raise ValueError("已删除的文档不能重建")
        source_ref: str | None
        if job.source_type == ImportSourceType.URL.value or (
            job.source_type == ImportSourceType.REPARSE.value and job.source_uri
        ):
            if not job.source_uri:
                raise ValueError("URL 导入任务缺少 source_uri")
            reparse_target = session.get(Document, job.reparse_document_id) if job.reparse_document_id else None
            has_snapshot = bool(job.storage_path and (
                reparse_target is None or job.storage_path != reparse_target.storage_path
            ))
            if has_snapshot and job.storage_path is not None:
                new_source_path = job.storage_path
                raw, content_type = read_source_file(job.storage_path), job.content_type
            else:
                raw, content_type = await _fetch_remote_content(job.source_uri)
                filename = _url_filename(job.source_uri, content_type)
                begin_source_quota(session, job.tenant_id, len(raw))
                new_source_path = save_source_file(job.tenant_id, raw, filename)
                job.storage_path = new_source_path
                job.content_type = content_type
                session.add(job)
                session.commit()  # 持久源文件预留，释放配额写锁再解析/Embedding。
            parsed = _parse_url_payload(raw, job.source_uri, content_type)
            source_kind = ImportSourceType.URL.value
            source_ref = job.source_uri
        else:
            if not job.storage_path:
                raise ValueError("文件导入任务缺少 storage_path")
            raw = read_source_file(job.storage_path)
            parsed = parse_uploaded_document(raw, job.source_name or "upload.bin", job.content_type)
            source_kind = ImportSourceType.FILE.value
            source_ref = job.source_name
        cleaning = clean_document(parsed)
        content_hash = cleaning.report["original_hash"]
        existing, versioning = _dedupe_or_version_existing(
            session,
            tenant_id=job.tenant_id,
            source_kind=source_kind,
            source_ref=source_ref,
            content_hash=content_hash,
            reparse_document_id=job.reparse_document_id,
            actor=session.get(User, job.user_id),
        )
        job.content_hash = content_hash
        job.parser_name = str(parsed.metadata.get("parser_name") or "")
        if existing is not None:
            if session.get(DocumentIngestionSnapshot, existing.id) is None:
                session.add(DocumentIngestionSnapshot(
                    document_id=existing.id, tenant_id=existing.tenant_id,
                    original_text=parsed.text,
                    original_blocks=json.dumps([asdict(block) for block in parsed.blocks], ensure_ascii=False),
                    cleaning_report=json.dumps(cleaning.report, ensure_ascii=False),
                ))
            job.document_id = existing.id
            job.status = ImportJobStatus.SUCCESS.value
            session.add(job)
            session.commit()
            return

        if job.reparse_document_id:
            target = session.get(Document, job.reparse_document_id)
            if target is None:
                raise ValueError("重解析目标文档不存在")
            rag = RAGService(session, target.tenant_id)
            external = _uses_external_vector_store(session)
            try:
                doc = await rag.reindex_document_in_place(
                    target, parsed, content_hash=content_hash, import_job_id=job.id
                )
            except Exception as exc:  # noqa: BLE001 — 外部删除不可逆，失败也要留痕
                # 外部索引的清理一旦发生就不可回滚：无论它是成功还是失败，
                # 只要主库随后没提交成功，两边就已经发散，必须登记待补偿。
                if external and getattr(exc, "stage", None) != "external_index_compensation":
                    # 外层处理失败时会 rollback，这里必须先把补偿记录提交出去，
                    # 否则这条「外部索引已不可逆变更」的证据会被一并回滚掉。
                    try:
                        session.rollback()
                        _record_external_index_compensation(
                            session,
                            job,
                            target.id,
                            f"重解析的外部索引清理已发生且不可回滚，主库未提交成功："
                            f"{type(exc).__name__}（需按主库状态重建外部向量）",
                            error_code=EXTERNAL_INDEX_DELETED_MAIN_DB_FAILED,
                        )
                        session.commit()
                    except Exception:  # noqa: BLE001 — 补偿记录失败不能掩盖原始异常
                        logger.warning(
                            "rag_external_index_compensation_record_failed job=%s", job.id
                        )
                raise
            job.document_id = doc.id
            job.status = ImportJobStatus.SUCCESS.value
            # 重解析同样不共享事务：外部索引已清理旧向量，主库才写入新分块。
            if external:
                # 与发布路径同源：此处外部清理已发生且不可回滚，登记失败不能
                # 把一次成功的重解析翻成失败任务。
                try:
                    _record_external_index_compensation(
                        session,
                        job,
                        doc.id,
                        "重解析已清理外部索引旧向量，新分块缺少对应向量（补齐即可）",
                        error_code=EXTERNAL_INDEX_MISSING_CHUNKS,
                    )
                except Exception:  # noqa: BLE001 — 已提交，不能因登记失败而报任务失败
                    logger.warning(
                        "rag_external_index_compensation_record_failed job=%s", job.id
                    )
            session.add(job)
            session.commit()
            return

        previous_id = versioning["previous_document_id"]
        # 旧版不在此处提前退出 current：那需要一次独立提交，一旦后续摄取失败就
        # 无法回滚，会让该来源既有知识整体不可检索。降级由 ingest_parsed_document
        # 与新版落在同一个事务内完成（同版本组只保留新版为当前版）。
        rag = RAGService(session, job.tenant_id)
        title = job.requested_title or parsed.title
        doc = await rag.ingest_parsed_document(
            parsed,
            user_id=job.user_id,
            title=title,
            storage_path=job.storage_path,
            backend=job.backend,
            source_kind=source_kind,
            source_uri=job.source_uri,
            content_hash=content_hash,
            version_group_id=versioning["version_group_id"],
            version_number=versioning["version_number"],
            previous_document_id=previous_id if isinstance(previous_id, str) else None,
            import_job_id=job.id,
        )
        job.document_id = doc.id
        job.status = ImportJobStatus.SUCCESS.value
        # 外部向量库不与应用主库共享事务：发布成功后仍可能未同步，显式登记待补偿，
        # 不把「主库已发布」当成「外部索引也已就绪」。
        # 此时发布已提交且不可回滚，登记只能尽力而为：失败只记录告警，
        # 绝不能把一次成功的发布翻成失败任务。
        try:
            # 判定本身也包在 try 内：``_uses_external_vector_store`` 会去取向量库，
            # 它一旦抖动抛错，就会让一次「已提交且不可回滚的发布」被翻成失败任务。
            if _uses_external_vector_store(session):
                _record_external_index_compensation(
                    session,
                    job,
                    doc.id,
                    "主库已发布新版分块，外部向量索引缺少对应向量（补齐即可）",
                    error_code=EXTERNAL_INDEX_MISSING_CHUNKS,
                )
        except Exception:  # noqa: BLE001 — 已提交，不能因登记失败而报任务失败
            logger.warning("rag_external_index_compensation_record_failed job=%s", job.id)
    except (Exception, asyncio.CancelledError) as exc:
        session.rollback()
        session.refresh(job)
        try:
            _validate_job_binding(session, job)
            receipt = _published_receipt(session, job) or _committed_success_receipt(session, job)
        except ValueError:
            receipt = None
        if receipt is not None:
            job.document_id, job.status, job.error = receipt.id, ImportJobStatus.SUCCESS.value, None
        else:
            cancelled = isinstance(exc, asyncio.CancelledError)
            if not cancelled:
                logger.error("rag_import_failed job=%s exception_type=%s", job.id, type(exc).__name__)
            job.status = ImportJobStatus.RUNNING.value if cancelled else ImportJobStatus.FAILED.value
            job.error = ("导入执行已中断，可恢复" if isinstance(exc, asyncio.CancelledError)
                         else _build_job_error_message(exc))
            if isinstance(exc, QuotaExceededError):
                job.error = "源文件配额已用尽"
            trace_exc = (ImportTraceError("导入执行被取消", stage="import_cancelled", error_code="cancelled")
                         if isinstance(exc, asyncio.CancelledError) else exc)
            _record_job_trace(session, job, trace_exc)
            _drop_attempt_source(job, new_source_path)
        if isinstance(exc, asyncio.CancelledError):
            raise
    finally:
        session.add(job)
        _finish_batch(session, job)
        session.commit()


async def _execute_claimed(job_id: str) -> None:
    try:
        with Session(engine, autoflush=False) as session:
            job = session.get(ImportJob, job_id)
            if job is not None:
                await _process_job(session, job)
    finally:
        with _registry_lock:
            _registry().active.pop(job_id, None)


async def run_import_jobs_once(limit: int | None = None) -> int:
    """每轮最多处理 batch_size，实际并发在本进程所有 runner 间共享。"""
    batch_size = _positive_budget(settings.RAG_IMPORT_BATCH_SIZE if limit is None else limit, "batch_size")
    concurrency = _positive_budget(settings.RAG_IMPORT_MAX_CONCURRENCY, "concurrency")
    processed = 0
    while processed < batch_size:
        claimed: list[str] = []
        try:
            with _registry_lock, Session(engine, autoflush=False) as session:
                registry = _registry()
                _recover_locked(session, registry)
                capacity = min(batch_size - processed, max(0, concurrency - len(registry.active)))
                candidates = session.exec(select(ImportJob).where(
                    col(ImportJob.status) == ImportJobStatus.PENDING.value,
                ).order_by(col(ImportJob.created_at).asc(), col(ImportJob.id).asc())).all()
                for job in candidates:
                    if len(claimed) >= capacity:
                        break
                    try:
                        _validate_job_binding(session, job)
                        if job.attempt_count >= job.max_attempts:
                            raise ValueError("导入重试次数已用尽")
                        source_key = _source_key(session, job)
                    except ValueError as exc:
                        job.status, job.error = ImportJobStatus.FAILED.value, str(exc)
                        _record_job_trace(session, job, exc)
                        session.add(job)
                        _finish_batch(session, job)
                        session.commit()
                        continue
                    if source_key in registry.active.values():
                        continue
                    result = session.execute(update(ImportJob).where(
                        col(ImportJob.id) == job.id, col(ImportJob.status) == ImportJobStatus.PENDING.value,
                        col(ImportJob.attempt_count) == job.attempt_count,
                    ).values(status=ImportJobStatus.RUNNING.value, error=None,
                             attempt_count=job.attempt_count + 1))
                    if getattr(result, "rowcount", 0) != 1:
                        session.rollback()
                        continue
                    session.refresh(job)
                    _finish_batch(session, job)
                    session.commit()
                    registry.active[job.id] = source_key
                    claimed.append(job.id)
        except BaseException:
            with _registry_lock:
                for job_id in claimed:
                    _registry().active.pop(job_id, None)
            raise
        if not claimed:
            break
        tasks = [asyncio.create_task(_execute_claimed(job_id)) for job_id in claimed]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            with _registry_lock:
                for job_id in claimed:
                    _registry().active.pop(job_id, None)
            raise
        processed += len(claimed)
    return processed


async def run_import_jobs_until_idle(limit: int | None = None) -> int:
    """等待本库活跃执行者，直到没有待执行及活跃任务。"""
    total = 0
    while True:
        total += await run_import_jobs_once(limit=limit)
        with _registry_lock, Session(engine) as session:
            pending = session.exec(select(ImportJob.id).where(
                col(ImportJob.status) == ImportJobStatus.PENDING.value,
            )).first()
            active = bool(_registry().active)
        if pending is None and not active:
            return total
        await asyncio.sleep(.01)
