"""Persistent evaluation jobs: authorization, budget, idempotency, and terminal states."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, col, create_engine, select

from app.api.routes import evaluation as evaluation_routes
from app.core.database import get_session
from app.core.security import create_access_token
from app.main import app
from app.models.evaluation import EvaluationDataset, EvaluationDatasetVersion, EvaluationJob
from app.models.user import Tenant, User
from app.services import evaluation_jobs
from app.services.evaluation_datasets import canonical_cases_hash
from app.services.evaluation_jobs import (
    EvaluationJobAccessError,
    EvaluationJobConflictError,
    EvaluationJobDispatcher,
    EvaluationJobService,
    EvaluationJobValidationError,
    execute_job,
    recover_interrupted_jobs,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def jobs_ctx(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    cases = json.loads((ROOT / "evals/datasets/rag-v0.1/cases.json").read_text(encoding="utf-8"))
    digest = canonical_cases_hash(cases)
    with Session(engine) as session:
        session.add(Tenant(id="job-a", name="A"))
        session.add(Tenant(id="job-b", name="B"))
        session.add(User(id="owner-a", tenant_id="job-a", username="owner-a", hashed_password="",
                         role="system_admin"))
        session.add(User(id="owner-b", tenant_id="job-b", username="owner-b", hashed_password="",
                         role="tenant_admin"))
        session.add(EvaluationDataset(id="ds-a", tenant_id="job-a", name="RAG", slug="rag-a",
                                      family="retrieval", created_by="owner-a"))
        session.add(EvaluationDatasetVersion(
            id="ver-a", dataset_id="ds-a", tenant_id="job-a", version="1.0.0",
            status="approved", source="upload", content_hash=digest,
            cases_json=json.dumps(cases), case_count=len(cases), created_by="owner-a",
        ))
        session.add(EvaluationDatasetVersion(
            id="ver-a2", dataset_id="ds-a", tenant_id="job-a", version="1.0.1",
            status="approved", source="upload", content_hash=digest,
            cases_json=json.dumps(cases), case_count=len(cases), created_by="owner-a",
        ))
        session.commit()

    def database():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = database
    monkeypatch.setattr(evaluation_routes.dispatcher, "submit", lambda bind, job_id: None)
    headers = {
        "a": {"Authorization": f"Bearer {create_access_token('owner-a', 'job-a', 'system_admin')}"},
        "b": {"Authorization": f"Bearer {create_access_token('owner-b', 'job-b', 'tenant_admin')}"},
    }
    yield TestClient(app), engine, headers
    app.dependency_overrides.pop(get_session, None)
    engine.dispose()


def _create(service: EvaluationJobService, user: User, **overrides):
    payload = {
        "dataset_id": "ds-a",
        "dataset_version_id": "ver-a",
        "runner": "retrieval",
        "mode": "smoke",
        "idempotency_key": "stable-key",
        "max_external_calls": 0,
    }
    payload.update(overrides)
    return service.create(user, **payload)


def test_service_idempotency_budget_and_tenant_boundary(jobs_ctx):
    _, engine, _ = jobs_ctx
    with Session(engine) as session:
        owner = session.get(User, "owner-a")
        other = session.get(User, "owner-b")
        service = EvaluationJobService(session)
        first, created = _create(service, owner)
        duplicate, duplicate_created = _create(service, owner)
        assert created is True
        assert duplicate_created is False
        assert duplicate.id == first.id
        with pytest.raises(EvaluationJobConflictError):
            _create(service, owner, dataset_version_id="ver-a2")
        with pytest.raises(EvaluationJobValidationError):
            _create(service, owner, idempotency_key="paid", max_external_calls=1)
        with pytest.raises(EvaluationJobValidationError):
            _create(service, owner, idempotency_key="official", mode="synthetic")
        with pytest.raises(EvaluationJobAccessError):
            _create(service, other, idempotency_key="cross-tenant")


def test_api_create_list_cancel_and_non_disclosure(jobs_ctx):
    client, _, headers = jobs_ctx
    payload = {
        "dataset_id": "ds-a", "dataset_version_id": "ver-a", "runner": "retrieval",
        "mode": "smoke", "idempotency_key": "api-key", "max_external_calls": 0,
    }
    created = client.post("/api/v1/evaluations/jobs", headers=headers["a"], json=payload)
    assert created.status_code == 202, created.text
    job_id = created.json()["id"]
    assert created.json()["status"] == "queued"
    duplicate = client.post("/api/v1/evaluations/jobs", headers=headers["a"], json=payload)
    assert duplicate.status_code == 202
    assert duplicate.json()["id"] == job_id
    assert client.get("/api/v1/evaluations/jobs", headers=headers["a"]).json()["total"] == 1
    hidden = client.get(f"/api/v1/evaluations/jobs/{job_id}", headers=headers["b"])
    missing = client.get("/api/v1/evaluations/jobs/missing", headers=headers["b"])
    assert hidden.status_code == missing.status_code == 403
    cancelled = client.post(f"/api/v1/evaluations/jobs/{job_id}/cancel", headers=headers["a"])
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert client.post(f"/api/v1/evaluations/jobs/{job_id}/cancel", headers=headers["a"]).status_code == 409


def test_execute_success_failure_cancel_and_restart(jobs_ctx, tmp_path: Path):
    _, engine, _ = jobs_ctx
    ids: list[str] = []
    with Session(engine) as session:
        owner = session.get(User, "owner-a")
        service = EvaluationJobService(session)
        for key in ("success", "failure", "cancel", "restart"):
            row, _ = _create(service, owner, idempotency_key=key)
            ids.append(row.id)

    async def success(job: EvaluationJob, marker: threading.Event) -> Path:
        if marker.is_set():
            raise asyncio.CancelledError
        path = tmp_path / f"{job.id}.json"
        path.write_text('{"status":"unmeasured"}', encoding="utf-8")
        return path

    async def failure(job: EvaluationJob, marker: threading.Event) -> Path:
        raise RuntimeError("runner_failed")

    asyncio.run(execute_job(engine, ids[0], threading.Event(), success))
    asyncio.run(execute_job(engine, ids[1], threading.Event(), failure))
    marker = threading.Event()
    marker.set()
    asyncio.run(execute_job(engine, ids[2], marker, success))
    with Session(engine) as session:
        assert session.get(EvaluationJob, ids[0]).status == "succeeded"
        assert session.get(EvaluationJob, ids[0]).report_hash
        assert session.get(EvaluationJob, ids[1]).error_code == "runner_failed"
        assert session.get(EvaluationJob, ids[2]).status == "cancelled"
        assert recover_interrupted_jobs(session) == 1
        assert session.get(EvaluationJob, ids[3]).status == "interrupted"
        assert session.get(EvaluationJob, ids[3]).error_code == "process_restarted"


@pytest.mark.parametrize("capacity", [1, 2])
def test_dispatcher_keeps_overflow_persistent_and_drains_after_failure(
    dispatcher_engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capacity: int,
):
    engine = dispatcher_engine
    monkeypatch.setattr(evaluation_jobs.settings, "EVALUATION_JOB_MAX_CONCURRENCY", capacity)
    monkeypatch.setattr(evaluation_jobs, "_ROOT", tmp_path)
    ids = []
    with Session(engine) as session:
        owner = session.get(User, "owner-a")
        for index in range(8):
            job, _ = _create(EvaluationJobService(session), owner, idempotency_key=f"queue-{index}")
            ids.append(job.id)
        ids = list(session.exec(
            select(EvaluationJob.id).order_by(col(EvaluationJob.created_at), col(EvaluationJob.id))
        ).all())
    started = {job_id: threading.Event() for job_id in ids}
    finished = {job_id: threading.Event() for job_id in ids}
    release = threading.Event()
    original_execute = execute_job

    async def runner(job, marker):
        started[job.id].set()
        assert release.wait(10), "worker was not released"
        if job.id == ids[0]:
            raise RuntimeError("runner_failed")
        report = tmp_path / f"{job.id}.json"
        report.write_text("{}", encoding="utf-8")
        return report

    async def execute(bind, job_id, marker):
        try:
            await original_execute(bind, job_id, marker, runner)
        finally:
            finished[job_id].set()

    monkeypatch.setattr(evaluation_jobs, "execute_job", execute)
    scheduler = EvaluationJobDispatcher()
    pool = None
    try:
        scheduler.submit(engine, ids[0])
        pool = scheduler._pool
        for job_id in ids:
            scheduler.submit(engine, job_id)
        assert all(started[job_id].wait(5) for job_id in ids[:capacity])
        with scheduler._lock:
            assert len(scheduler._cancel) == capacity
        with Session(engine) as session:
            assert all(session.get(EvaluationJob, job_id).status == "queued" for job_id in ids[capacity:])
            owner = session.get(User, "owner-a")
            EvaluationJobService(session).cancel(owner, ids[-2])
        scheduler.request_cancel(ids[-2])
        release.set()
        assert all(finished[job_id].wait(10) for job_id in ids if job_id != ids[-2]), "persistent backlog did not drain"
        with Session(engine) as session:
            assert session.get(EvaluationJob, ids[0]).status == "failed"
            assert session.get(EvaluationJob, ids[-2]).status == "cancelled"
            assert not started[ids[-2]].is_set()
            assert all(session.get(EvaluationJob, job_id).status == "succeeded" for job_id in ids[1:-2])
            assert session.get(EvaluationJob, ids[-1]).status == "succeeded"
    finally:
        release.set()
        scheduler.shutdown()
        if pool is not None:
            pool.shutdown(wait=True)


@pytest.fixture
def dispatcher_engine(jobs_ctx, tmp_path: Path):
    _, source, _ = jobs_ctx
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'dispatcher.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(source) as original, Session(engine) as copied:
        for model in (Tenant, User, EvaluationDataset, EvaluationDatasetVersion):
            for row in original.exec(select(model)).all():
                copied.add(model.model_validate(row.model_dump()))
        copied.commit()
    yield engine
    engine.dispose()


def test_dispatcher_shutdown_cancels_active_without_claiming_backlog(
    dispatcher_engine, monkeypatch: pytest.MonkeyPatch,
):
    engine = dispatcher_engine
    monkeypatch.setattr(evaluation_jobs.settings, "EVALUATION_JOB_MAX_CONCURRENCY", 1)
    with Session(engine) as session:
        owner = session.get(User, "owner-a")
        first, _ = _create(EvaluationJobService(session), owner, idempotency_key="active")
        second, _ = _create(EvaluationJobService(session), owner, idempotency_key="waiting")
        first, second = sorted((first, second), key=lambda row: (row.created_at, row.id))
        first_id, second_id = first.id, second.id
    started, finished = threading.Event(), threading.Event()

    async def runner(job, marker):
        started.set()
        assert marker.wait(5), "shutdown did not signal cancellation"
        raise asyncio.CancelledError

    async def execute(bind, job_id, marker):
        try:
            await execute_job(bind, job_id, marker, runner)
        finally:
            finished.set()

    monkeypatch.setattr(evaluation_jobs, "execute_job", execute)
    scheduler = EvaluationJobDispatcher()
    pool = None
    try:
        scheduler.submit(engine, first_id)
        pool = scheduler._pool
        assert started.wait(5)
        scheduler.shutdown()
        assert finished.wait(5)
        scheduler.submit(engine, second_id)
        with Session(engine) as session:
            assert session.get(EvaluationJob, first_id).status == "cancelled"
            assert session.get(EvaluationJob, second_id).status == "queued"
            assert recover_interrupted_jobs(session) == 1
            assert session.get(EvaluationJob, second_id).status == "interrupted"
    finally:
        scheduler.shutdown()
        if pool is not None:
            pool.shutdown(wait=True)


def test_dispatcher_unexpected_prestart_error_stops_without_retry(
    dispatcher_engine, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
):
    engine = dispatcher_engine
    monkeypatch.setattr(evaluation_jobs.settings, "EVALUATION_JOB_MAX_CONCURRENCY", 1)
    with Session(engine) as session:
        owner = session.get(User, "owner-a")
        first, _ = _create(EvaluationJobService(session), owner, idempotency_key="broken")
        second, _ = _create(EvaluationJobService(session), owner, idempotency_key="backlog")
        first, second = sorted((first, second), key=lambda row: (row.created_at, row.id))
        first_id, second_id = first.id, second.id
    calls = []
    finished = threading.Event()

    async def broken(bind, job_id, marker):
        calls.append(job_id)
        raise RuntimeError("private database details")

    scheduler = EvaluationJobDispatcher()
    original_run = scheduler._run

    def run(*args):
        try:
            original_run(*args)
        finally:
            finished.set()

    monkeypatch.setattr(evaluation_jobs, "execute_job", broken)
    monkeypatch.setattr(scheduler, "_run", run)
    try:
        scheduler.submit(engine, first_id)
        assert finished.wait(5)
        scheduler.submit(engine, first_id)
        scheduler.submit(engine, second_id)
        assert calls == [first_id]
        assert not scheduler._cancel
        assert "RuntimeError" in caplog.text
        assert "private database details" not in caplog.text
        with Session(engine) as session:
            assert session.get(EvaluationJob, first_id).status == "queued"
            assert session.get(EvaluationJob, second_id).status == "queued"
            assert recover_interrupted_jobs(session) == 2
    finally:
        scheduler.shutdown()
