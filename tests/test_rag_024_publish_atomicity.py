"""RAG-024：导入新版本发布原子性。

新版发布必须在准备完成后原子切换：
- Embedding / 落库失败后，旧版仍应是 current 且仍可检索；
- 成功时版本组内只有一个 current；
- 失败重试不产生第二个 current（幂等）；
- 外部向量索引写入未完成时，必须有可追踪的补偿记录。

导入任务执行器使用全局 engine，因此本文件也用同一个库，靠租户唯一前缀隔离；
运行级隔离由外部传入的 DATABASE_URL 保证。
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.database import engine, init_db
from app.core.security import Role
from app.models.rag import Document, ImportJob, ImportJobStatus, ImportJobTrace
from app.models.user import User
from app.rag.document_storage import save_source_file
from app.rag.embeddings.mock import MockEmbeddingProvider
from app.rag.import_jobs import (
    create_upload_import_job,
    retry_import_job,
    run_import_jobs_once,
)
from app.rag.service import RAGService


class _FailingEmbedding(MockEmbeddingProvider):
    """首次调用失败的嵌入提供者，用于注入 Embedding 故障。"""

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        super().__init__(*args, **kwargs)
        self.fail_next = True

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("注入的 Embedding 故障")
        return await super().embed(texts)


def _user(tenant_id: str) -> User:
    return User(
        id=uuid4().hex,
        tenant_id=tenant_id,
        username="rag024",
        hashed_password="",
        role=Role.TENANT_ADMIN.value,
        token_version=0,
        is_active=True,
    )


def _configure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import app.rag.document_storage as storage_mod

    monkeypatch.setattr(storage_mod, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(settings, "RAG_BACKEND", "native")
    monkeypatch.setattr(settings, "RAG_VECTOR_STORE", "local")
    monkeypatch.setattr(settings, "RAG_CHUNK_STRATEGY", "structured")
    monkeypatch.setattr(settings, "RAG_IMPORT_ENABLED", False)


def _rag(session: Session, tenant: str) -> RAGService:
    return RAGService(session, tenant, embedding_provider=MockEmbeddingProvider())


def _current_docs(session: Session, group_id: str) -> list[Document]:
    return list(
        session.exec(
            select(Document).where(
                Document.version_group_id == group_id,
                col(Document.is_current).is_(True),
            )
        ).all()
    )


async def _seed_current_version(
    session: Session, tenant: str, user: User, source: str, body: str
) -> Document:
    """摄取一个当前版本，并真实保存源文件（重解析任务需要 storage_path）。"""
    raw = body.encode()
    path = save_source_file(tenant, raw, source)
    rag = _rag(session, tenant)
    return await rag.ingest_text(
        body, title="rag024 文档", source=source, user_id=user.id, storage_path=path
    )


@pytest.fixture()
def session() -> Session:
    init_db()
    with Session(engine) as s:
        yield s


async def test_failed_new_version_keeps_previous_current_and_searchable(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Embedding 失败后：旧版仍是 current，且仍能检索到。"""
    _configure(monkeypatch, tmp_path)
    tenant = f"rag024-fail-{uuid4().hex[:8]}"
    user = _user(tenant)
    source = f"rag024-fail-{uuid4().hex[:6]}.txt"
    marker = f"RAG024OLD{uuid4().hex[:10]}"

    old = await _seed_current_version(
        session, tenant, user, source, f"{marker} 旧版本正文，用于验证失败后仍可检索。"
    )

    raw = "rag024 新版本正文，摄取时注入 Embedding 故障。".encode()
    path = save_source_file(tenant, raw, source)
    job = create_upload_import_job(
        session, user, storage_path=path, filename=source, content_type="text/plain"
    )

    monkeypatch.setattr("app.rag.service.get_embedding_provider", lambda: _FailingEmbedding())
    await run_import_jobs_once(limit=10)
    session.expire_all()

    completed = session.get(ImportJob, job.id)
    assert completed is not None
    assert completed.status == ImportJobStatus.FAILED.value

    # 核心断言：旧版不能被提前降级，必须仍可检索。
    session.expire_all()
    still_current = session.get(Document, old.id)
    assert still_current is not None
    assert still_current.is_current is True
    hits = await _rag(session, tenant).search(marker, top_k=5)
    assert hits, "失败后旧版必须仍可检索"


async def test_successful_new_version_leaves_single_current(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure(monkeypatch, tmp_path)
    tenant = f"rag024-ok-{uuid4().hex[:8]}"
    user = _user(tenant)
    source = f"rag024-ok-{uuid4().hex[:6]}.txt"

    old = await _seed_current_version(
        session, tenant, user, source, "rag024 旧版本正文，将被新版本替换。"
    )
    group_id = old.version_group_id

    raw = "rag024 新版本正文，正常发布。".encode()
    path = save_source_file(tenant, raw, source)
    job = create_upload_import_job(
        session, user, storage_path=path, filename=source, content_type="text/plain"
    )
    monkeypatch.setattr("app.rag.service.get_embedding_provider", lambda: MockEmbeddingProvider())
    await run_import_jobs_once(limit=10)
    session.expire_all()

    completed = session.get(ImportJob, job.id)
    assert completed is not None and completed.status == ImportJobStatus.SUCCESS.value
    current = _current_docs(session, group_id)
    assert len(current) == 1, "成功发布后版本组内只能有一个 current"
    assert current[0].id != old.id


async def test_retry_after_failure_does_not_create_second_current(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """失败后重试：仍只有一个 current，不产生重复版本。"""
    _configure(monkeypatch, tmp_path)
    tenant = f"rag024-retry-{uuid4().hex[:8]}"
    user = _user(tenant)
    source = f"rag024-retry-{uuid4().hex[:6]}.txt"

    old = await _seed_current_version(
        session, tenant, user, source, "rag024 旧版本正文，先失败再重试。"
    )
    group_id = old.version_group_id

    raw = "rag024 新版本正文，先失败再重试。".encode()
    path = save_source_file(tenant, raw, source)
    job = create_upload_import_job(
        session, user, storage_path=path, filename=source, content_type="text/plain"
    )

    monkeypatch.setattr("app.rag.service.get_embedding_provider", lambda: _FailingEmbedding())
    await run_import_jobs_once(limit=10)
    session.expire_all()
    assert session.get(ImportJob, job.id).status == ImportJobStatus.FAILED.value

    monkeypatch.setattr("app.rag.service.get_embedding_provider", lambda: MockEmbeddingProvider())
    retry_import_job(session, user, job.id)
    await run_import_jobs_once(limit=10)
    session.expire_all()

    current = _current_docs(session, group_id)
    assert len(current) == 1, "重试后仍只能有一个 current"


class _ExternalStore:
    """非本地向量库替身：与主库不共享事务。可注入删除失败。"""

    def __init__(self, fail_delete: bool = False) -> None:
        self.fail_delete = fail_delete

    async def add(self, chunks: list) -> None:
        return None

    async def hybrid_search(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return []

    async def delete_by_document(self, document_id: str, tenant_id: str) -> int:
        if self.fail_delete:
            raise RuntimeError("注入的外部索引清理故障")
        return 0

    async def count(self, tenant_id: str) -> int:
        return 0


async def test_external_index_cleanup_failure_is_recorded(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """外部索引清理失败必须留下可追踪的补偿记录，不能被吞掉后宣称成功。"""
    from app.rag.import_jobs import create_reparse_job

    _configure(monkeypatch, tmp_path)
    tenant = f"rag024-ext-{uuid4().hex[:8]}"
    user = _user(tenant)
    source = f"rag024-ext-{uuid4().hex[:6]}.txt"

    old = await _seed_current_version(
        session, tenant, user, source, "rag024 正文，外部索引清理失败场景。"
    )
    monkeypatch.setattr("app.rag.service.get_embedding_provider", lambda: MockEmbeddingProvider())
    monkeypatch.setattr(
        "app.rag.service.get_vector_store", lambda _s: _ExternalStore(fail_delete=True)
    )

    job = create_reparse_job(session, user, old.id)
    await run_import_jobs_once(limit=10)
    session.expire_all()

    completed = session.get(ImportJob, job.id)
    assert completed is not None and completed.status == ImportJobStatus.FAILED.value
    traces = list(
        session.exec(select(ImportJobTrace).where(ImportJobTrace.job_id == job.id)).all()
    )
    assert traces, "外部索引失败必须留下补偿记录"
    assert any("external_index" in (t.stage or "") for t in traces), [t.stage for t in traces]


async def test_external_index_cleanup_then_main_db_failure_is_recorded(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """外部删除已不可逆地发生后主库失败：两边发散，必须登记补偿。"""
    import app.rag.service as service_mod
    from app.rag.import_jobs import create_reparse_job

    _configure(monkeypatch, tmp_path)
    tenant = f"rag024-ext3-{uuid4().hex[:8]}"
    user = _user(tenant)
    source = f"rag024-ext3-{uuid4().hex[:6]}.txt"

    old = await _seed_current_version(
        session, tenant, user, source, "rag024 正文，外部删除后主库失败场景。"
    )
    monkeypatch.setattr("app.rag.service.get_embedding_provider", lambda: MockEmbeddingProvider())
    monkeypatch.setattr("app.rag.service.get_vector_store", lambda _s: _ExternalStore())
    monkeypatch.setattr(
        "app.rag.vectorstore.factory.get_vector_store", lambda _s: _ExternalStore()
    )
    # 精确模拟「外部删除已成功、其后主库写入失败」：不全局打补丁，避免污染其他会话。
    store = _ExternalStore()

    async def _delete_then_fail(self, target, parsed, *, content_hash=None):  # noqa: ANN001
        await store.delete_by_document(target.id, target.tenant_id)
        raise RuntimeError("注入的主库写入故障")

    monkeypatch.setattr(RAGService, "reindex_document_in_place", _delete_then_fail)

    job = create_reparse_job(session, user, old.id)
    await run_import_jobs_once(limit=10)
    session.expire_all()

    completed = session.get(ImportJob, job.id)
    assert completed is not None and completed.status == ImportJobStatus.FAILED.value
    traces = list(
        session.exec(select(ImportJobTrace).where(ImportJobTrace.job_id == job.id)).all()
    )
    assert any("external_index" in (t.stage or "") for t in traces), [t.stage for t in traces]


async def test_reparse_success_with_external_store_records_compensation(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """重解析成功也不能假装外部索引已就绪：清理已发生，须登记待补偿。"""
    from app.rag.import_jobs import create_reparse_job

    _configure(monkeypatch, tmp_path)
    tenant = f"rag024-ext4-{uuid4().hex[:8]}"
    user = _user(tenant)
    source = f"rag024-ext4-{uuid4().hex[:6]}.txt"

    old = await _seed_current_version(
        session, tenant, user, source, "rag024 正文，重解析外部索引场景。"
    )
    monkeypatch.setattr("app.rag.service.get_embedding_provider", lambda: MockEmbeddingProvider())
    monkeypatch.setattr("app.rag.service.get_vector_store", lambda _s: _ExternalStore())
    monkeypatch.setattr(
        "app.rag.vectorstore.factory.get_vector_store", lambda _s: _ExternalStore()
    )

    job = create_reparse_job(session, user, old.id)
    await run_import_jobs_once(limit=10)
    session.expire_all()

    completed = session.get(ImportJob, job.id)
    assert completed is not None and completed.status == ImportJobStatus.SUCCESS.value
    traces = list(
        session.exec(select(ImportJobTrace).where(ImportJobTrace.job_id == job.id)).all()
    )
    assert any(t.stage == "external_index_compensation" for t in traces), [
        t.stage for t in traces
    ]


async def test_local_store_publish_records_no_compensation_noise(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """本地向量库与主库同事务，不应产生补偿记录噪音。"""
    _configure(monkeypatch, tmp_path)
    tenant = f"rag024-local-{uuid4().hex[:8]}"
    user = _user(tenant)
    source = f"rag024-local-{uuid4().hex[:6]}.txt"

    await _seed_current_version(
        session, tenant, user, source, "rag024 旧版本正文，本地向量库场景。"
    )
    raw = "rag024 新版本正文，本地向量库发布。".encode()
    path = save_source_file(tenant, raw, source)
    job = create_upload_import_job(
        session, user, storage_path=path, filename=source, content_type="text/plain"
    )
    monkeypatch.setattr("app.rag.service.get_embedding_provider", lambda: MockEmbeddingProvider())
    await run_import_jobs_once(limit=10)
    session.expire_all()

    completed = session.get(ImportJob, job.id)
    assert completed is not None and completed.status == ImportJobStatus.SUCCESS.value
    traces = list(
        session.exec(select(ImportJobTrace).where(ImportJobTrace.job_id == job.id)).all()
    )
    assert traces == [], [t.stage for t in traces]


async def test_external_store_publish_records_pending_compensation(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """外部向量库不共享事务：发布成功后须登记外部索引待补偿，不假装已同步。"""
    _configure(monkeypatch, tmp_path)
    tenant = f"rag024-ext2-{uuid4().hex[:8]}"
    user = _user(tenant)
    source = f"rag024-ext2-{uuid4().hex[:6]}.txt"

    await _seed_current_version(
        session, tenant, user, source, "rag024 旧版本正文，外部向量库发布场景。"
    )
    raw = "rag024 新版本正文，发布到外部向量库。".encode()
    path = save_source_file(tenant, raw, source)
    job = create_upload_import_job(
        session, user, storage_path=path, filename=source, content_type="text/plain"
    )
    monkeypatch.setattr("app.rag.service.get_embedding_provider", lambda: MockEmbeddingProvider())
    monkeypatch.setattr("app.rag.service.get_vector_store", lambda _s: _ExternalStore())
    monkeypatch.setattr(
        "app.rag.vectorstore.factory.get_vector_store", lambda _s: _ExternalStore()
    )

    await run_import_jobs_once(limit=10)
    session.expire_all()

    completed = session.get(ImportJob, job.id)
    assert completed is not None and completed.status == ImportJobStatus.SUCCESS.value
    traces = list(
        session.exec(select(ImportJobTrace).where(ImportJobTrace.job_id == job.id)).all()
    )
    assert any(t.stage == "external_index_compensation" for t in traces), [
        t.stage for t in traces
    ]
