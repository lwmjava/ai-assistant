"""单进程运行所有权与有界 SSE 回放，订阅不拥有生产任务。"""

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlmodel import Session

from app.agents.pipeline import safe_error_metadata
from app.core.config import settings
from app.core.security import Role
from app.models.conversation import Conversation, Message
from app.models.user import Tenant, User

logger = logging.getLogger(__name__)

MAX_EVENTS = 1000
MAX_EVENT_BYTES = 8 * 1024 * 1024
MAX_RUNS = 100
TERMINAL_TTL_SECONDS = 900.0
HEARTBEAT_SECONDS = 15.0


class RunAccessError(ValueError):
    """运行不存在或当前身份无权访问。"""


class RunConflictError(ValueError):
    """运行身份冲突或回放窗口无法满足请求。"""


class RunCapacityError(RuntimeError):
    """活动运行或单事件超出内存上限。"""


@dataclass
class ActiveRun:
    run_id: str
    owner: tuple[str, str, str, int]
    bind: Any = field(repr=False)
    conversation_id: str | None = None
    message_id: str | None = None
    assistant_message_id: str | None = None
    reply: str = ""
    status: str = "running"
    sequence: int = 0
    event_bytes: int = 0
    events: deque[tuple[int, dict[str, str], int]] = field(default_factory=deque, repr=False)
    changed: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    ready: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    task: asyncio.Task | None = field(default=None, repr=False)
    initial_error: BaseException | None = field(default=None, repr=False)
    finished_at: float | None = None
    last_output: float = field(default_factory=time.monotonic)

    def result(self) -> dict:
        return {"run_id": self.run_id, "status": self.status,
                "conversation_id": self.conversation_id, "message_id": self.message_id,
                "assistant_message_id": self.assistant_message_id, "reply": self.reply}


class ActiveRunManager:
    """仅保存短暂回放；长期结果仍由受控对话服务写入消息表。"""

    def __init__(self) -> None:
        self.runs: dict[str, ActiveRun] = {}

    @staticmethod
    def _demo_owner(run: ActiveRun) -> bool:
        return not settings.AUTH_ENABLED and run.owner == (
            "__auth_disabled__", "__auth_disabled__", Role.SYSTEM_ADMIN.value, 0
        )

    def _prune(self) -> None:
        now = time.monotonic()
        for identity, run in list(self.runs.items()):
            if run.finished_at is not None and now - run.finished_at >= TERMINAL_TTL_SECONDS:
                del self.runs[identity]

    def create(self, user: User, bind: Any, producer: Callable, *, run_id: str | None = None,
               conversation_id: str | None = None) -> ActiveRun:
        self._prune()
        identity = str(uuid.UUID(run_id)).replace("-", "") if run_id else uuid.uuid4().hex
        if identity in self.runs:
            self._authorize(self.runs[identity], user)
            raise RunConflictError("运行编号已存在，请查询原运行")
        if len(self.runs) >= MAX_RUNS:
            terminal = [run for run in self.runs.values() if run.finished_at is not None]
            if not terminal:
                raise RunCapacityError("活动运行已满，请稍后重试")
            oldest = min(terminal, key=lambda run: run.finished_at or 0)
            del self.runs[oldest.run_id]
        bind = getattr(bind, "engine", bind)
        run = ActiveRun(identity, (user.id, user.tenant_id, user.role, user.token_version), bind,
                        conversation_id=conversation_id)
        self._authorize(run, user)
        self.runs[identity] = run
        run.task = asyncio.create_task(self._produce(run, producer))
        return run

    def _authorize(self, run: ActiveRun, current: User | None = None) -> None:
        if current is not None and (current.id, current.tenant_id, current.role, current.token_version) != run.owner:
            raise RunAccessError("运行不存在或无权访问")
        if current is not None and (not current.is_active or current.must_change_password):
            raise RunAccessError("运行不存在或无权访问")
        with Session(run.bind) as session:
            if not self._demo_owner(run):
                user = session.get(User, run.owner[0])
                if user is None or not user.is_active or user.must_change_password:
                    raise RunAccessError("运行不存在或无权访问")
                if (user.id, user.tenant_id, user.role, user.token_version) != run.owner:
                    raise RunAccessError("运行不存在或无权访问")
                tenant = session.get(Tenant, user.tenant_id)
                if tenant is None or not tenant.is_active:
                    raise RunAccessError("运行不存在或无权访问")
            if run.conversation_id:
                conv = session.get(Conversation, run.conversation_id)
                if (conv is None or conv.tenant_id != run.owner[1]
                        or (conv.user_id != run.owner[0] and run.owner[2] != Role.SYSTEM_ADMIN.value)):
                    raise RunAccessError("运行不存在或无权访问")

    def get(self, run_id: str, current: User) -> ActiveRun:
        self._prune()
        try:
            identity = uuid.UUID(run_id).hex
        except ValueError:
            raise RunAccessError("运行不存在或无权访问") from None
        run = self.runs.get(identity)
        if run is None:
            raise RunAccessError("运行不存在或无权访问")
        self._authorize(run, current)
        return run

    def emit(self, run: ActiveRun, name: str, fields: dict) -> None:
        if name == "heartbeat":
            fields = {**fields, "ts": time.time()}
        if name == "error":
            fields = {"code": "generation_failed", "message": "运行失败，请稍后重试", "retryable": False, **fields}
        data = json.dumps({**fields, "run_id": run.run_id}, ensure_ascii=False)
        size = len(data.encode("utf-8")) + len(name.encode("utf-8")) + 64
        if size > MAX_EVENT_BYTES:
            raise RunCapacityError("单事件超出回放容量")
        run.sequence += 1
        event = {"id": f"{run.run_id}:{run.sequence}", "event": name, "data": data}
        run.events.append((run.sequence, event, size))
        run.event_bytes += size
        while len(run.events) > MAX_EVENTS or run.event_bytes > MAX_EVENT_BYTES:
            _, _, removed = run.events.popleft()
            run.event_bytes -= removed
        run.last_output = time.monotonic()
        run.changed.set()
        run.changed = asyncio.Event()

    def cursor(self, run: ActiveRun, last_event_id: str | None) -> int:
        sequence = 0
        if last_event_id:
            identity, separator, raw = last_event_id.rpartition(":")
            if not separator or identity != run.run_id or not raw.isdecimal():
                raise RunConflictError("回放位置无效，请加载运行最终结果")
            sequence = int(raw)
        if sequence > run.sequence or (run.events and sequence < run.events[0][0] - 1):
            raise RunConflictError("回放窗口已过期，请加载运行最终结果")
        return sequence

    async def subscribe(self, run: ActiveRun, current: User, after: int) -> AsyncIterator[dict[str, str]]:
        while True:
            self._authorize(run, current)
            wakeup = run.changed
            self.cursor(run, f"{run.run_id}:{after}")
            available = [(number, event) for number, event, _ in run.events if number > after]
            for number, event in available:
                self._authorize(run, current)
                after = number
                yield event
            if run.status != "running" and after >= run.sequence:
                return
            try:
                await asyncio.wait_for(wakeup.wait(), timeout=HEARTBEAT_SECONDS)
            except TimeoutError:
                self._authorize(run, current)

    async def _heartbeat(self, run: ActiveRun) -> None:
        while run.status == "running":
            await asyncio.sleep(HEARTBEAT_SECONDS)
            if time.monotonic() - run.last_output >= HEARTBEAT_SECONDS:
                try:
                    self._authorize(run)
                except Exception:
                    if run.task is not None:
                        run.task.cancel()
                    return
                self.emit(run, "heartbeat", {})

    @staticmethod
    def _fields(name: str, raw: str, metadata: dict | None = None) -> tuple[str, dict]:
        if name in {"stage", "tool", "response", "done", "sources"}:
            return "", {}
        if name == "token":
            return name, {"delta": raw}
        if name in {"conversation", "message"}:
            return name, {name + "_id": raw}
        if name == "skills":
            return name, {"names": json.loads(raw) if raw else []}
        if name == "delegations":
            return name, {"items": json.loads(raw) if raw else []}
        if name == "rate_limit":
            parsed = json.loads(raw) if raw else {}
            retry_after = parsed.get("retry_after_seconds") if isinstance(parsed, dict) else None
            if type(retry_after) is not int or retry_after < 0:
                retry_after = None
            return "error", {"code": "rate_limited", "message": "请求过于频繁，请稍后再试",
                             "retryable": False, "retry_after_seconds": retry_after}
        if name == "error":
            return name, safe_error_metadata(metadata)
        parsed = json.loads(raw) if raw else {}
        return name, parsed if isinstance(parsed, dict) else {"items": parsed}

    async def _produce(self, run: ActiveRun, producer: Callable) -> None:
        heartbeat = asyncio.create_task(self._heartbeat(run))
        session = Session(run.bind)
        stream = None
        failed = False
        rate_limited = False
        try:
            self.emit(run, "run.started", {"status": "running"})
            user = session.get(User, run.owner[0])
            if self._demo_owner(run):
                user = User(id=run.owner[0], tenant_id=run.owner[1], username="auth-disabled",
                            hashed_password="", role=Role.SYSTEM_ADMIN.value, token_version=0)
            if user is None:
                raise RunAccessError("运行不存在或无权访问")
            stream = producer(session, user)
            async for event in stream:
                self._authorize(run)
                if event.type == "conversation":
                    run.conversation_id = event.data
                if event.type == "message":
                    run.message_id = event.data
                if event.type in {"error", "rate_limit"}:
                    failed = True
                if event.type == "error" and rate_limited:
                    continue
                name, fields = self._fields(event.type, event.data, getattr(event, "metadata", None))
                if event.type == "rate_limit":
                    rate_limited = True
                if name:
                    self.emit(run, name, fields)
                run.ready.set()
            run.status = "failed" if failed else "done"
        except asyncio.CancelledError:
            run.status = "cancelled"
        except Exception as exc:
            if not run.ready.is_set():
                run.initial_error = exc.with_traceback(None)
            run.status = "failed"
            # 只留类型。异常原文和堆栈可能带用户输入、查询或密钥。
            logger.error(
                "active_run_failed run_id=%s exception_type=%s",
                run.run_id,
                type(exc).__name__,
            )
            self.emit(run, "error", {"message": "运行失败，请稍后重试"})
        finally:
            try:
                if stream is not None:
                    await stream.aclose()
                message_id = session.info.get("agent_message_id")
                if message_id:
                    session.rollback()
                    message = session.get(Message, message_id)
                    if message is not None and message.role == "user":
                        conversation = session.get(Conversation, message.conversation_id)
                        if (conversation is not None and conversation.tenant_id == run.owner[1]
                                and (conversation.user_id == run.owner[0] or run.owner[2] == Role.SYSTEM_ADMIN.value)):
                            run.message_id = message.id
                            run.conversation_id = message.conversation_id
                assistant_id = session.info.get("agent_assistant_message_id")
                if assistant_id:
                    session.rollback()
                    assistant = session.get(Message, assistant_id)
                    if assistant is not None and assistant.conversation_id == run.conversation_id:
                        run.reply = assistant.content
                        run.assistant_message_id = assistant.id
                        if assistant.status == "failed":
                            run.status = "failed"
            except BaseException as exc:
                run.status = "failed"
                logger.error(
                    "active_run_result_read_failed run_id=%s exception_type=%s",
                    run.run_id,
                    type(exc).__name__,
                )
            finally:
                session.close()
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
                try:
                    self.emit(run, "run.finished", {"status": run.status,
                              "conversation_id": run.conversation_id, "message_id": run.message_id})
                finally:
                    run.finished_at = time.monotonic()
                    run.ready.set()

    async def cancel(self, run: ActiveRun, current: User) -> None:
        self._authorize(run, current)
        if run.task is not None and not run.task.done():
            run.task.cancel()
            await asyncio.gather(run.task, return_exceptions=True)
            if run.status == "running":
                run.status = "cancelled"
                run.finished_at = time.monotonic()
                self.emit(run, "run.finished", {"status": "cancelled"})
                run.ready.set()

    async def shutdown(self) -> None:
        tasks = [run.task for run in self.runs.values() if run.task is not None and not run.task.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.runs.clear()


active_runs = ActiveRunManager()
