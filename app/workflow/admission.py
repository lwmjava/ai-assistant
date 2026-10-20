"""进程内原子准入；锁内仅访问标量，不访问会话或等待任务。"""

from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from weakref import WeakKeyDictionary

from sqlalchemy.engine import Connection, Engine

from app.core.config import settings


class WorkflowAdmissionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


_lock = Lock()
_count = 0
_active: set[tuple[str, str, str]] = set()
_deleting: set[tuple[str, str, str]] = set()
_consumed: WeakKeyDictionary[Engine, dict[tuple[str, str], datetime]] = WeakKeyDictionary()


@dataclass
class AdmissionLease:
    engine: Engine
    tenant_id: str
    workflow_id: str
    due: datetime | None
    released: bool = False

    def consume(self) -> None:
        if self.due is not None:
            with _lock:
                _consumed.setdefault(self.engine, {})[(self.tenant_id, self.workflow_id)] = self.due

    def release(self) -> None:
        global _count
        with _lock:
            if not self.released:
                self.released = True
                _active.discard((str(id(self.engine)), self.tenant_id, self.workflow_id))
                _count -= 1


def acquire(bind: Engine | Connection, tenant_id: str, workflow_id: str,
            due: datetime | None = None) -> AdmissionLease:
    global _count
    limit = settings.WORKFLOW_MAX_CONCURRENCY
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("工作流并发上限必须为1至1000的整数")
    if due is not None:
        if due.tzinfo is None or due.utcoffset() is None:
            raise ValueError("工作流周期必须带时区")
        due = due.astimezone(UTC)
    db = bind.engine
    key = (str(id(db)), tenant_id, workflow_id)
    with _lock:
        if key in _deleting:
            raise WorkflowAdmissionError("WORKFLOW_DELETING")
        if key in _active:
            raise WorkflowAdmissionError("WORKFLOW_ALREADY_RUNNING")
        previous = _consumed.get(db, {}).get((tenant_id, workflow_id))
        if due is not None and previous is not None and due <= previous:
            raise WorkflowAdmissionError("WORKFLOW_PERIOD_CONSUMED")
        if _count >= limit:
            raise WorkflowAdmissionError("WORKFLOW_CAPACITY_EXCEEDED")
        lease = AdmissionLease(db, tenant_id, workflow_id, due)
        _active.add(key)
        _count += 1
        return lease


def is_active(workflow_id: str, tenant_id: str, database_key: str) -> bool:
    with _lock:
        return (database_key, tenant_id, workflow_id) in _active


def forget_workflow(bind: Engine | Connection, workflow_id: str, tenant_id: str) -> None:
    with _lock:
        slots = _consumed.get(bind.engine)
        if slots is not None:
            slots.pop((tenant_id, workflow_id), None)


@dataclass
class DeletionLease:
    key: tuple[str, str, str]

    def release(self) -> None:
        with _lock:
            _deleting.discard(self.key)


def reserve_deletion(bind: Engine | Connection, workflow_id: str, tenant_id: str) -> DeletionLease:
    key = (str(id(bind.engine)), tenant_id, workflow_id)
    with _lock:
        if key in _active or key in _deleting:
            raise WorkflowAdmissionError("WORKFLOW_ALREADY_RUNNING")
        _deleting.add(key)
        return DeletionLease(key)
