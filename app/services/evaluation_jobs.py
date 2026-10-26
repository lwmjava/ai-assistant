"""Persistent, tenant-scoped lifecycle for zero-cost evaluation jobs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import threading
from collections.abc import Awaitable, Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, func, select

from app.core.config import settings
from app.models.evaluation import (
    EvaluationCaseResult,
    EvaluationDataset,
    EvaluationDatasetVersion,
    EvaluationJob,
)
from app.models.user import User
from app.services.evaluation_adapters import EvaluationAdapterError, builtin_evaluation_adapters
from app.services.evaluation_datasets import canonical_cases_hash
from app.services.evaluation_orchestrator import run_real_evaluation
from app.services.evaluation_quality import EvaluationQualityError, quality_snapshot
from app.services.evaluation_silver import SilverGenerationError, ensure_rag_silver_dataset

_ROOT = Path(__file__).resolve().parents[2]
_REPORT_ROOT = _ROOT / "evals" / "reports"
_RUN_ROOT = _ROOT / "data" / "validation" / "evaluation-jobs"
_IDEMPOTENCY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_TERMINAL = frozenset({"succeeded", "failed", "cancelled", "interrupted"})
_MODES = {"retrieval": "smoke", "generation": "synthetic", "agent": "smoke"}
_DATASETS = {
    "retrieval": _ROOT / "evals" / "datasets" / "rag-v0.1" / "cases.json",
    "generation": _ROOT / "evals" / "datasets" / "rag-v0.1" / "cases.json",
    "agent": _ROOT / "evals" / "datasets" / "agent-chain-v0.1.1" / "cases.json",
}


class EvaluationJobError(Exception):
    """Base job domain error."""


class EvaluationJobAccessError(EvaluationJobError):
    """Missing and inaccessible resources share one public result."""


class EvaluationJobValidationError(EvaluationJobError):
    """The request cannot enter the approved zero-cost lifecycle."""


class EvaluationJobConflictError(EvaluationJobError):
    """Idempotency or terminal-state conflict."""


Runner = Callable[[EvaluationJob, threading.Event], Awaitable[Path]]


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _request_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _registered_input_hash(runner: str) -> str:
    path = _DATASETS[runner]
    try:
        cases = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EvaluationJobValidationError("runner_input_unavailable") from exc
    if not isinstance(cases, list) or not all(isinstance(row, dict) for row in cases):
        raise EvaluationJobValidationError("runner_input_invalid")
    return canonical_cases_hash(cases)


class EvaluationJobService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _visible(self, user: User, row: EvaluationJob | None) -> EvaluationJob:
        if row is None:
            raise EvaluationJobAccessError
        if user.role != "system_admin" and row.tenant_id != user.tenant_id:
            raise EvaluationJobAccessError
        return row

    def create(
        self,
        user: User,
        *,
        dataset_id: str,
        dataset_version_id: str,
        runner: str,
        mode: str,
        idempotency_key: str,
        max_external_calls: int,
    ) -> tuple[EvaluationJob, bool]:
        if runner not in _MODES or mode != _MODES.get(runner):
            raise EvaluationJobValidationError("external_mode_not_authorized")
        if max_external_calls != 0:
            raise EvaluationJobValidationError("external_budget_not_authorized")
        if not _IDEMPOTENCY.fullmatch(idempotency_key):
            raise EvaluationJobValidationError("invalid_idempotency_key")
        dataset = self.session.get(EvaluationDataset, dataset_id)
        version = self.session.get(EvaluationDatasetVersion, dataset_version_id)
        if (
            dataset is None
            or version is None
            or version.dataset_id != dataset.id
            or version.tenant_id != dataset.tenant_id
            or version.status != "approved"
            or dataset.family != runner
            or (user.role != "system_admin" and dataset.tenant_id != user.tenant_id)
        ):
            raise EvaluationJobAccessError
        expected_hash = _registered_input_hash(runner)
        if version.content_hash != expected_hash:
            raise EvaluationJobValidationError("dataset_not_supported_by_runner")
        payload = {
            "dataset_id": dataset.id,
            "dataset_version_id": version.id,
            "runner": runner,
            "mode": mode,
            "max_external_calls": max_external_calls,
        }
        digest = _request_hash(payload)
        existing = self.session.exec(
            select(EvaluationJob).where(
                col(EvaluationJob.tenant_id) == dataset.tenant_id,
                col(EvaluationJob.submitted_by) == user.id,
                col(EvaluationJob.idempotency_key) == idempotency_key,
            )
        ).first()
        if existing is not None:
            if existing.request_hash != digest:
                raise EvaluationJobConflictError("idempotency_mismatch")
            return existing, False
        row = EvaluationJob(
            tenant_id=dataset.tenant_id,
            submitted_by=user.id,
            dataset_id=dataset.id,
            dataset_version_id=version.id,
            runner=runner,
            mode=mode,
            idempotency_key=idempotency_key,
            request_hash=digest,
            input_hash=version.content_hash,
            max_external_calls=0,
        )
        self.session.add(row)
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            existing = self.session.exec(
                select(EvaluationJob).where(
                    col(EvaluationJob.tenant_id) == dataset.tenant_id,
                    col(EvaluationJob.submitted_by) == user.id,
                    col(EvaluationJob.idempotency_key) == idempotency_key,
                )
            ).first()
            if existing is None or existing.request_hash != digest:
                raise EvaluationJobConflictError("idempotency_mismatch") from None
            return existing, False
        self.session.refresh(row)
        return row, True

    def create_real(
        self,
        user: User,
        *,
        tenant_id: str,
        dataset_id: str | None,
        dataset_version_id: str | None,
        target_type: str,
        target_ids: list[str],
        execution_user_id: str | None,
        enable_generation: bool,
        enable_judge: bool,
        idempotency_key: str,
        max_external_calls: int,
        max_cost_yuan: float = 0,
        data_egress_confirmed: bool = False,
    ) -> tuple[EvaluationJob, bool]:
        if user.role != "system_admin":
            raise EvaluationJobAccessError
        if not _IDEMPOTENCY.fullmatch(idempotency_key):
            raise EvaluationJobValidationError("invalid_idempotency_key")
        paid_runtime = target_type in {"agent", "workflow"}
        if max_external_calls and not (enable_generation or enable_judge or paid_runtime):
            raise EvaluationJobValidationError("external_budget_without_paid_stage")
        if (enable_generation or enable_judge or paid_runtime) and max_external_calls < 1:
            raise EvaluationJobValidationError("external_budget_required")
        quality: dict[str, Any] | None = None
        if enable_generation or enable_judge:
            if target_type != "rag":
                raise EvaluationJobValidationError("quality_target_not_supported")
            if not enable_generation:
                raise EvaluationJobValidationError("judge_requires_generation")
            if not data_egress_confirmed:
                raise EvaluationJobValidationError("quality_data_confirmation_required")
            if not 0 < max_cost_yuan <= 5 or not 1 <= max_external_calls <= 30:
                raise EvaluationJobValidationError("quality_budget_invalid")
            try:
                quality = quality_snapshot()
            except EvaluationQualityError as exc:
                raise EvaluationJobValidationError(str(exc)) from None
        try:
            snapshot = builtin_evaluation_adapters.get(target_type).snapshot(
                self.session,
                tenant_id=tenant_id,
                target_ids=target_ids,
                execution_user_id=execution_user_id,
            )
        except (EvaluationAdapterError, LookupError, ValueError) as exc:
            code = str(exc) if str(exc) else "target_unavailable"
            raise EvaluationJobValidationError(code) from exc
        if dataset_id is None or dataset_version_id is None:
            if target_type != "rag" or dataset_id is not None or dataset_version_id is not None:
                raise EvaluationJobValidationError("dataset_version_required")
            try:
                dataset, version = ensure_rag_silver_dataset(
                    self.session,
                    initiator=user,
                    tenant_id=tenant_id,
                    targets=snapshot.targets,
                    max_cases=10 if quality is not None else 50,
                )
            except SilverGenerationError as exc:
                raise EvaluationJobValidationError(str(exc)) from exc
        else:
            dataset_row = self.session.get(EvaluationDataset, dataset_id)
            version_row = self.session.get(EvaluationDatasetVersion, dataset_version_id)
            expected_family = "retrieval" if target_type == "rag" else target_type
            if (
                dataset_row is None
                or version_row is None
                or dataset_row.tenant_id != tenant_id
                or version_row.tenant_id != tenant_id
                or version_row.dataset_id != dataset_row.id
                or version_row.status != "approved"
                or dataset_row.family != expected_family
            ):
                raise EvaluationJobAccessError
            dataset = dataset_row
            version = version_row
        options: dict[str, Any] = {
            "enable_generation": enable_generation,
            "enable_judge": enable_judge,
            "initiator_token_version": user.token_version,
            "initiator_tenant_id": user.tenant_id,
        }
        if quality is not None:
            if not 1 <= version.case_count <= 10:
                raise EvaluationJobValidationError("quality_case_limit")
            if max_external_calls < version.case_count * (3 if enable_judge else 2):
                raise EvaluationJobValidationError("quality_call_budget_insufficient")
            options.update({
                "data_egress_confirmed": True,
                "initiator_token_version": user.token_version,
                "initiator_tenant_id": user.tenant_id,
            })
        budget: dict[str, Any] = {"max_external_calls": max_external_calls}
        if quality is not None:
            budget.update({"max_cost_yuan": max_cost_yuan, "quality": quality})
        snapshot_payload = {
            "target_type": snapshot.target_type,
            "targets": snapshot.targets,
            "execution_principal": snapshot.execution_principal,
        }
        payload = {
            "tenant_id": tenant_id,
            "dataset_id": dataset.id,
            "dataset_version_id": version.id,
            "target_type": target_type,
            "target_ids": target_ids,
            "execution_user_id": execution_user_id,
            "options": options,
            "budget": budget,
        }
        digest = _request_hash(payload)
        existing = self.session.exec(
            select(EvaluationJob).where(
                col(EvaluationJob.tenant_id) == tenant_id,
                col(EvaluationJob.submitted_by) == user.id,
                col(EvaluationJob.idempotency_key) == idempotency_key,
            )
        ).first()
        if existing is not None:
            if existing.request_hash != digest:
                raise EvaluationJobConflictError("idempotency_mismatch")
            return existing, False
        row = EvaluationJob(
            tenant_id=tenant_id,
            submitted_by=user.id,
            dataset_id=dataset.id,
            dataset_version_id=version.id,
            job_kind="real_resource",
            target_type=target_type,
            target_ids_json=json.dumps(target_ids, ensure_ascii=False, separators=(",", ":")),
            target_snapshot_json=json.dumps(
                snapshot_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ),
            adapter_name=snapshot.adapter_name,
            adapter_version=snapshot.adapter_version,
            metric_schema_version=(
                "evaluation-rag-quality-v1" if quality is not None else snapshot.metric_schema_version
            ),
            execution_user_id=execution_user_id,
            execution_principal_json=(
                json.dumps(snapshot.execution_principal, ensure_ascii=False, sort_keys=True)
                if snapshot.execution_principal is not None
                else None
            ),
            options_json=json.dumps(options, sort_keys=True, separators=(",", ":")),
            budget_json=json.dumps(budget, sort_keys=True, separators=(",", ":")),
            runner=("generation" if quality is not None else "retrieval") if target_type == "rag" else target_type,
            mode="real",
            idempotency_key=idempotency_key,
            request_hash=digest,
            input_hash=version.content_hash,
            max_external_calls=max_external_calls,
            case_total=version.case_count,
        )
        self.session.add(row)
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            existing = self.session.exec(
                select(EvaluationJob).where(
                    col(EvaluationJob.tenant_id) == tenant_id,
                    col(EvaluationJob.submitted_by) == user.id,
                    col(EvaluationJob.idempotency_key) == idempotency_key,
                )
            ).first()
            if existing is None or existing.request_hash != digest:
                raise EvaluationJobConflictError("idempotency_mismatch") from None
            return existing, False
        self.session.refresh(row)
        return row, True

    def get(self, user: User, job_id: str) -> EvaluationJob:
        return self._visible(user, self.session.get(EvaluationJob, job_id))

    def list(self, user: User, *, page: int, page_size: int) -> tuple[list[EvaluationJob], int]:
        stmt = select(EvaluationJob)
        count_stmt = select(func.count()).select_from(EvaluationJob)
        if user.role != "system_admin":
            stmt = stmt.where(col(EvaluationJob.tenant_id) == user.tenant_id)
            count_stmt = count_stmt.where(col(EvaluationJob.tenant_id) == user.tenant_id)
        total = int(self.session.exec(count_stmt).one())
        rows = list(
            self.session.exec(
                stmt.order_by(col(EvaluationJob.created_at).desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        )
        return rows, total

    def cancel(self, user: User, job_id: str) -> EvaluationJob:
        row = self.get(user, job_id)
        if user.role not in {"system_admin", "tenant_admin"} and row.submitted_by != user.id:
            raise EvaluationJobAccessError
        if row.status in _TERMINAL:
            raise EvaluationJobConflictError("terminal_job")
        row.cancel_requested_at = _now()
        row.status = "cancelled" if row.status == "queued" else "cancel_requested"
        if row.status == "cancelled":
            row.finished_at = _now()
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def list_case_results(
        self, user: User, job_id: str, *, page: int, page_size: int
    ) -> tuple[Sequence[EvaluationCaseResult], int]:
        job = self.get(user, job_id)
        stmt = select(EvaluationCaseResult).where(
            col(EvaluationCaseResult.job_id) == job.id,
            col(EvaluationCaseResult.tenant_id) == job.tenant_id,
        )
        rows = list(
            self.session.exec(
                stmt.order_by(col(EvaluationCaseResult.created_at), col(EvaluationCaseResult.case_id))
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        )
        total = int(
            self.session.exec(
                select(func.count())
                .select_from(EvaluationCaseResult)
                .where(
                    col(EvaluationCaseResult.job_id) == job.id,
                    col(EvaluationCaseResult.tenant_id) == job.tenant_id,
                )
            ).one()
        )
        return rows, total


def recover_interrupted_jobs(session: Session) -> int:
    rows = list(
        session.exec(
            select(EvaluationJob).where(
                col(EvaluationJob.status).in_(["queued", "running", "cancel_requested"])
            )
        ).all()
    )
    now = _now()
    for row in rows:
        row.status = "interrupted"
        row.error_code = "process_restarted"
        row.finished_at = now
        session.add(row)
    if rows:
        session.commit()
    return len(rows)


def _runner_command(job: EvaluationJob, report: Path, run_dir: Path) -> list[str]:
    python = sys.executable
    if job.runner == "retrieval":
        return [python, str(_ROOT / "scripts/run_rag_baseline.py"), "--mode", "smoke", "--db",
                str(run_dir / "retrieval.db"), "--out", str(report)]
    if job.runner == "generation":
        return [python, str(_ROOT / "scripts/run_rag_generation_eval.py"), "--mode", "synthetic",
                "--db", str(run_dir / "generation.db"), "--out", str(report)]
    return [python, str(_ROOT / "scripts/run_agent_chain_eval.py"), "--mode", "smoke", "--out", str(report)]


async def registered_runner(job: EvaluationJob, cancelled: threading.Event) -> Path:
    run_dir = _RUN_ROOT / job.id
    run_dir.mkdir(parents=True, exist_ok=True)
    _REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    report = _REPORT_ROOT / f"evaluation-job-{job.id}.json"
    runner_env = os.environ.copy()
    runner_env.update(
        {
            "ENV": "development",
            "RAG_VECTOR_STORE": "local",
            "LLM_API_KEY": "",
            "LLM_INTENT_API_KEY": "",
            "LLM_FALLBACK_API_KEY": "",
            "EMBEDDING_API_KEY": "",
            "DASHSCOPE_API_KEY": "",
            "OPENAI_API_KEY": "",
        }
    )
    process = await asyncio.create_subprocess_exec(
        *_runner_command(job, report, run_dir),
        cwd=str(_ROOT),
        env=runner_env,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    started = asyncio.get_running_loop().time()
    while process.returncode is None:
        if cancelled.is_set():
            process.terminate()
            await process.wait()
            raise asyncio.CancelledError
        if asyncio.get_running_loop().time() - started > settings.EVALUATION_JOB_TIMEOUT_SECONDS:
            process.kill()
            await process.wait()
            raise TimeoutError("runner_timeout")
        await asyncio.sleep(0.1)
    if process.returncode != 0:
        raise RuntimeError("runner_failed")
    resolved = report.resolve()
    if not resolved.is_relative_to(_REPORT_ROOT.resolve()) or not resolved.is_file():
        raise RuntimeError("report_missing")
    if resolved.stat().st_size > 16 * 1024 * 1024:
        raise RuntimeError("report_too_large")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise RuntimeError("report_invalid") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("report_invalid")
    return resolved


async def registered_real_runner(
    session: Session, job: EvaluationJob, cancelled: threading.Event
) -> Path:
    report = _REPORT_ROOT / f"evaluation-job-{job.id}.json"
    return await run_real_evaluation(session, job, cancelled, report)


async def execute_job(
    bind: Engine,
    job_id: str,
    cancelled: threading.Event,
    runner: Runner = registered_runner,
) -> None:
    with Session(bind) as session:
        row = session.get(EvaluationJob, job_id)
        if row is None or row.status != "queued":
            return
        row.status = "running"
        row.started_at = _now()
        session.add(row)
        session.commit()
        session.refresh(row)
        try:
            report = (
                await asyncio.wait_for(
                    registered_real_runner(session, row, cancelled),
                    timeout=settings.EVALUATION_JOB_TIMEOUT_SECONDS,
                )
                if row.job_kind == "real_resource" and runner is registered_runner
                else await runner(row, cancelled)
            )
            session.refresh(row)
            if cancelled.is_set() or row.status == "cancel_requested":
                row.status = "cancelled"
                row.error_code = "cancelled"
            else:
                content = report.read_bytes()
                row.status = "succeeded"
                row.report_id = f"job-{row.id}"
                row.report_path = str(report.resolve().relative_to(_ROOT)).replace("\\", "/")
                row.report_hash = hashlib.sha256(content).hexdigest()
                row.error_code = None
        except asyncio.CancelledError:
            row.status = "cancelled"
            row.error_code = "cancelled"
        except TimeoutError:
            row.status = "failed"
            row.error_code = "runner_timeout"
        except (OSError, RuntimeError) as exc:
            row.status = "failed"
            code = str(exc)
            row.error_code = code if re.fullmatch(r"[a-z_]{1,64}", code) else "runner_failed"
        row.finished_at = _now()
        session.add(row)
        session.commit()


class EvaluationJobDispatcher:
    def __init__(self) -> None:
        self._pool: ThreadPoolExecutor | None = None
        self._lock = threading.Lock()
        self._cancel: dict[str, threading.Event] = {}
        self._capacity = 0
        self._stopped = False

    def submit(self, bind: Engine, job_id: str) -> None:
        with self._lock:
            if self._stopped:
                return
            if self._pool is None:
                self._capacity = settings.EVALUATION_JOB_MAX_CONCURRENCY
                self._pool = ThreadPoolExecutor(
                    max_workers=self._capacity,
                    thread_name_prefix="evaluation-job",
                )
            self._dispatch_locked(bind, self._pool)

    def _dispatch_locked(self, bind: Engine, pool: ThreadPoolExecutor) -> None:
        available = self._capacity - len(self._cancel)
        if available <= 0:
            return
        with Session(bind) as session:
            statement = select(EvaluationJob.id).where(col(EvaluationJob.status) == "queued")
            if self._cancel:
                statement = statement.where(col(EvaluationJob.id).not_in(list(self._cancel)))
            job_ids = list(session.exec(
                statement.order_by(col(EvaluationJob.created_at), col(EvaluationJob.id)).limit(available)
            ).all())
        for job_id in job_ids:
            marker = threading.Event()
            self._cancel[job_id] = marker
            try:
                pool.submit(self._run, bind, job_id, marker, pool)
            except RuntimeError:
                self._cancel.pop(job_id, None)
                raise

    def _run(
        self, bind: Engine, job_id: str, marker: threading.Event, pool: ThreadPoolExecutor,
    ) -> None:
        try:
            asyncio.run(execute_job(bind, job_id, marker))
        except Exception as exc:
            logging.getLogger(__name__).error(
                "Evaluation dispatch stopped after worker error: %s", type(exc).__name__,
            )
            self.shutdown()
            raise
        finally:
            with self._lock:
                self._cancel.pop(job_id, None)
                if not self._stopped:
                    self._dispatch_locked(bind, pool)

    def request_cancel(self, job_id: str) -> None:
        with self._lock:
            marker = self._cancel.get(job_id)
            if marker is not None:
                marker.set()

    def shutdown(self) -> None:
        with self._lock:
            self._stopped = True
            for marker in self._cancel.values():
                marker.set()
            pool = self._pool
            self._pool = None
        if pool is not None:
            pool.shutdown(wait=False)


dispatcher = EvaluationJobDispatcher()
