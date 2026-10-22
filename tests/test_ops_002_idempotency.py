"""OPS-002：导入幂等键与租户切换并发（单实例）。"""

import threading

from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, col, select

from app.core.database import engine
from app.core.idempotency import import_batch_keys, switch_tenant_keys
from app.core.security import Role, hash_password
from app.core.tenant_switch import switch_tenant_locked
from app.main import app
from app.models.rag import ImportBatch
from app.models.user import Tenant, User
from app.services.membership import ensure_membership

PASSWORD = "Ops002@Pass1"


def setup_function() -> None:
    import app.models.membership  # noqa: F401
    import app.models.rag  # noqa: F401
    import app.models.user  # noqa: F401

    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    import_batch_keys.clear()
    switch_tenant_keys.clear()


def _client() -> TestClient:
    return TestClient(app)


def test_import_upload_idempotency_key_returns_same_batch() -> None:
    client = _client()
    with Session(engine) as session:
        tenant = Tenant(name="ops")
        session.add(tenant)
        session.commit()
        session.refresh(tenant)
        user = User(
            tenant_id=tenant.id,
            username="uploader",
            hashed_password=hash_password(PASSWORD),
            role=Role.MEMBER.value,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        ensure_membership(session, user)
        uploader_id = user.id

    login = client.post("/api/v1/auth/login", json={"username": "uploader", "password": PASSWORD})
    headers = {
        "Authorization": f"Bearer {login.json()['access_token']}",
        "Idempotency-Key": "upload-once-1",
    }
    files = {"files": ("note.txt", b"hello idempotent", "text/plain")}
    first = client.post("/api/v1/rag/import-jobs/upload", headers=headers, files=files)
    assert first.status_code == 202
    first_id = first.json()["id"]

    second = client.post(
        "/api/v1/rag/import-jobs/upload",
        headers=headers,
        files={"files": ("note.txt", b"hello idempotent", "text/plain")},
    )
    assert second.status_code == 202
    assert second.json()["id"] == first_id

    with Session(engine) as session:
        batches = session.exec(
            select(ImportBatch).where(col(ImportBatch.user_id) == uploader_id)
        ).all()
        assert len(batches) == 1


def test_concurrent_switch_tenant_stays_consistent() -> None:
    with Session(engine) as session:
        home = Tenant(name="home")
        other = Tenant(name="other")
        session.add(home)
        session.add(other)
        session.commit()
        session.refresh(home)
        session.refresh(other)
        user = User(
            tenant_id=home.id,
            username="switcher",
            hashed_password=hash_password(PASSWORD),
            role=Role.MEMBER.value,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        ensure_membership(session, user)
        user.tenant_id = other.id
        session.add(user)
        session.commit()
        session.refresh(user)
        ensure_membership(session, user)
        user.tenant_id = home.id
        session.add(user)
        session.commit()
        user_id = user.id
        other_id = other.id

    barrier = threading.Barrier(4)
    results: list[str] = []

    def switch_once() -> None:
        barrier.wait()
        with Session(engine) as session:
            row = session.get(User, user_id)
            assert row is not None
            switched = switch_tenant_locked(session, row, other_id)
            results.append(switched.tenant_id)

    threads = [threading.Thread(target=switch_once) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20)

    assert results
    assert set(results) == {other_id}
    with Session(engine) as session:
        final = session.get(User, user_id)
        assert final is not None
        assert final.tenant_id == other_id
