"""Isolated authorization counterexamples without model or embedding calls."""

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models.rag import Document, DocumentACL, DocumentACLGrant
from app.models.user import Tenant, User
from app.rag.resource_acl import resource_read_decision


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", "tenant")
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="t", name="tenant"))
        for uid in ("owner", "peer"):
            session.add(User(id=uid, tenant_id="t", username=uid, hashed_password="unused"))
        session.add(Document(id="doc", tenant_id="t", user_id="owner", title="private"))
        session.commit()
        yield session
    engine.dispose()


def decide(db, uid="peer"):
    subject = db.get(User, uid)
    assert subject is not None
    return resource_read_decision(db, "doc", subject)


def acl(db, visibility):
    db.add(DocumentACL(document_id="doc", tenant_id="t", visibility=visibility, updated_by="owner"))
    db.commit()


@pytest.mark.parametrize(
    "visibility,expected",
    [("tenant", True), ("private", False), ("restricted", False), ("unknown", False)],
)
def test_visibility(db, visibility, expected):
    acl(db, visibility)
    assert decide(db).allowed is expected


def test_missing_acl_denies_owner(db):
    assert not decide(db, "owner").allowed


def test_private_owner_allowed(db):
    acl(db, "private")
    assert decide(db, "owner").allowed


def test_grant_and_revocation(db):
    acl(db, "restricted")
    grant = DocumentACLGrant(document_id="doc", user_id="peer", tenant_id="t")
    db.add(grant)
    db.commit()
    assert decide(db).revision == 1
    db.delete(grant)
    db.commit()
    assert not decide(db).allowed


@pytest.mark.parametrize(
    "mutation",
    ["disabled", "tenant_disabled", "deleted", "old_version", "cross_tenant", "wrong_acl_tenant"],
)
def test_resource_and_subject_revalidation(db, mutation):
    acl(db, "tenant")
    user = db.get(User, "peer")
    doc = db.get(Document, "doc")
    if mutation == "disabled":
        user.is_active = False
    elif mutation == "tenant_disabled":
        db.get(Tenant, "t").is_active = False
    elif mutation == "deleted":
        from datetime import UTC, datetime
        doc.deleted_at = datetime.now(UTC)
    elif mutation == "old_version":
        doc.is_current = False
    elif mutation == "cross_tenant":
        doc.tenant_id = "other"
    else:
        db.get(DocumentACL, "doc").tenant_id = "other"
    db.commit()
    assert not decide(db).allowed


def test_uploader_admin_not_exempt(db, monkeypatch):
    acl(db, "tenant")
    db.get(User, "peer").role = "system_admin"
    db.commit()
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", "uploader")
    assert not decide(db).allowed


def test_private_grant_does_not_override_visibility(db):
    acl(db, "private")
    db.add(DocumentACLGrant(document_id="doc", user_id="peer", tenant_id="t"))
    db.commit()
    assert not decide(db).allowed


def test_migration_adds_empty_tables_and_refuses_destructive_rollback(monkeypatch):
    import importlib.util
    from pathlib import Path

    from sqlalchemy import inspect

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = Path(__file__).resolve().parents[1] / "alembic/versions/fc28fa01b3c4_add_document_resource_acl.py"
    spec = importlib.util.spec_from_file_location("acl_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(connection)))
        module.upgrade()
        tables = set(inspect(connection).get_table_names())
        assert tables == {"rag_document_acl", "rag_document_acl_grants"}
        with pytest.raises(RuntimeError, match="retained"):
            module.downgrade()
        assert set(inspect(connection).get_table_names()) == tables
    engine.dispose()


@pytest.mark.parametrize("field,value", [("token_version", 1), ("tenant_id", "changed")])
def test_identity_map_refresh_preserves_request_snapshot(db, field, value):
    from sqlalchemy import update

    acl(db, "tenant")
    subject = db.get(User, "peer")
    db.execute(
        update(User).where(User.id == "peer").values(**{field: value}),
        execution_options={"synchronize_session": False},
    )
    assert not resource_read_decision(db, "doc", subject).allowed
    assert not resource_read_decision(db, "doc", subject).allowed


@pytest.mark.parametrize("field,days", [("effective_at", 1), ("expires_at", -1)])
def test_document_time_window(db, field, days):
    from datetime import UTC, datetime, timedelta

    acl(db, "tenant")
    setattr(db.get(Document, "doc"), field, datetime.now(UTC) + timedelta(days=days))
    db.commit()
    assert not decide(db).allowed


def admin(db, uid="owner"):
    user = db.get(User, uid)
    user.role = "tenant_admin"
    db.commit()
    return user


def propose(db, actor, visibility="restricted", user_ids=None, revision=0):
    from app.rag.resource_acl import propose_acl_change

    return propose_acl_change(
        db, actor, "doc", expected_revision=revision,
        visibility=visibility, user_ids=["peer"] if user_ids is None else user_ids,
    )


def confirm(db, actor, row, digest=None):
    from app.rag.resource_acl import confirm_acl_change

    return confirm_acl_change(db, actor, row.id, reviewed_digest=row.payload_digest if digest is None else digest)


def test_same_admin_confirms_once_with_atomic_audit(db):
    from sqlalchemy import select

    from app.audit.models import AuditLog
    from app.models.rag import DocumentACLProposal
    from app.rag.resource_acl import ACLChangeConflictError

    actor = admin(db)
    row = propose(db, actor)
    db.commit()
    assert db.get(DocumentACL, "doc") is None
    assert confirm(db, actor, row) == 1
    db.commit()
    assert decide(db).allowed
    assert db.get(DocumentACLProposal, row.id).confirmed_by == actor.id
    logs = db.execute(select(AuditLog).where(AuditLog.action == "document_acl_changed")).scalars().all()
    assert len(logs) == 1 and logs[0].user_id == actor.id
    assert "private" not in logs[0].details
    with pytest.raises(ACLChangeConflictError):
        confirm(db, actor, row)


def test_changed_revision_requires_new_proposal(db):
    from app.rag.resource_acl import ACLChangeConflictError

    actor = admin(db)
    row = propose(db, actor)
    other = propose(db, actor, "private", [])
    db.commit()
    confirm(db, actor, other)
    db.commit()
    with pytest.raises(ACLChangeConflictError):
        confirm(db, actor, row)
    assert db.get(DocumentACL, "doc").visibility == "private"
    assert not decide(db).allowed


def test_update_replaces_grants_and_bumps_revision(db):
    actor = admin(db)
    row = propose(db, actor)
    confirm(db, actor, row)
    db.commit()
    change = propose(db, actor, "private", [], revision=1)
    confirm(db, actor, change)
    db.commit()
    assert db.get(DocumentACL, "doc").revision == 2
    assert db.get(DocumentACLGrant, ("doc", "peer")) is None
    assert not decide(db).allowed


@pytest.mark.parametrize(
    "mutation",
    ["digest", "payload", "expired", "proposer_revoked", "proposer_demoted", "grantee_disabled", "doc_deleted"],
)
def test_confirmation_rechecks_boundaries(db, mutation):
    from datetime import UTC, datetime, timedelta

    from app.rag.resource_acl import ACLChangeConflictError

    actor = admin(db)
    confirmer = admin(db, "peer")
    row = propose(db, actor)
    reviewed = row.payload_digest
    if mutation == "digest":
        reviewed = "bad"
    elif mutation == "payload":
        row.payload = '{"user_ids": [], "visibility": "tenant"}'
    elif mutation == "expired":
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    elif mutation == "proposer_revoked":
        actor.token_version += 1
    elif mutation == "proposer_demoted":
        actor.role = "member"
    elif mutation == "grantee_disabled":
        confirmer.is_active = False
        confirmer = actor
    else:
        db.get(Document, "doc").deleted_at = datetime.now(UTC)
    db.commit()
    with pytest.raises((ACLChangeConflictError, PermissionError)):
        confirm(db, confirmer, row, digest=reviewed)
    assert db.get(DocumentACL, "doc") is None
    assert db.get(DocumentACLGrant, ("doc", "peer")) is None


@pytest.mark.parametrize(
    "visibility,grantees",
    [("invalid", []), ("private", ["peer"]), ("tenant", ["peer"]), ("restricted", ["missing"])],
)
def test_proposal_rejects_invalid_targets(db, visibility, grantees):
    actor = admin(db)
    with pytest.raises((ValueError, PermissionError)):
        propose(db, actor, visibility, grantees)


def test_member_and_cross_tenant_cannot_propose(db):
    with pytest.raises(PermissionError):
        propose(db, db.get(User, "owner"))
    actor = admin(db)
    db.get(Document, "doc").tenant_id = "other"
    db.commit()
    with pytest.raises(PermissionError):
        propose(db, actor)


def test_caller_rollback_retains_no_applied_change(db):
    actor = admin(db)
    row = propose(db, actor)
    db.commit()
    confirm(db, actor, row)
    db.rollback()
    assert db.get(DocumentACL, "doc") is None
    db.refresh(row)
    assert row.consumed_at is None


def test_audit_failure_rolls_back_all_permission_writes(db):
    from sqlalchemy import event

    from app.audit.models import AuditLog

    actor = admin(db)
    row = propose(db, actor)
    db.commit()

    def reject_audit(mapper, connection, target):
        if target.action == "document_acl_changed":
            raise RuntimeError("audit unavailable")

    event.listen(AuditLog, "before_insert", reject_audit)
    try:
        with pytest.raises(RuntimeError, match="audit unavailable"):
            confirm(db, actor, row)
    finally:
        event.remove(AuditLog, "before_insert", reject_audit)
    assert db.get(DocumentACL, "doc") is None
    assert db.get(DocumentACLGrant, ("doc", "peer")) is None
    db.refresh(row)
    assert row.consumed_at is None


def test_different_admin_confirms_with_separate_audit_identity(db):
    import json

    from sqlalchemy import select

    from app.audit.models import AuditLog

    proposer = admin(db)
    confirmer = admin(db, "peer")
    row = propose(db, proposer)
    db.commit()
    assert confirm(db, confirmer, row) == 1
    db.commit()
    log = db.execute(select(AuditLog).where(AuditLog.action == "document_acl_changed")).scalar_one()
    details = json.loads(log.details)
    assert details["proposed_by"] == proposer.id
    assert details["confirmed_by"] == confirmer.id


def test_proposal_outer_rollback_does_not_persist(db):
    from app.models.rag import DocumentACLProposal

    actor = admin(db)
    row = propose(db, actor)
    proposal_id = row.id
    db.rollback()
    assert db.get(DocumentACLProposal, proposal_id) is None


def test_expiry_uses_server_clock(db, monkeypatch):
    from datetime import UTC, datetime, timedelta

    import app.rag.resource_acl as module

    actor = admin(db)
    row = propose(db, actor)
    db.commit()
    future = row.expires_at.replace(tzinfo=UTC) + timedelta(seconds=1)

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return future

    monkeypatch.setattr(module, "datetime", Later)
    with pytest.raises(module.ACLChangeConflictError):
        confirm(db, actor, row)
    assert db.get(DocumentACL, "doc") is None


def test_concurrent_confirmation_changes_permissions_at_most_once(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy import select
    from sqlalchemy.exc import OperationalError

    import app.rag.resource_acl as module
    from app.audit.models import AuditLog
    from app.models.rag import DocumentACLProposal

    engine = create_engine("sqlite:///" + (tmp_path / "acl.db").as_posix(), connect_args={"timeout": 1})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as seed:
        seed.add(Tenant(id="t", name="tenant"))
        seed.add(User(id="owner", tenant_id="t", username="owner", hashed_password="unused", role="tenant_admin"))
        seed.add(Document(id="doc", tenant_id="t", user_id="owner", title="document"))
        seed.commit()
        row = module.propose_acl_change(
            seed, seed.get(User, "owner"), "doc", expected_revision=0, visibility="private", user_ids=[],
        )
        proposal_id, digest = row.id, row.payload_digest
        seed.commit()
    barrier = Barrier(2)
    original_revision = module._current_revision

    def simultaneous_read(*args):
        revision = original_revision(*args)
        barrier.wait(timeout=10)
        return revision

    monkeypatch.setattr(module, "_current_revision", simultaneous_read)

    def execute():
        with Session(engine) as session:
            try:
                result = module.confirm_acl_change(
                    session, session.get(User, "owner"), proposal_id, reviewed_digest=digest,
                )
                session.commit()
                return result
            except (module.ACLChangeConflictError, OperationalError):
                session.rollback()
                return None

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: execute(), range(2)))
        assert results.count(1) == 1
        with Session(engine) as session:
            assert session.get(DocumentACL, "doc").revision == 1
            assert session.get(DocumentACLProposal, proposal_id).consumed_at is not None
            audits = session.execute(select(AuditLog).where(AuditLog.action == "document_acl_changed")).scalars().all()
            assert len(audits) == 1
    finally:
        engine.dispose()


def test_confirmation_migration_retains_records(monkeypatch):
    import importlib.util
    from pathlib import Path

    from sqlalchemy import inspect

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = Path(__file__).resolve().parents[1] / "alembic/versions/fd39ab12c4d5_add_document_acl_proposals.py"
    spec = importlib.util.spec_from_file_location("acl_confirmation_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(connection)))
        module.upgrade()
        assert set(inspect(connection).get_table_names()) == {"rag_document_acl_proposals"}
        with pytest.raises(RuntimeError, match="retained"):
            module.downgrade()
        assert "rag_document_acl_proposals" in inspect(connection).get_table_names()
    engine.dispose()


def test_expiry_is_rechecked_after_waiting_for_authorization_locks(db, monkeypatch):
    from datetime import UTC, datetime, timedelta

    import app.rag.resource_acl as module

    actor = admin(db)
    row = propose(db, actor)
    db.commit()
    deadline = row.expires_at.replace(tzinfo=UTC)
    current = [deadline - timedelta(seconds=1)]
    original_check = module._require_grantees

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current[0]

    def delayed_check(*args):
        original_check(*args)
        current[0] = deadline + timedelta(seconds=1)

    monkeypatch.setattr(module, "datetime", Clock)
    monkeypatch.setattr(module, "_require_grantees", delayed_check)
    with pytest.raises(module.ACLChangeConflictError, match="expired"):
        confirm(db, actor, row)
    assert db.get(DocumentACL, "doc") is None
    db.refresh(row)
    assert row.consumed_at is None


@pytest.mark.parametrize("mutation", ["revoked", "demoted", "disabled", "tenant_disabled"])
def test_confirmer_current_authority_is_required_on_every_attempt(db, mutation):
    from sqlalchemy import update

    from app.rag.resource_acl import propose_acl_change

    proposer = admin(db)
    confirmer = admin(db, "peer")
    row = propose_acl_change(
        db, proposer, "doc", expected_revision=0, visibility="private", user_ids=[],
    )
    db.commit()
    assert confirmer.token_version == 0
    if mutation == "tenant_disabled":
        db.get(Tenant, "t").is_active = False
        db.flush()
    else:
        change = {"token_version": 1} if mutation == "revoked" else (
            {"role": "member"} if mutation == "demoted" else {"is_active": False}
        )
        db.execute(update(User).where(User.id == "peer").values(**change),
                   execution_options={"synchronize_session": False})
    for _ in range(2):
        with pytest.raises(PermissionError):
            confirm(db, confirmer, row)
    assert db.get(DocumentACL, "doc") is None


def test_proposal_cannot_be_confirmed_by_another_tenant_admin(db):
    actor = admin(db)
    row = propose(db, actor)
    db.add(Tenant(id="other", name="other"))
    stranger = User(
        id="stranger", tenant_id="other", username="stranger", hashed_password="unused", role="system_admin",
    )
    db.add(stranger)
    db.commit()
    with pytest.raises(ValueError):
        confirm(db, stranger, row)
    assert db.get(DocumentACL, "doc") is None


def test_audit_failure_restores_existing_acl_and_grants(db):
    from sqlalchemy import event

    from app.audit.models import AuditLog

    actor = admin(db)
    initial = propose(db, actor)
    confirm(db, actor, initial)
    db.commit()
    row = propose(db, actor, "private", [], revision=1)
    db.commit()

    def reject_audit(mapper, connection, target):
        if target.action == "document_acl_changed":
            raise RuntimeError("audit unavailable")

    event.listen(AuditLog, "before_insert", reject_audit)
    try:
        with pytest.raises(RuntimeError):
            confirm(db, actor, row)
    finally:
        event.remove(AuditLog, "before_insert", reject_audit)
    assert db.get(DocumentACL, "doc").revision == 1
    assert db.get(DocumentACLGrant, ("doc", "peer")) is not None
    db.refresh(row)
    assert row.consumed_at is None


def test_acl_migration_rejects_complete_columns_without_primary_key(monkeypatch):
    import importlib.util
    from pathlib import Path

    from sqlalchemy import text

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = Path(__file__).resolve().parents[1] / "alembic/versions/fc28fa01b3c4_add_document_resource_acl.py"
    spec = importlib.util.spec_from_file_location("incompatible_acl_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE rag_document_acl (document_id VARCHAR NOT NULL, tenant_id VARCHAR NOT NULL, "
            "visibility VARCHAR NOT NULL, revision INTEGER NOT NULL, updated_by VARCHAR NOT NULL, "
            "updated_at DATETIME NOT NULL, FOREIGN KEY(document_id) REFERENCES rag_documents(id), "
            "FOREIGN KEY(tenant_id) REFERENCES tenants(id), FOREIGN KEY(updated_by) REFERENCES users(id))"
        ))
        monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(connection)))
        with pytest.raises(RuntimeError, match="incompatible"):
            module.upgrade()
    engine.dispose()
