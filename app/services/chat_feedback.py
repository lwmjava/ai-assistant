"""Feedback persistence and ownership, separate from execution scheduling."""

import asyncio
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import TypeVar, cast

from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.agents.pipeline import AgentEvent
from app.models.conversation import Conversation, Message
from app.models.feedback import ChatAttempt, ChatFeedback, MessageRating
from app.models.user import Tenant, User
from app.security.input_filter import InputFilter
from app.security.log_sanitizer import LogSanitizer

T = TypeVar("T")
NO_RESPONSE_SECONDS = 90.0
REASONS = {"off_topic", "factual", "format", "citation", "incomplete", "other"}


class FeedbackAccessError(ValueError):
    """Absent and unauthorized records share the same public error."""


class FeedbackConflictError(ValueError):
    """An execution identity or feedback state is incompatible."""


class FeedbackValidationError(ValueError):
    """An unsupported signal or comment was supplied."""


def clean_comment(comment: str | None) -> str | None:
    if comment is None or not comment.strip():
        return None
    if len(comment) > 500:
        raise FeedbackValidationError("评论最多500字")
    return LogSanitizer().sanitize(InputFilter().filter(comment.strip()).sanitized_text)


class FeedbackService:
    def __init__(self, session: Session):
        self.session = session

    def _principal(self, user: User) -> None:
        expected = (user.id, user.tenant_id, user.role, user.token_version)
        actual = self.session.get(User, user.id, populate_existing=True)
        tenant = self.session.get(Tenant, user.tenant_id, populate_existing=True)
        if (
            actual is None
            or not actual.is_active
            or actual.must_change_password
            or (actual.id, actual.tenant_id, actual.role, actual.token_version) != expected
            or tenant is None
            or not tenant.is_active
        ):
            raise FeedbackAccessError("记录不存在")

    def _owned_conversation(self, user: User, conversation_id: str | None) -> None:
        if conversation_id is None:
            return
        conv = self.session.get(Conversation, conversation_id)
        if conv is None or conv.user_id != user.id or conv.tenant_id != user.tenant_id:
            raise FeedbackAccessError("记录不存在")

    def owned_attempt(self, user: User, attempt_id: str) -> ChatAttempt:
        self._principal(user)
        row = self.session.get(ChatAttempt, attempt_id, populate_existing=True)
        if row is None or row.user_id != user.id or row.tenant_id != user.tenant_id:
            raise FeedbackAccessError("记录不存在")
        self._owned_conversation(user, row.conversation_id)
        return row

    def begin(self, user: User, attempt_id: str | None = None) -> ChatAttempt:
        identity = uuid.UUID(attempt_id).hex if attempt_id else uuid.uuid4().hex
        row = ChatAttempt(id=identity, tenant_id=user.tenant_id, user_id=user.id)
        try:
            self.session.add(row)
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            existing = self.session.get(ChatAttempt, identity)
            if existing is None:
                raise
            if existing.tenant_id != user.tenant_id or existing.user_id != user.id:
                raise FeedbackAccessError("记录不存在") from None
            raise FeedbackConflictError("尝试编号已存在，请查询原尝试") from None
        self.session.info["feedback_attempt_id"] = row.id
        self.session.info.pop("agent_message_id", None)
        self.session.info.pop("agent_assistant_message_id", None)
        return row

    def finish(
        self,
        attempt_id: str,
        outcome: str,
        *,
        conversation_id: str | None = None,
        user_message_id: str | None = None,
        assistant_message_id: str | None = None,
    ) -> None:
        self.session.rollback()
        message = self.session.get(Message, user_message_id) if user_message_id else None
        if message is not None:
            conversation_id = message.conversation_id
        assistant = self.session.get(Message, assistant_message_id) if assistant_message_id else None
        if assistant is not None:
            if assistant.status == "failed":
                outcome = "api_error" if outcome == "success" else outcome
            if assistant.conversation_id != conversation_id:
                assistant_message_id = None
        self.session.execute(
            update(ChatAttempt)
            .where(
                col(ChatAttempt.id) == attempt_id,
                col(ChatAttempt.outcome) == "pending",
            )
            .values(
                outcome=outcome,
                conversation_id=conversation_id,
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
                updated_at=datetime.now(UTC),
            )
        )
        self.session.commit()

    def finish_session(self, attempt_id: str, outcome: str) -> None:
        self.finish(
            attempt_id,
            outcome,
            user_message_id=self.session.info.get("agent_message_id"),
            assistant_message_id=self.session.info.get("agent_assistant_message_id"),
        )

    def submit_failure(self, user: User, attempt_id: str, kind: str, comment: str | None) -> ChatFeedback:
        attempt = self.owned_attempt(user, attempt_id)
        if kind not in {"api_error", "no_response"} or attempt.outcome != kind:
            raise FeedbackConflictError("该尝试不支持此反馈")
        safe = clean_comment(comment)
        row = ChatFeedback(
            tenant_id=user.tenant_id,
            user_id=user.id,
            attempt_id=attempt.id,
            conversation_id=attempt.conversation_id,
            kind=kind,
            comment=safe,
        )
        try:
            self.session.add(row)
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            existing = self.session.exec(
                select(ChatFeedback).where(
                    col(ChatFeedback.user_id) == user.id, col(ChatFeedback.attempt_id) == attempt_id
                )
            ).first()
            if existing is None:
                raise
            return existing
        return row

    def submit_rating(
        self, user: User, message_id: str, rating: str, reason: str | None, comment: str | None
    ) -> MessageRating:
        self._principal(user)
        message = self.session.get(Message, message_id)
        if message is None:
            raise FeedbackAccessError("记录不存在")
        self._owned_conversation(user, message.conversation_id)
        if message.role != "assistant" or message.status != "complete":
            raise FeedbackConflictError("仅可评价成功完成的回复")
        attempt = self.session.exec(
            select(ChatAttempt).where(
                col(ChatAttempt.assistant_message_id) == message_id,
                col(ChatAttempt.user_id) == user.id,
                col(ChatAttempt.tenant_id) == user.tenant_id,
                col(ChatAttempt.outcome) == "success",
            )
        ).first()
        if attempt is None:
            raise FeedbackConflictError("回复缺少成功尝试记录")
        if rating not in {"up", "down"} or (reason is not None and reason not in REASONS):
            raise FeedbackValidationError("评价或原因无效")
        values = {
            "rating": rating,
            "reason_code": (reason or "unspecified") if rating == "down" else None,
            "comment": clean_comment(comment),
            "updated_at": datetime.now(UTC),
        }
        row = self.session.exec(
            select(MessageRating).where(
                col(MessageRating.user_id) == user.id, col(MessageRating.assistant_message_id) == message_id
            )
        ).first()
        if row is None:
            row = MessageRating(
                tenant_id=user.tenant_id,
                user_id=user.id,
                conversation_id=message.conversation_id,
                assistant_message_id=message_id,
                attempt_id=attempt.id,
                rating=rating,
            )
            self.session.add(row)
            try:
                self.session.flush()
            except IntegrityError:
                self.session.rollback()
                row = self.session.exec(
                    select(MessageRating).where(
                        col(MessageRating.user_id) == user.id, col(MessageRating.assistant_message_id) == message_id
                    )
                ).first()
                if row is None:
                    raise
        self.session.execute(update(MessageRating).where(col(MessageRating.id) == row.id).values(**values))
        self.session.commit()
        self.session.refresh(row)
        return row

    def list_records(
        self, user: User, kind: str, *, tenant_id: str | None = None, page: int = 1, page_size: int = 20
    ) -> tuple[list, int]:
        self._principal(user)
        model = ChatFeedback if kind == "failure" else MessageRating
        if user.role == "system_admin":
            if not tenant_id:
                raise FeedbackAccessError("查询必须指定租户")
            conditions = [col(model.tenant_id) == tenant_id]
        else:
            if tenant_id and tenant_id != user.tenant_id:
                raise FeedbackAccessError("记录不存在")
            conditions = [col(model.tenant_id) == user.tenant_id]
            if user.role != "tenant_admin":
                conditions.append(col(model.user_id) == user.id)
        total = self.session.exec(select(func.count()).select_from(model).where(*conditions)).one()
        rows = self.session.exec(
            select(model)
            .where(*conditions)
            .order_by(col(model.created_at).desc(), col(model.id).desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        return list(rows), total

    def delete(self, user: User, kind: str, record_id: str) -> None:
        self._principal(user)
        model = ChatFeedback if kind == "failure" else MessageRating
        row = cast(ChatFeedback | MessageRating | None, self.session.get(model, record_id))
        if row is None or (user.role != "system_admin" and (row.user_id != user.id or row.tenant_id != user.tenant_id)):
            raise FeedbackAccessError("记录不存在")
        self.session.delete(row)
        self.session.commit()

    def message_metadata(self, user: User, conversation: Conversation) -> dict[str, dict]:
        if conversation.user_id != user.id or conversation.tenant_id != user.tenant_id:
            return {}
        attempts = self.session.exec(
            select(ChatAttempt)
            .where(
                col(ChatAttempt.user_id) == user.id,
                col(ChatAttempt.tenant_id) == user.tenant_id,
                col(ChatAttempt.conversation_id) == conversation.id,
            )
            .order_by(col(ChatAttempt.created_at).asc(), col(ChatAttempt.id).asc())
        ).all()
        failures = self.session.exec(
            select(ChatFeedback).where(
                col(ChatFeedback.user_id) == user.id,
                col(ChatFeedback.tenant_id) == user.tenant_id,
                col(ChatFeedback.conversation_id) == conversation.id,
            )
        ).all()
        ratings = self.session.exec(
            select(MessageRating).where(
                col(MessageRating.user_id) == user.id,
                col(MessageRating.tenant_id) == user.tenant_id,
                col(MessageRating.conversation_id) == conversation.id,
            )
        ).all()
        submitted = {row.attempt_id: row.id for row in failures}
        rated = {row.assistant_message_id: row for row in ratings}
        result: dict[str, dict] = {}
        for attempt in attempts:
            for message_id in (attempt.user_message_id, attempt.assistant_message_id):
                if message_id:
                    row = rated.get(message_id)
                    result[message_id] = {
                        "attempt_id": attempt.id,
                        "attempt_outcome": attempt.outcome,
                        "feedback_submitted": attempt.id in submitted,
                        "feedback_id": submitted.get(attempt.id),
                        "rating": row.rating if row else None,
                        "rating_id": row.id if row else None,
                    }
        return result

    async def track_call(self, user: User, operation: Callable[[], Awaitable[T]], attempt_id: str | None = None) -> T:
        attempt = self.begin(user, attempt_id)
        outcome = "api_error"
        ready = asyncio.Event()
        self.session.info["feedback_message_ready"] = ready
        task = asyncio.ensure_future(operation())
        waiting = asyncio.create_task(ready.wait())
        try:
            done, _ = await asyncio.wait({task, waiting}, return_when=asyncio.FIRST_COMPLETED)
            result = await task if task in done else await asyncio.wait_for(task, NO_RESPONSE_SECONDS)
            outcome = "api_error" if getattr(result, "status_code", 200) >= 400 else "success"
            return result
        except TimeoutError:
            outcome = "no_response"
            raise
        except asyncio.CancelledError:
            outcome = "stopped"
            raise
        finally:
            for pending in (waiting, task):
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(waiting, task, return_exceptions=True)
            self.session.info.pop("feedback_message_ready", None)
            self.finish_session(attempt.id, outcome)

    async def track_stream(
        self,
        user: User,
        attempt_id: str,
        stream: AsyncIterator[AgentEvent],
        *,
        no_response_seconds: float = NO_RESPONSE_SECONDS,
        started: bool = False,
    ) -> AsyncGenerator[AgentEvent, None]:
        attempt = self.session.get(ChatAttempt, attempt_id) if started else self.begin(user, attempt_id)
        if attempt is None:
            raise FeedbackAccessError("记录不存在")
        outcome = "api_error"
        deadline: float | None = None
        token = False
        failed = False
        ready = asyncio.Event()
        self.session.info["feedback_message_ready"] = ready
        try:
            while True:
                try:
                    if deadline is None and not token:
                        read = asyncio.ensure_future(anext(stream))
                        waiting = asyncio.create_task(ready.wait())
                        try:
                            completed, _ = await asyncio.wait({read, waiting}, return_when=asyncio.FIRST_COMPLETED)
                            if waiting in completed:
                                deadline = time.monotonic() + no_response_seconds
                            event = (
                                await read if read in completed else await asyncio.wait_for(read, no_response_seconds)
                            )
                        finally:
                            for task in (read, waiting):
                                if not task.done():
                                    task.cancel()
                            await asyncio.gather(read, waiting, return_exceptions=True)
                    elif deadline is not None and not token:
                        event = await asyncio.wait_for(anext(stream), max(0, deadline - time.monotonic()))
                    else:
                        event = await anext(stream)
                except StopAsyncIteration:
                    break
                if event.type == "message" and deadline is None:
                    deadline = time.monotonic() + no_response_seconds
                if event.type == "token":
                    token = True
                if event.type in {"error", "rate_limit"}:
                    failed = True
                if event.type == "done":
                    outcome = "api_error" if failed else "success"
                yield event
        except TimeoutError:
            outcome = "no_response"
            yield AgentEvent("error", "长时间没有输出，请稍后重试", metadata={"code": "no_response"})
        except asyncio.CancelledError:
            # 显式取消才是 stopped。生产循环出错后的 aclose 只会注入 GeneratorExit，
            # 必须保留当前结果，否则服务端失败会被写成取消，不能再提交失败反馈。
            outcome = "stopped"
            raise
        finally:
            self.session.info.pop("feedback_message_ready", None)
            close = getattr(stream, "aclose", None)
            try:
                if close is not None:
                    await close()
            finally:
                self.finish_session(attempt.id, outcome)
