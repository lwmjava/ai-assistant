"""Smoke test for audit log system."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio

from app.audit import (
    AuditAction,
    AuditLog,
    AuditLogger,
    get_audit_logger,
    reset_audit_logger,
)
from app.core.database import engine, init_db
from sqlmodel import Session, select


def setup():
    """Ensure audit_logs table exists."""
    init_db(auto_migrate=False)


# ── Test 1: AuditAction enum ──
print("Test 1: AuditAction enum values")
assert AuditAction.USER_LOGIN == "user_login"
assert AuditAction.USER_CREATE == "user_create"
assert AuditAction.TENANT_CREATE == "tenant_create"
assert AuditAction.CONVERSATION_CREATE == "conversation_create"
assert AuditAction.KNOWLEDGE_BASE_UPLOAD == "knowledge_base_upload"
assert AuditAction.WORKFLOW_EXECUTE == "workflow_execute"
assert AuditAction.SYSTEM_CONFIG_UPDATE == "system_config_update"
assert AuditAction.CLI_DANGEROUS_OP == "cli_dangerous_op"
assert len(list(AuditAction)) >= 20  # at least 20 action types
print("  OK")


# ── Test 2: AuditLog model instantiation ──
print("Test 2: AuditLog model instantiation")
log = AuditLog(
    action=AuditAction.USER_LOGIN,
    user_id="u1",
    tenant_id="t1",
    resource_type="user",
    resource_id="u1",
    details='{"ip": "1.2.3.4"}',
    ip_address="1.2.3.4",
)
assert log.action == "user_login"
assert log.user_id == "u1"
assert log.tenant_id == "t1"
assert log.resource_type == "user"
assert log.resource_id == "u1"
assert log.id is not None  # auto-generated UUID
assert str(log)  # __repr__ works
print("  OK")


# ── Test 3: AuditLogger singleton ──
print("Test 3: AuditLogger singleton")
reset_audit_logger()
logger1 = get_audit_logger()
logger2 = get_audit_logger()
assert logger1 is logger2, "get_audit_logger should return same instance"
reset_audit_logger()
logger3 = get_audit_logger()
assert logger3 is not logger1, "reset_audit_logger should create new instance"
print("  OK")


# ── Test 4: AuditLogger.log() writes to DB ──
print("Test 4: AuditLogger.log() writes to DB")
setup()
reset_audit_logger()
audit = get_audit_logger()

record = asyncio.run(
    audit.log(
        action=AuditAction.USER_LOGIN,
        user_id="test-user-1",
        tenant_id="test-tenant-1",
        resource_type="user",
        resource_id="test-user-1",
        details={"method": "password", "success": True},
        ip_address="127.0.0.1",
    )
)
assert record is not None, "log() should return an AuditLog record"
assert record.action == "user_login"
assert record.user_id == "test-user-1"
assert record.tenant_id == "test-tenant-1"
assert record.resource_type == "user"
assert record.details is not None
assert "method" in record.details

# Verify in DB
with Session(engine) as session:
    db_log = session.exec(select(AuditLog).where(AuditLog.id == record.id)).first()
    assert db_log is not None
    assert db_log.action == "user_login"
    assert db_log.ip_address == "127.0.0.1"
    session.delete(db_log)
    session.commit()
print("  OK")


# ── Test 5: AuditLogger.log() with string action ──
print("Test 5: AuditLogger.log() with string action")
record = asyncio.run(
    audit.log(
        action="custom_action",
        user_id="u2",
        details="plain string details",
    )
)
assert record is not None
assert record.action == "custom_action"
assert record.details == "plain string details"

# Cleanup
with Session(engine) as session:
    db_log = session.exec(select(AuditLog).where(AuditLog.id == record.id)).first()
    if db_log:
        session.delete(db_log)
        session.commit()
print("  OK")


# ── Test 6: AuditLogger.log() with None fields ──
print("Test 6: AuditLogger.log() with minimal fields")
record = asyncio.run(audit.log(action=AuditAction.OTHER))
assert record is not None
assert record.action == "other"
assert record.user_id is None
assert record.tenant_id is None
assert record.resource_type is None
assert record.details is None

# Cleanup
with Session(engine) as session:
    db_log = session.exec(select(AuditLog).where(AuditLog.id == record.id)).first()
    if db_log:
        session.delete(db_log)
        session.commit()
print("  OK")


# ── Test 7: cleanup_old_logs ──
print("Test 7: cleanup_old_logs")
deleted = asyncio.run(audit.cleanup_old_logs())
print(f"  deleted={deleted} (expected 0 for fresh DB)")
print("  OK")


# ── Test 8: AuditLog model has all required columns ──
print("Test 8: AuditLog table columns")
columns = {c.name for c in AuditLog.__table__.columns}
required = {"id", "user_id", "tenant_id", "action", "resource_type", "resource_id", "details", "ip_address", "user_agent", "created_at", "updated_at"}
assert required.issubset(columns), f"Missing columns: {required - columns}"
print("  OK")


# ── Test 9: Multiple log entries ──
print("Test 9: Multiple log entries")
records = []
for i in range(3):
    r = asyncio.run(
        audit.log(
            action=AuditAction.USER_CREATE,
            user_id=f"user-{i}",
            tenant_id="t1",
            details={"batch": "test", "index": i},
        )
    )
    records.append(r)

assert all(r is not None for r in records)
assert len({r.id for r in records}) == 3  # all unique IDs

# Cleanup
with Session(engine) as session:
    for r in records:
        db_log = session.exec(select(AuditLog).where(AuditLog.id == r.id)).first()
        if db_log:
            session.delete(db_log)
    session.commit()
print("  OK")


print("\n=== All 9 tests passed ===")