"""单进程工作流执行句柄；不保存数据库会话或模型对象。"""

import asyncio
from dataclasses import dataclass, field

from sqlalchemy.engine import Connection, Engine


def database_key(bind: Engine | Connection) -> str:
    """按实际Engine实例绑定，避免同URL的独立内存数据库互相取消。"""
    return str(id(bind.engine))


@dataclass
class ActiveExecution:
    workflow_id: str
    tenant_id: str
    database_key: str
    task: asyncio.Task[tuple[str, str | None, str | None]]
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    reason: str | None = None
    persistence_failed: bool = False

    def stop(self, reason: str) -> None:
        """只向尚未退出的子任务发送一次取消，避免打断其 finally。"""
        if not self.task.done() and self.reason is None:
            self.reason = reason
            self.task.cancel()


_active: dict[tuple[str, str], ActiveExecution] = {}


def register(execution_id: str, entry: ActiveExecution) -> None:
    _active[(entry.database_key, execution_id)] = entry


def finish(execution_id: str, entry: ActiveExecution) -> None:
    _active.pop((entry.database_key, execution_id), None)
    entry.finished.set()


def has_active_workflow(workflow_id: str, tenant_id: str, database_key: str) -> bool:
    return any(e.workflow_id == workflow_id and e.tenant_id == tenant_id
               and e.database_key == database_key for e in _active.values())


async def cancel_execution(execution_id: str, workflow_id: str, tenant_id: str, database_key: str) -> bool:
    """绑定匹配后等待唯一执行所有者完成收尾；无本进程句柄返回 False。"""
    entry = _active.get((database_key, execution_id))
    if (entry is None or entry.workflow_id != workflow_id or entry.tenant_id != tenant_id
            or entry.task.get_loop() is not asyncio.get_running_loop()):
        return False
    entry.stop("WORKFLOW_CANCELLED")
    await entry.finished.wait()
    if entry.persistence_failed:
        raise RuntimeError("工作流终态保存失败")
    return True
