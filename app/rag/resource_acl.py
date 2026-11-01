"""Authoritative resource read checks, isolated from the legacy read chain."""

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col

from app.audit.models import AuditLog
from app.core.security import Role, check_permission
from app.models.rag import Document, DocumentACL, DocumentACLGrant, DocumentACLProposal
from app.models.user import Tenant, User
from app.rag.access import kb_scope
from app.rag.effective_date import document_is_live, ensure_utc


@dataclass(frozen=True)
class ResourceReadDecision:
    allowed: bool
    revision: int | None = None


def resource_read_decision(
    session: Session, document_id: str, subject: User,
) -> ResourceReadDecision:
    """Revalidate the current subject and resource; never trust vector metadata.

    Database errors propagate to the caller; callers must never fall back to a
    broader read scope. This service neither commits nor modifies permissions.
    """
    denied = ResourceReadDecision(False)
    subject_id, subject_tenant, subject_version = subject.id, subject.tenant_id, subject.token_version
    user = session.execute(
        select(col(User.id), col(User.tenant_id), col(User.token_version), col(User.is_active), col(User.role))
        .where(col(User.id) == subject_id)
    ).first()
    if (
        user is None or not user.is_active or user.tenant_id != subject_tenant
        or user.token_version != subject_version
    ):
        return denied
    tenant = session.get(Tenant, user.tenant_id, populate_existing=True)
    if tenant is None or not tenant.is_active:
        return denied
    try:
        permitted = check_permission(user.role, "knowledge_bases", "read")
    except ValueError:
        return denied
    if not permitted:
        return denied
    doc = session.get(Document, document_id, populate_existing=True)
    if (
        doc is None or doc.tenant_id != user.tenant_id
        or doc.deleted_at is not None or not doc.is_current
    ):
        return denied
    if not document_is_live(doc.is_current, doc.effective_at, doc.expires_at, datetime.now(UTC)):
        return denied
    if kb_scope() == "uploader" and doc.user_id != user.id:
        return denied
    acl = session.get(DocumentACL, document_id, populate_existing=True)
    if acl is None or acl.tenant_id != user.tenant_id or acl.revision < 1:
        return denied
    if acl.visibility not in {"tenant", "private", "restricted"}:
        return denied
    allowed = acl.visibility == "tenant" or doc.user_id == user.id
    if not allowed and acl.visibility == "restricted":
        grant = session.get(DocumentACLGrant, (document_id, user.id), populate_existing=True)
        allowed = grant is not None and grant.tenant_id == user.tenant_id
    return ResourceReadDecision(allowed, acl.revision if allowed else None)


class ACLChangeConflictError(ValueError):
    """The reviewed document permissions have changed or were already consumed."""


def _require_admin(session: Session, user_id: str, tenant_id: str, token_version: int) -> None:
    row = session.execute(
        select(col(User.tenant_id), col(User.token_version), col(User.is_active), col(User.role))
        .where(col(User.id) == user_id).with_for_update()
    ).first()
    tenant = session.execute(
        select(col(Tenant.is_active)).where(col(Tenant.id) == tenant_id).with_for_update()
    ).first()
    if (
        row is None or row.tenant_id != tenant_id or row.token_version != token_version
        or not row.is_active or row.role not in {Role.TENANT_ADMIN.value, Role.SYSTEM_ADMIN.value}
        or tenant is None or not tenant.is_active
    ):
        raise PermissionError("Document permission change is not authorized")


def _require_document(session: Session, document_id: str, tenant_id: str) -> None:
    row = session.execute(
        select(col(Document.tenant_id), col(Document.deleted_at), col(Document.is_current))
        .where(col(Document.id) == document_id).with_for_update()
    ).first()
    if row is None or row.tenant_id != tenant_id or row.deleted_at is not None or not row.is_current:
        raise PermissionError("Document permission change is not authorized")


def _payload(visibility: str, user_ids: list[str]) -> str:
    if visibility not in {"tenant", "private", "restricted"}:
        raise ValueError("Invalid document visibility")
    if (
        not isinstance(user_ids, list) or len(user_ids) > 1000
        or any(not isinstance(uid, str) or not uid or len(uid) > 128 for uid in user_ids)
    ):
        raise ValueError("Invalid document grantees")
    if visibility != "restricted" and user_ids:
        raise ValueError("Only restricted documents accept explicit grantees")
    return json.dumps({"visibility": visibility, "user_ids": sorted(set(user_ids))}, sort_keys=True)


def _require_grantees(session: Session, tenant_id: str, user_ids: list[str]) -> None:
    if not user_ids:
        return
    rows = session.execute(
        select(col(User.id), col(User.tenant_id), col(User.is_active))
        .where(col(User.id).in_(user_ids)).with_for_update()
    ).all()
    if len(rows) != len(user_ids) or any(not row.is_active or row.tenant_id != tenant_id for row in rows):
        raise PermissionError("Document grantees are not available in this tenant")


def _proposal_digest(row: DocumentACLProposal) -> str:
    material = json.dumps({
        "id": row.id, "document_id": row.document_id, "tenant_id": row.tenant_id,
        "proposed_by": row.proposed_by, "proposer_token_version": row.proposer_token_version,
        "expected_revision": row.expected_revision, "payload": row.payload,
        "expires_at": ensure_utc(row.expires_at).isoformat(),
    }, sort_keys=True)
    return hashlib.sha256(material.encode()).hexdigest()


def _current_revision(session: Session, document_id: str, tenant_id: str) -> int:
    row = session.execute(
        select(col(DocumentACL.tenant_id), col(DocumentACL.revision))
        .where(col(DocumentACL.document_id) == document_id)
    ).first()
    if row is None:
        return 0
    if row.tenant_id != tenant_id or row.revision < 1:
        raise ACLChangeConflictError("Document permissions cannot be changed")
    return row.revision



@contextmanager
def _change_transaction(session: Session) -> Iterator[None]:
    """Keep a released savepoint inside the caller transaction on SQLite too."""
    connection = session.connection()
    if (
        connection.dialect.name == "sqlite"
        and not getattr(connection.connection.driver_connection, "in_transaction", True)
    ):
        connection.exec_driver_sql("BEGIN")
    with session.begin_nested():
        yield


def propose_acl_change(
    session: Session, subject: User, document_id: str, *,
    expected_revision: int, visibility: str, user_ids: list[str],
) -> DocumentACLProposal:
    """Persist a reviewed-content proposal without changing access or committing.

    The authenticated human controller supplies the subject. This service is
    deliberately not exposed as an Agent tool or automatic approval path.
    """
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("Invalid expected permission revision")
    payload = _payload(visibility, user_ids)
    actor_id, tenant_id, token_version = subject.id, subject.tenant_id, subject.token_version
    with _change_transaction(session):
        _require_admin(session, actor_id, tenant_id, token_version)
        _require_document(session, document_id, tenant_id)
        _require_grantees(session, tenant_id, sorted(set(user_ids)))
        if _current_revision(session, document_id, tenant_id) != expected_revision:
            raise ACLChangeConflictError("Document permissions changed; propose again")
        row = DocumentACLProposal(
            document_id=document_id, tenant_id=tenant_id, proposed_by=actor_id,
            proposer_token_version=token_version, expected_revision=expected_revision,
            payload=payload, payload_digest="", expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        row.payload_digest = _proposal_digest(row)
        session.add(row)
        session.add(AuditLog(
            user_id=actor_id, tenant_id=tenant_id, action="document_acl_proposed",
            resource_type="document", resource_id=document_id,
            details=json.dumps({"proposal_id": row.id, "digest": row.payload_digest}),
        ))
        session.flush()
    return row


def confirm_acl_change(
    session: Session, subject: User, proposal_id: str, *, reviewed_digest: str,
) -> int:
    """Confirm and apply exactly the human-reviewed proposal once, atomically.

    No approved boolean is accepted. The caller must commit this transaction;
    failure rolls back the consumption, permissions, grantees and audit together.
    """
    actor_id, tenant_id, token_version = subject.id, subject.tenant_id, subject.token_version
    try:
        with _change_transaction(session):
            _require_admin(session, actor_id, tenant_id, token_version)
            row = session.get(DocumentACLProposal, proposal_id, populate_existing=True, with_for_update=True)
            now = datetime.now(UTC)
            if (
                row is None or row.tenant_id != tenant_id or row.consumed_at is not None
                or ensure_utc(row.expires_at) <= now or row.payload_digest != reviewed_digest
                or _proposal_digest(row) != reviewed_digest
            ):
                raise ACLChangeConflictError("Confirmation is unavailable; review a new proposal")
            _require_admin(session, row.proposed_by, tenant_id, row.proposer_token_version)
            _require_document(session, row.document_id, tenant_id)
            data = json.loads(row.payload)
            if set(data) != {"visibility", "user_ids"} or _payload(data["visibility"], data["user_ids"]) != row.payload:
                raise ACLChangeConflictError("Confirmation payload is invalid")
            _require_grantees(session, tenant_id, data["user_ids"])
            if _current_revision(session, row.document_id, tenant_id) != row.expected_revision:
                raise ACLChangeConflictError("Document permissions changed; propose again")
            now = datetime.now(UTC)
            if ensure_utc(row.expires_at) <= now:
                raise ACLChangeConflictError("Confirmation expired while validating; review again")
            consumed = session.execute(
                update(DocumentACLProposal).where(
                    col(DocumentACLProposal.id) == proposal_id,
                    col(DocumentACLProposal.consumed_at).is_(None),
                    col(DocumentACLProposal.payload_digest) == reviewed_digest,
                    col(DocumentACLProposal.expires_at) > now,
                ).values(consumed_at=now, confirmed_by=actor_id),
                execution_options={"synchronize_session": False},
            )
            if getattr(consumed, "rowcount", 0) != 1:
                raise ACLChangeConflictError("Confirmation already consumed")
            revision = row.expected_revision + 1
            if row.expected_revision == 0:
                session.add(DocumentACL(
                    document_id=row.document_id, tenant_id=tenant_id, visibility=data["visibility"],
                    revision=revision, updated_by=actor_id, updated_at=now,
                ))
                session.flush()
            else:
                changed = session.execute(
                    update(DocumentACL).where(
                        col(DocumentACL.document_id) == row.document_id,
                        col(DocumentACL.tenant_id) == tenant_id,
                        col(DocumentACL.revision) == row.expected_revision,
                    ).values(visibility=data["visibility"], revision=revision, updated_by=actor_id, updated_at=now),
                    execution_options={"synchronize_session": False},
                )
                if getattr(changed, "rowcount", 0) != 1:
                    raise ACLChangeConflictError("Document permissions changed; propose again")
            session.execute(delete(DocumentACLGrant).where(col(DocumentACLGrant.document_id) == row.document_id))
            for uid in data["user_ids"]:
                session.add(DocumentACLGrant(document_id=row.document_id, user_id=uid, tenant_id=tenant_id))
            session.add(AuditLog(
                user_id=actor_id, tenant_id=tenant_id, action="document_acl_changed",
                resource_type="document", resource_id=row.document_id,
                details=json.dumps({
                    "proposal_id": proposal_id, "proposed_by": row.proposed_by,
                    "confirmed_by": actor_id, "digest": reviewed_digest,
                    "previous_revision": row.expected_revision, "revision": revision,
                }),
            ))
            session.flush()
        return revision
    except IntegrityError as exc:
        raise ACLChangeConflictError("Document permissions changed; propose again") from exc
