"""RAG 导入任务服务：任务创建、执行、重试、重解析与版本治理。"""

from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlmodel import Session, select

from app.core.config import settings
from app.core.database import engine
from app.models.rag import (
    Document,
    ImportBatch,
    ImportBatchStatus,
    ImportJob,
    ImportJobStatus,
    ImportSourceType,
)
from app.models.user import User
from app.rag.document_parsers import (
    DocumentOcrRequiredError,
    DocumentParseError,
    DocumentTextEmptyError,
    ParsedDocument,
    UnsupportedDocumentTypeError,
    parse_uploaded_document,
)
from app.rag.document_storage import read_source_file, save_source_file
from app.rag.import_trace import ImportTraceError
from app.rag.service import RAGService

logger = logging.getLogger(__name__)
_IMPORT_JOB_ERROR_MAX_CHARS = 500
_IMPORT_JOB_TRACE_PREVIEW_CHARS = 240


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


def _recompute_batch(session: Session, batch: ImportBatch) -> None:
    """根据子任务状态回写批次统计。"""
    jobs = session.exec(select(ImportJob).where(ImportJob.batch_id == batch.id)).all()
    batch.total_jobs = len(jobs)
    batch.completed_jobs = sum(
        1 for job in jobs if job.status in {ImportJobStatus.SUCCESS.value, ImportJobStatus.FAILED.value}
    )
    batch.successful_jobs = sum(1 for job in jobs if job.status == ImportJobStatus.SUCCESS.value)
    batch.failed_jobs = sum(1 for job in jobs if job.status == ImportJobStatus.FAILED.value)
    if batch.completed_jobs == 0:
        batch.status = ImportBatchStatus.PENDING.value
    elif batch.failed_jobs == 0 and batch.completed_jobs == batch.total_jobs:
        batch.status = ImportBatchStatus.SUCCESS.value
    elif batch.successful_jobs > 0 and batch.failed_jobs > 0:
        batch.status = ImportBatchStatus.PARTIAL_SUCCESS.value
    elif batch.completed_jobs < batch.total_jobs:
        batch.status = ImportBatchStatus.RUNNING.value
    else:
        batch.status = ImportBatchStatus.FAILED.value
    session.add(batch)


def create_import_batch(
    session: Session,
    user: User,
    *,
    total_jobs: int,
    source_type: str,
) -> ImportBatch:
    """创建导入批次。"""
    batch = ImportBatch(
        tenant_id=user.tenant_id,
        user_id=user.id,
        total_jobs=total_jobs,
        source_type=source_type,
        status=ImportBatchStatus.PENDING.value,
    )
    session.add(batch)
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
) -> ImportJob:
    """创建文件导入任务。"""
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
    session.commit()
    session.refresh(job)
    if batch_id:
        batch = session.get(ImportBatch, batch_id)
        if batch is not None:
            _recompute_batch(session, batch)
            session.commit()
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
    job.status = ImportJobStatus.PENDING.value
    job.error = None
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def _dedupe_or_version_existing(
    session: Session,
    *,
    tenant_id: str,
    source_kind: str,
    source_ref: str | None,
    content_hash: str,
    reparse_document_id: str | None,
) -> tuple[Document | None, dict[str, str | int | bool | None]]:
    """判定是否去重或创建新版本。"""
    if reparse_document_id:
        current = session.get(Document, reparse_document_id)
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

    stmt = select(Document).where(
        Document.tenant_id == tenant_id,
        Document.is_current.is_(True),
        Document.source_kind == source_kind,
    )
    if source_ref:
        if source_kind == ImportSourceType.URL.value:
            stmt = stmt.where(Document.source_uri == source_ref)
        else:
            stmt = stmt.where(Document.source == source_ref)
    current = session.exec(stmt.order_by(Document.updated_at.desc())).first()
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


async def _process_job(session: Session, job: ImportJob) -> None:
    """执行单个导入任务。"""
    job.status = ImportJobStatus.RUNNING.value
    job.error = None
    job.attempt_count += 1
    session.add(job)
    session.commit()
    session.refresh(job)

    try:
        if job.source_type == ImportSourceType.URL.value or (
            job.source_type == ImportSourceType.REPARSE.value and job.source_uri
        ):
            if not job.source_uri:
                raise ValueError("URL 导入任务缺少 source_uri")
            raw, content_type = await _fetch_remote_content(job.source_uri)
            filename = _url_filename(job.source_uri, content_type)
            job.storage_path = save_source_file(job.tenant_id, raw, filename)
            job.content_type = content_type
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
        content_hash = hashlib.sha256(parsed.text.encode("utf-8")).hexdigest()
        existing, versioning = _dedupe_or_version_existing(
            session,
            tenant_id=job.tenant_id,
            source_kind=source_kind,
            source_ref=source_ref,
            content_hash=content_hash,
            reparse_document_id=job.reparse_document_id,
        )
        job.content_hash = content_hash
        job.parser_name = str(parsed.metadata.get("parser_name") or "")
        if existing is not None:
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
            doc = await rag.reindex_document_in_place(
                target, parsed, content_hash=content_hash
            )
            job.document_id = doc.id
            job.status = ImportJobStatus.SUCCESS.value
            session.add(job)
            session.commit()
            return

        previous_id = versioning["previous_document_id"]
        if previous_id:
            previous = session.get(Document, previous_id)
            if previous is not None:
                previous.is_current = False
                session.add(previous)
                session.commit()

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
            version_number=int(versioning["version_number"]),
            previous_document_id=previous_id if isinstance(previous_id, str) else None,
            import_job_id=job.id,
        )
        job.document_id = doc.id
        job.status = ImportJobStatus.SUCCESS.value
    except (
        UnsupportedDocumentTypeError,
        DocumentParseError,
        DocumentTextEmptyError,
        DocumentOcrRequiredError,
        ImportTraceError,
        httpx.HTTPError,
        OSError,
        ValueError,
    ) as exc:
        job.status = ImportJobStatus.FAILED.value
        job.error = _build_job_error_message(exc)
        _record_job_trace(session, job, exc)
    except Exception as exc:  # noqa: BLE001
        logger.exception("导入任务执行失败: job=%s", job.id)
        job.status = ImportJobStatus.FAILED.value
        job.error = _build_job_error_message(exc)
        _record_job_trace(session, job, exc)
    finally:
        session.add(job)
        session.commit()
        if job.batch_id:
            batch = session.get(ImportBatch, job.batch_id)
            if batch is not None:
                _recompute_batch(session, batch)
                session.commit()


async def run_import_jobs_once(limit: int | None = None) -> int:
    """处理一批待执行导入任务，返回处理数量。"""
    limit = limit or settings.RAG_IMPORT_MAX_CONCURRENCY
    processed = 0
    with Session(engine) as session:
        jobs = session.exec(
            select(ImportJob)
            .where(ImportJob.status == ImportJobStatus.PENDING.value)
            .order_by(ImportJob.created_at.asc())
            .limit(limit)
        ).all()
        job_ids = [job.id for job in jobs]
    for job_id in job_ids:
        with Session(engine) as session:
            job = session.get(ImportJob, job_id)
            if job is None or job.status != ImportJobStatus.PENDING.value:
                continue
            await _process_job(session, job)
            processed += 1
    return processed


async def run_import_jobs_until_idle(limit: int | None = None) -> int:
    """循环处理直到没有待执行任务。"""
    total = 0
    while True:
        handled = await run_import_jobs_once(limit=limit)
        total += handled
        if handled == 0:
            break
        await asyncio.sleep(0)
    return total
