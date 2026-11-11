"""租户级合规清除：仅在隔离库与测试租户上验证。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.audit.models import AuditAction, AuditLog
from app.core.tenants.purge import TenantPurgeError, collect_tenant_counts, purge_tenant, verify_tenant_purged
from app.models.conversation import Conversation, Message
from app.models.membership import Invitation, Membership
from app.models.rag import Document, DocumentChunk
from app.models.skill import Skill
from app.models.user import Tenant, User
from app.models.workflow import Workflow
from app.rag.document_storage import list_tenant_source_files
from scripts.purge_tenant_data import main as purge_script_main


def _engine(tmp_path: Path):
    from app.core.tenants.purge import import_persistence_models

    import_persistence_models()
    url = "sqlite:///" + (tmp_path / "isolated.db").as_posix()
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})
    SQLModel.metadata.create_all(engine)
    return engine, url


def _add_tenant(session: Session, *, name: str, test_user: bool = True) -> tuple[Tenant, User]:
    tenant = Tenant(name=name, is_active=True)
    session.add(tenant)
    session.commit()
    session.refresh(tenant)
    user = User(
        tenant_id=tenant.id,
        username=f"user-{tenant.id[:8]}",
        email=f"{tenant.id[:8]}@example.test",
        hashed_password="x",
        is_test_user=test_user,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return tenant, user


def _seed_business(
    session: Session,
    tenant: Tenant,
    user: User,
    *,
    knowledge_root: Path,
    title: str,
) -> None:
    conversation = Conversation(tenant_id=tenant.id, user_id=user.id, title=title)
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    session.add(Message(conversation_id=conversation.id, role="user", content="请查看订单"))
    document = Document(tenant_id=tenant.id, user_id=user.id, title=title, source="upload")
    session.add(document)
    session.commit()
    session.refresh(document)
    session.add(
        DocumentChunk(
            tenant_id=tenant.id,
            document_id=document.id,
            chunk_index=0,
            content=f"{title} 证据句",
            source=title,
        )
    )
    session.add(Skill(
        tenant_id=tenant.id,
        owner_id=user.id,
        name=f"skill-{title}",
        name_key=f"skill-{title}",
        description=title,
        constraints="",
        system_prompt="",
        example="",
        keywords="[]",
    ))
    session.add(
        Workflow(
            tenant_id=tenant.id,
            owner_id=user.id,
            name=f"flow-{title}",
            cron_expr="0 0 * * *",
            prompt_template="hello",
        )
    )
    session.add(
        AuditLog(
            action=AuditAction.USER_LOGIN.value,
            tenant_id=tenant.id,
            user_id=user.id,
            resource_type="user",
            resource_id=user.id,
            details=json.dumps({"username": user.username}, ensure_ascii=False),
        )
    )
    session.commit()
    tenant_dir = knowledge_root / tenant.id
    tenant_dir.mkdir(parents=True, exist_ok=True)
    (tenant_dir / "source.txt").write_text(f"{title} file", encoding="utf-8")


def _common_kwargs(url: str, tmp_path: Path, knowledge: Path) -> dict[str, object]:
    return {
        "allow_isolated_database": True,
        "database_url": url,
        "environment": "development",
        "report_dir": tmp_path / "reports",
        "knowledge_root": knowledge,
        "vector_backend": "local",
    }


def test_dry_run_does_not_delete(tmp_path: Path) -> None:
    engine, url = _engine(tmp_path)
    knowledge = tmp_path / "knowledge"
    with Session(engine) as session:
        tenant, user = _add_tenant(session, name="test-acme")
        _seed_business(session, tenant, user, knowledge_root=knowledge, title="acme-doc")
        before = collect_tenant_counts(session, tenant.id)
        result = purge_tenant(session, tenant.id, dry_run=True, **_common_kwargs(url, tmp_path, knowledge))
        after = collect_tenant_counts(session, tenant.id)
        assert result.dry_run is True
        assert result.verified is False
        assert result.files_planned == 1
        assert after == before
        assert session.get(Tenant, tenant.id) is not None
        assert list_tenant_source_files(tenant.id, root=knowledge)
        assert result.confirmation_path is None
        assert result.audit_purge_id is None


def test_execute_requires_matching_confirmation(tmp_path: Path) -> None:
    engine, url = _engine(tmp_path)
    knowledge = tmp_path / "knowledge"
    with Session(engine) as session:
        tenant, _user = _add_tenant(session, name="test-acme")
        with pytest.raises(TenantPurgeError) as mismatch:
            purge_tenant(
                session,
                tenant.id,
                dry_run=False,
                confirm_tenant_id="wrong",
                i_understand_irreversible=True,
                **_common_kwargs(url, tmp_path, knowledge),
            )
        assert mismatch.value.code == "confirmation_mismatch"
        with pytest.raises(TenantPurgeError) as missing:
            purge_tenant(
                session,
                tenant.id,
                dry_run=False,
                confirm_tenant_id=tenant.id,
                i_understand_irreversible=False,
                **_common_kwargs(url, tmp_path, knowledge),
            )
        assert missing.value.code == "irreversible_not_acknowledged"
        assert session.get(Tenant, tenant.id) is not None


def test_non_test_tenant_and_non_test_user_refused(tmp_path: Path) -> None:
    engine, url = _engine(tmp_path)
    knowledge = tmp_path / "knowledge"
    with Session(engine) as session:
        prod, _user = _add_tenant(session, name="acme")
        with pytest.raises(TenantPurgeError) as refused:
            purge_tenant(session, prod.id, dry_run=True, **_common_kwargs(url, tmp_path, knowledge))
        assert refused.value.code == "non_test_tenant_refused"
        test_tenant, user = _add_tenant(session, name="test-beta", test_user=False)
        with pytest.raises(TenantPurgeError) as user_refused:
            purge_tenant(session, test_tenant.id, dry_run=True, **_common_kwargs(url, tmp_path, knowledge))
        assert user_refused.value.code == "non_test_user_refused"
        assert session.get(User, user.id) is not None


def test_production_env_and_default_database_refused(tmp_path: Path) -> None:
    engine, url = _engine(tmp_path)
    knowledge = tmp_path / "knowledge"
    with Session(engine) as session:
        tenant, _user = _add_tenant(session, name="test-acme")
        kwargs = _common_kwargs(url, tmp_path, knowledge)
        with pytest.raises(TenantPurgeError) as prod:
            purge_tenant(session, tenant.id, dry_run=True, **{**kwargs, "environment": "production"})
        assert prod.value.code == "production_environment_refused"
        with pytest.raises(TenantPurgeError) as default_db:
            purge_tenant(
                session,
                tenant.id,
                dry_run=True,
                **{**kwargs, "database_url": "sqlite:///./data/ai_assistant.db"},
            )
        assert default_db.value.code == "default_database_refused"
        with pytest.raises(TenantPurgeError) as isolated:
            purge_tenant(session, tenant.id, dry_run=True, **{**kwargs, "allow_isolated_database": False})
        assert isolated.value.code == "isolated_database_required"


def test_cross_tenant_membership_refused(tmp_path: Path) -> None:
    engine, url = _engine(tmp_path)
    knowledge = tmp_path / "knowledge"
    with Session(engine) as session:
        target, user = _add_tenant(session, name="test-acme")
        other, _other_user = _add_tenant(session, name="test-other")
        session.add(Membership(user_id=user.id, tenant_id=other.id, role="member"))
        session.commit()
        with pytest.raises(TenantPurgeError) as refused:
            purge_tenant(session, target.id, dry_run=True, **_common_kwargs(url, tmp_path, knowledge))
        assert refused.value.code == "cross_tenant_membership_refused"


def test_invalid_tenant_id_refused(tmp_path: Path) -> None:
    engine, url = _engine(tmp_path)
    knowledge = tmp_path / "knowledge"
    with Session(engine) as session:
        with pytest.raises(TenantPurgeError) as refused:
            purge_tenant(session, "../other", dry_run=True, **_common_kwargs(url, tmp_path, knowledge))
        assert refused.value.code == "invalid_tenant_id"


def test_execute_purges_target_and_keeps_other_tenant(tmp_path: Path) -> None:
    engine, url = _engine(tmp_path)
    knowledge = tmp_path / "knowledge"
    with Session(engine) as session:
        target, target_user = _add_tenant(session, name="test-acme")
        other, other_user = _add_tenant(session, name="test-kept")
        _seed_business(session, target, target_user, knowledge_root=knowledge, title="purge-me")
        _seed_business(session, other, other_user, knowledge_root=knowledge, title="keep-me")
        session.add(
            Invitation(
                code="invite-target",
                tenant_id=target.id,
                created_by=target_user.id,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        session.commit()
        other_before = collect_tenant_counts(session, other.id)
        result = purge_tenant(
            session,
            target.id,
            dry_run=False,
            confirm_tenant_id=target.id,
            i_understand_irreversible=True,
            confirmed_by="operator-a",
            **_common_kwargs(url, tmp_path, knowledge),
        )
        assert result.verified is True
        assert result.files_deleted == 1
        assert result.files_remaining == 0
        assert result.confirmation_path is not None and result.confirmation_path.is_file()
        confirmation = json.loads(result.confirmation_path.read_text(encoding="utf-8"))
        assert confirmation["tenant_id"] == target.id
        assert confirmation["confirmed_by"] == "operator-a"
        assert confirmation["timezone"] == "Asia/Shanghai"
        assert result.backup_dir is not None
        assert (result.backup_dir / "inventory.json").is_file()
        assert (result.backup_dir / "knowledge" / "source.txt").is_file()
        assert result.report_path is not None
        report = json.loads(result.report_path.read_text(encoding="utf-8"))
        assert report["verified"] is True
        assert session.get(Tenant, target.id) is None
        assert session.get(User, target_user.id) is None
        assert session.exec(select(Conversation).where(Conversation.tenant_id == target.id)).first() is None
        assert session.exec(select(Document).where(Document.tenant_id == target.id)).first() is None
        assert session.exec(select(DocumentChunk).where(DocumentChunk.tenant_id == target.id)).first() is None
        leftover = verify_tenant_purged(session, target.id, knowledge_root=knowledge)
        assert leftover["verified"] is True
        logs = list(session.exec(select(AuditLog).where(AuditLog.tenant_id == target.id)))
        actions = sorted(row.action for row in logs)
        assert actions == [AuditAction.TENANT_PURGE.value, AuditAction.TENANT_PURGE_CONFIRMED.value]
        assert {row.id for row in logs} == {result.audit_confirmation_id, result.audit_purge_id}
        assert collect_tenant_counts(session, other.id) == other_before
        assert session.get(Tenant, other.id) is not None
        assert session.get(User, other_user.id) is not None
        assert list_tenant_source_files(other.id, root=knowledge)
        assert list_tenant_source_files(target.id, root=knowledge) == []


def test_script_defaults_to_dry_run(tmp_path: Path) -> None:
    engine, url = _engine(tmp_path)
    knowledge = tmp_path / "knowledge"
    with Session(engine) as session:
        tenant, user = _add_tenant(session, name="test-cli")
        _seed_business(session, tenant, user, knowledge_root=knowledge, title="cli-doc")
        tenant_id = tenant.id
    code = purge_script_main(
        [
            "--tenant-id",
            tenant_id,
            "--database-url",
            url,
            "--isolated-database",
            "--environment",
            "development",
            "--knowledge-root",
            str(knowledge),
            "--report-dir",
            str(tmp_path / "reports"),
        ]
    )
    assert code == 0
    with Session(engine) as session:
        assert session.get(Tenant, tenant_id) is not None
        assert list_tenant_source_files(tenant_id, root=knowledge)


def test_script_execute_writes_report(tmp_path: Path) -> None:
    engine, url = _engine(tmp_path)
    knowledge = tmp_path / "knowledge"
    report_dir = tmp_path / "reports"
    with Session(engine) as session:
        tenant, user = _add_tenant(session, name="test-cli")
        _seed_business(session, tenant, user, knowledge_root=knowledge, title="cli-doc")
        tenant_id = tenant.id
    code = purge_script_main(
        [
            "--tenant-id",
            tenant_id,
            "--execute",
            "--confirm-tenant-id",
            tenant_id,
            "--i-understand-irreversible",
            "--isolated-database",
            "--database-url",
            url,
            "--environment",
            "development",
            "--knowledge-root",
            str(knowledge),
            "--report-dir",
            str(report_dir),
            "--confirmed-by",
            "cli-operator",
        ]
    )
    assert code == 0
    reports = list(report_dir.glob("tenant-purge-report-*.json"))
    assert len(reports) == 1
    payload = json.loads(reports[0].read_text(encoding="utf-8"))
    assert payload["verified"] is True
    with Session(engine) as session:
        assert session.get(Tenant, tenant_id) is None
        check = verify_tenant_purged(session, tenant_id, knowledge_root=knowledge)
        assert check["verified"] is True
