"""Opt-in real PostgreSQL migration and persistence contracts on disposable databases."""

import importlib.util
import json
import os
import re
import secrets
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from app.core.config import settings

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def pg_admin():
    value = os.environ.get("POSTGRES_VALIDATION_URL")
    if not value:
        if os.environ.get("POSTGRES_VALIDATION_REQUIRED") == "1":
            pytest.fail("explicit disposable PostgreSQL target is required")
        pytest.skip("real PostgreSQL opt-in target not configured")
    url = make_url(value)
    if (url.get_backend_name() != "postgresql" or url.host not in {"127.0.0.1", "localhost", "::1"}
            or not re.fullmatch(r"validation_[a-f0-9]{8,32}", url.database or "")):
        pytest.fail("target must be a loopback disposable validation database")
    engine = sa.create_engine(url, pool_pre_ping=True)
    with engine.connect() as connection:
        assert int(connection.exec_driver_sql("SHOW server_version_num").scalar_one()) >= 140000
        assert connection.exec_driver_sql("SELECT current_database()").scalar_one() == url.database
    yield engine
    engine.dispose()


@pytest.fixture
def pg_database(pg_admin):
    name = "nfr005_" + uuid.uuid4().hex
    with pg_admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(sa.text(f'CREATE DATABASE "{name}"'))
    engine = sa.create_engine(pg_admin.url.set(database=name), pool_pre_ping=True)
    try:
        yield engine
    finally:
        engine.dispose()
        # Only the random database created by this fixture is eligible for cleanup.
        assert re.fullmatch(r"nfr005_[a-f0-9]{32}", name)
        with pg_admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(sa.text(f'DROP DATABASE "{name}"'))


def upgrade(engine, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", engine.url.render_as_string(hide_password=False))
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(config, "head")
    return ScriptDirectory.from_config(config).get_current_head()


def test_empty_postgresql_migrates_once_to_real_head(pg_database, monkeypatch):
    assert sa.inspect(pg_database).get_table_names() == []
    expected = upgrade(pg_database, monkeypatch)
    assert expected
    with pg_database.connect() as connection:
        assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == expected
    assert {"users", "tenants", "conversations", "messages", "rag_documents", "evaluation_jobs",
            "evaluation_datasets", "conversation_memory_snapshots", "run_history"} <= set(
                sa.inspect(pg_database).get_table_names())
    assert upgrade(pg_database, monkeypatch) == expected


def test_migrated_schema_covers_registered_models(pg_database, monkeypatch):
    from sqlmodel import SQLModel

    upgrade(pg_database, monkeypatch)
    inspector = sa.inspect(pg_database)
    tables = set(inspector.get_table_names())
    missing = {}
    for name, table in SQLModel.metadata.tables.items():
        if name not in tables:
            missing[name] = sorted(table.columns.keys())
            continue
        actual = {column["name"] for column in inspector.get_columns(name)}
        if set(table.columns.keys()) - actual:
            missing[name] = sorted(set(table.columns.keys()) - actual)
    assert not missing, missing


def test_application_startup_preserves_partial_unique_document_index(pg_database, monkeypatch):
    from app.core import database

    monkeypatch.setattr(settings, "DATABASE_URL", pg_database.url.render_as_string(hide_password=False))
    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr(database, "engine", pg_database)
    database.init_db()
    database.init_db()
    indexes = sa.inspect(pg_database).get_indexes("rag_documents")
    current_index = next((index for index in indexes if index["name"] == "uq_rag_documents_one_current_version"), None)
    assert current_index is not None
    assert current_index["unique"]
    assert "is_current" in str(current_index["dialect_options"]["postgresql_where"])


@pytest.mark.parametrize("target", ["jobs", "datasets"])
def test_legacy_postgresql_checks_expand_without_retaining_narrow_values(pg_database, target):
    if target == "jobs":
        filename = "f8a4b6c7d9e0_expand_real_evaluation_constraints.py"
        table = "evaluation_jobs"
        ddl = ("CREATE TABLE evaluation_jobs (id TEXT PRIMARY KEY, runner VARCHAR NOT NULL, mode VARCHAR NOT NULL, "
               "CONSTRAINT old_runner CHECK (runner IN ('retrieval','generation','agent')), "
               "CONSTRAINT old_mode CHECK (mode IN ('smoke','synthetic')), "
               "CONSTRAINT keep_id CHECK (length(id)>0))")
        old_values = "('old','agent','smoke')"
        new_values = "('new','workflow','real')"
    else:
        filename = "f9b5c7d8e0f1_expand_dataset_family_constraint.py"
        table = "evaluation_datasets"
        ddl = ("CREATE TABLE evaluation_datasets (id TEXT PRIMARY KEY, family VARCHAR NOT NULL, "
               "CONSTRAINT old_family CHECK (family IN ('retrieval','generation','agent')), "
               "CONSTRAINT keep_id CHECK (length(id)>0))")
        old_values = "('old','agent')"
        new_values = "('new','workflow')"
    spec = importlib.util.spec_from_file_location("pg_constraint_upgrade", ROOT / "alembic/versions" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pg_database.begin() as connection:
        connection.exec_driver_sql(ddl)
        connection.exec_driver_sql(f"INSERT INTO {table} VALUES {old_values}")
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
            module.upgrade()
        connection.exec_driver_sql(f"INSERT INTO {table} VALUES {new_values}")
        assert connection.exec_driver_sql(f"SELECT id FROM {table} ORDER BY id").scalars().all() == ["new", "old"]
        assert any(check["name"] == "keep_id" for check in sa.inspect(connection).get_check_constraints(table))


@pytest.fixture
def persisted(pg_database, monkeypatch):
    from sqlmodel import Session

    from app.core import database
    from app.core.security import hash_password
    from app.models.user import Tenant, User
    from app.services.membership import ensure_membership

    upgrade(pg_database, monkeypatch)
    monkeypatch.setattr(database, "engine", pg_database)
    password = secrets.token_urlsafe(24)
    with Session(pg_database) as session:
        session.add_all([Tenant(id="tenant-a", name="A"), Tenant(id="tenant-b", name="B")])
        session.commit()
        for role in ("system_admin", "tenant_admin", "member", "viewer", "system_viewer"):
            user = User(id=role, username=role, tenant_id="tenant-a", role=role,
                        hashed_password=hash_password(password))
            session.add(user)
            session.commit()
            ensure_membership(session, user)
    return pg_database, SecretStr(password)


def test_real_auth_chat_reads_keep_tenant_and_role_boundaries(persisted):
    from fastapi.testclient import TestClient
    from sqlmodel import Session

    from app.main import app
    from app.models.conversation import Conversation, Message
    from app.services.quota import accept_user_message

    engine, password = persisted
    with Session(engine) as session:
        conversation, _ = accept_user_message(
            session, tenant_id="tenant-a", user_id="member", role="member",
            conversation_id=None, message="数据库回读：中文与 emoji ✅",
        )
        conversation_id = conversation.id
        session.add(Conversation(id="other", tenant_id="tenant-b", user_id="member"))
        session.commit()
    client = TestClient(app)
    for role in ("system_admin", "tenant_admin", "member", "viewer", "system_viewer"):
        result = client.post("/api/v1/auth/login", json={"username": role, "password": password.get_secret_value()})
        assert result.status_code == 200
        headers = {"Authorization": "Bearer " + result.json()["access_token"]}
        response = client.get("/api/v1/chat/conversations", headers=headers)
        assert response.status_code == (403 if role in {"viewer", "system_viewer"} else 200)
        if role == "member":
            detail = client.get(f"/api/v1/chat/conversations/{conversation_id}", headers=headers)
            assert detail.status_code == 200
            assert detail.json()["messages"][0]["content"] == "数据库回读：中文与 emoji ✅"
            assert client.get("/api/v1/chat/conversations/other", headers=headers).status_code == 404
    with Session(engine) as session:
        assert session.get(Message, detail.json()["messages"][0]["id"]).content.endswith("✅")


def test_evaluation_workflow_audit_memory_and_history_roundtrip(persisted):
    from sqlmodel import Session

    from app.audit.models import AuditLog
    from app.debug.trace import AgentTrace
    from app.memory.base import MemorySnapshot
    from app.models.conversation import Conversation, Message
    from app.models.evaluation import EvaluationDatasetVersion, EvaluationJob
    from app.models.user import User
    from app.models.workflow import Workflow
    from app.services.evaluation_datasets import EvaluationDatasetService
    from app.services.evaluation_jobs import EvaluationJobAccessError, EvaluationJobService
    from app.services.memory_snapshots import MemorySnapshotService
    from app.services.run_history import RunHistoryService

    engine, _ = persisted
    with Session(engine) as session:
        admin = session.get(User, "system_admin")
        dataset = EvaluationDatasetService(session).create_dataset(
            admin, name="真实PG持久化", slug="pg-workflow", family="workflow",
        )
        dataset_id = dataset.id
        version = EvaluationDatasetVersion(id="version", dataset_id=dataset.id, tenant_id="tenant-a",
                    version="1", status="approved", source="upload", content_hash="frozen", cases_json="[]",
                    case_count=0, created_by=admin.id)
        session.add(version)
        session.add(EvaluationJob(id="job", tenant_id="tenant-a", submitted_by=admin.id,
                    dataset_id=dataset.id, dataset_version_id="version", runner="workflow", mode="real",
                    idempotency_key="persistent", request_hash="request", input_hash="frozen"))
        session.add(Workflow(id="workflow", tenant_id="tenant-a", owner_id="member", name="PG往返",
                    cron_expr="0 8 * * *", prompt_template="No execution", enabled=False))
        session.add(Conversation(id="memory", tenant_id="tenant-a", user_id="member"))
        session.commit()
        session.add(Message(id="message", conversation_id="memory", role="user", content="保留的事实"))
        session.add(AuditLog(id="audit", tenant_id="tenant-a", user_id="member", action="pg_contract",
                             details=json.dumps({"中文": True}, ensure_ascii=False)))
        session.commit()
    with Session(engine) as session:
        user = session.get(User, "member")
        conversation = session.get(Conversation, "memory")
        snapshot = MemorySnapshotService(session).save(
            user, conversation, MemorySnapshot(summary="保留的事实", compressed_count=1),
        )
        assert snapshot is not None
        trace = AgentTrace()
        trace.start()
        trace.llm_call("contract-model")
        trace.finish()
        history = RunHistoryService(session).record(user, conversation.id, trace)
        run_id = history.id
    with Session(engine) as session:
        admin = session.get(User, "system_admin")
        assert EvaluationDatasetService(session).get_dataset(admin, dataset_id).family == "workflow"
        assert EvaluationJobService(session).cancel(admin, "job").status == "cancelled"
        assert session.get(Workflow, "workflow").enabled is False
        assert json.loads(session.get(AuditLog, "audit").details) == {"中文": True}
        user = session.get(User, "member")
        assert MemorySnapshotService(session).load(user, session.get(Conversation, "memory")).summary == "保留的事实"
        assert RunHistoryService(session).get(admin, run_id).llm_call_count == 1
        other = user.model_copy(update={"tenant_id": "tenant-b"})
        with pytest.raises(EvaluationJobAccessError):
            EvaluationJobService(session).get(other, "job")


def test_constraint_failure_rolls_back_without_poisoning_next_transaction(persisted):
    from sqlmodel import Session

    from app.models.conversation import Message
    from app.models.user import User

    engine, _ = persisted
    with Session(engine) as session:
        session.add(User(id="duplicate", username="member", tenant_id="tenant-a", hashed_password=""))
        with pytest.raises(sa.exc.IntegrityError):
            session.commit()
        session.rollback()
        assert session.get(User, "duplicate") is None
        session.add(Message(id="bad-fk", conversation_id="absent", role="user", content="must roll back"))
        with pytest.raises(sa.exc.IntegrityError):
            session.commit()
        session.rollback()
        session.add(User(id="valid", username="new-valid", tenant_id="tenant-a", hashed_password=""))
        session.commit()
    with Session(engine) as session:
        assert session.get(User, "valid") is not None
        assert session.get(Message, "bad-fk") is None


def test_concurrent_message_quota_is_atomic(persisted):
    from sqlmodel import Session, func, select

    from app.models.conversation import Conversation, Message
    from app.models.user import Tenant
    from app.services.quota import QuotaExceededError, accept_user_message

    engine, _ = persisted
    with Session(engine) as session:
        tenant = session.get(Tenant, "tenant-a")
        tenant.message_limit = 1
        session.add(tenant)
        session.commit()
    barrier = Barrier(2)

    def write():
        with Session(engine) as session:
            session.execute(sa.text("SET statement_timeout = '5s'"))
            session.commit()
            barrier.wait(timeout=5)
            try:
                accept_user_message(session, tenant_id="tenant-a", user_id="member", role="member",
                                    conversation_id=None, message="一次预算")
                return "accepted"
            except QuotaExceededError:
                return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: write(), range(2)))
    assert sorted(results) == ["accepted", "rejected"]
    with Session(engine) as session:
        assert session.exec(select(func.count()).select_from(Message)).one() == 1
        assert session.exec(select(func.count()).select_from(Conversation)).one() == 1


def test_forward_repair_of_stamped_store_preserves_integer_and_boolean_defaults(pg_database, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", pg_database.url.render_as_string(hide_password=False))
    command.upgrade(Config(str(ROOT / "alembic.ini")), "fa06d8e9f1a2")
    with pg_database.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE rag_documents DROP COLUMN version_number")
        connection.exec_driver_sql("ALTER TABLE messages DROP COLUMN sources")
    upgrade(pg_database, monkeypatch)
    inspector = sa.inspect(pg_database)
    columns = {column["name"]: column for column in inspector.get_columns("rag_documents")}
    assert isinstance(columns["version_number"]["type"], sa.Integer)
    with pg_database.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_name='rag_documents' AND column_name='version_number'"
        ).scalar_one() == "1"
    assert "sources" in {column["name"] for column in inspector.get_columns("messages")}


def test_document_versions_enforce_one_current_but_keep_archived_versions(persisted):
    from sqlmodel import Session, select

    from app.models.rag import Document

    engine, _ = persisted
    with Session(engine) as session:
        for index, current in enumerate([False, False, True]):
            session.add(Document(id=f"doc-{index}", tenant_id="tenant-a", user_id="member",
                                 title="版本", version_group_id="one-group", is_current=current))
        session.commit()
        session.add(Document(id="duplicate", tenant_id="tenant-a", user_id="member",
                             title="拒绝", version_group_id="one-group", is_current=True))
        with pytest.raises(sa.exc.IntegrityError):
            session.commit()
        session.rollback()
    with Session(engine) as session:
        rows = session.exec(select(Document).where(Document.version_group_id == "one-group")).all()
        assert len(rows) == 3
        assert sum(row.is_current for row in rows) == 1


def test_large_storage_and_token_quotas_survive_postgresql_roundtrip(persisted):
    from sqlmodel import Session

    from app.models.user import Tenant
    from app.services.quota import consume_tokens, update_tenant_quota

    engine, _ = persisted
    with Session(engine) as session:
        update_tenant_quota(session, actor_id="system_admin", actor_tenant_id="tenant-a", tenant_id="tenant-a",
                            message_limit=None, storage_limit_bytes=10 * 1024**3, token_limit=5_000_000_000,
                            reason="isolated persistence contract")
        consume_tokens(session, "tenant-a", 2**31 + 7)
    with Session(engine) as session:
        tenant = session.get(Tenant, "tenant-a")
        assert tenant.storage_limit_bytes == 10 * 1024**3
        assert tenant.token_limit == 5_000_000_000
        assert tenant.tokens_used_period == 2**31 + 7


def test_historical_document_republish_releases_current_unique_key_first(persisted):
    from sqlmodel import Session, col, select

    from app.models.rag import Document
    from app.models.user import User
    from app.rag.embeddings.mock import MockEmbeddingProvider
    from app.rag.service import RAGService

    engine, _ = persisted
    with Session(engine) as session:
        session.add_all([
            Document(id="zzz-current", tenant_id="tenant-a", user_id="member", title="Current",
                     version_group_id="republish-group", is_current=True, version_state="published"),
            Document(id="aaa-history", tenant_id="tenant-a", user_id="member", title="History",
                     version_group_id="republish-group", is_current=False, version_state="replaced"),
        ])
        session.commit()
        result = RAGService(session, "tenant-a", embedding_provider=MockEmbeddingProvider()).publish_document(
            "aaa-history", session.get(User, "tenant_admin"),
        )
        assert result is not None and result.id == "aaa-history"
    with Session(engine) as session:
        current = session.exec(select(Document).where(
            Document.version_group_id == "republish-group", col(Document.is_current).is_(True),
        )).all()
        assert [row.id for row in current] == ["aaa-history"]
        assert session.get(Document, "zzz-current").version_state == "replaced"


@pytest.mark.asyncio
async def test_new_version_failure_after_demotion_restores_prior_current(persisted, monkeypatch):
    from sqlmodel import Session, select

    from app.models.rag import Document
    from app.rag.embeddings.mock import MockEmbeddingProvider
    from app.rag.service import RAGService

    engine, _ = persisted
    with Session(engine) as session:
        session.add(Document(id="kept-current", tenant_id="tenant-a", user_id="member", title="Kept",
                             version_group_id="rollback-group", is_current=True, version_state="published"))
        session.commit()
        service = RAGService(session, "tenant-a", embedding_provider=MockEmbeddingProvider())

        async def fail_after_demotion(chunks):
            assert session.get(Document, "kept-current").is_current is False
            raise RuntimeError("isolated vector write failure")

        monkeypatch.setattr(service._vector_store, "add", fail_after_demotion)
        with pytest.raises(RuntimeError, match="isolated vector write failure"):
            await service.ingest_text(
                "New version must roll back", "Replacement", "isolated-source", "member",
                version_group_id="rollback-group", version_number=2, previous_document_id="kept-current",
            )
    with Session(engine) as session:
        rows = session.exec(select(Document).where(Document.version_group_id == "rollback-group")).all()
        assert [row.id for row in rows] == ["kept-current"]
        assert rows[0].is_current is True
        assert rows[0].version_state == "published"


@pytest.mark.parametrize("duplicate", [False, True])
def test_existing_store_without_current_version_index_is_repaired_or_rejected(pg_database, monkeypatch, duplicate):
    from app.models.rag import Document

    monkeypatch.setattr(settings, "DATABASE_URL", pg_database.url.render_as_string(hide_password=False))
    command.upgrade(Config(str(ROOT / "alembic.ini")), "f9b5c7d8e0f1")
    with pg_database.begin() as connection:
        connection.exec_driver_sql("DROP INDEX uq_rag_documents_one_current_version")
    table = sa.Table("rag_documents", sa.MetaData(), autoload_with=pg_database)
    with pg_database.begin() as connection:
        for index in range(2 if duplicate else 1):
            values = Document(id=str(index), tenant_id="tenant", user_id="user", title="kept",
                              version_group_id="one-group", is_current=True).model_dump()
            connection.execute(sa.insert(table).values({key: value for key, value in values.items() if key in table.c}))
    if duplicate:
        with pytest.raises(sa.exc.IntegrityError):
            upgrade(pg_database, monkeypatch)
    else:
        upgrade(pg_database, monkeypatch)
        assert "uq_rag_documents_one_current_version" in {
            index["name"] for index in sa.inspect(pg_database).get_indexes("rag_documents")
        }
    with pg_database.connect() as connection:
        assert connection.exec_driver_sql("SELECT count(*) FROM rag_documents").scalar_one() == (2 if duplicate else 1)
