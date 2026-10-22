"""Feedback ownership, privacy and durable attempt contracts."""

import asyncio
import uuid

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.conversation import Conversation, Message
from app.models.feedback import ChatAttempt, ChatFeedback, MessageRating
from app.models.user import Tenant, User
from app.services.chat_feedback import FeedbackAccessError, FeedbackConflictError, FeedbackService


@pytest.fixture
def data():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        users = [
            User(id=name, tenant_id=tenant, username=name, hashed_password="", role=role)
            for name, tenant, role in [
                ("owner", "a", "member"),
                ("other", "a", "member"),
                ("admin", "a", "system_admin"),
                ("outsider", "b", "member"),
            ]
        ]
        session.add_all([Tenant(id="a", name="a"), Tenant(id="b", name="b"), *users])
        session.add(Conversation(id="conv", tenant_id="a", user_id="owner"))
        session.add(Message(id="question", conversation_id="conv", role="user", content="private question"))
        session.add(Message(id="answer", conversation_id="conv", role="assistant", content="private answer"))
        session.commit()
        service = FeedbackService(session)
        attempt = service.begin(users[0], uuid.uuid4().hex)
        service.finish(attempt.id, "api_error", conversation_id="conv", user_message_id="question")
        yield service, users, attempt.id
    engine.dispose()


def test_failure_idempotency_and_redaction(data):
    service, users, attempt = data
    row = service.submit_failure(
        users[0], attempt, "api_error", "电话13800138000 email a@example.com token=secret-value"
    )
    again = service.submit_failure(users[0], attempt, "api_error", "changed")
    assert row.id == again.id
    assert row.comment == again.comment
    assert "13800138000" not in row.comment and "a@example.com" not in row.comment
    assert "secret-value" not in row.comment
    assert len(service.session.exec(select(ChatFeedback)).all()) == 1


@pytest.mark.parametrize("index", [1, 2, 3])
def test_even_administrator_cannot_submit_for_another_owner(data, index):
    service, users, attempt = data
    with pytest.raises(FeedbackAccessError):
        service.submit_failure(users[index], attempt, "api_error", "comment")


@pytest.mark.parametrize("outcome", ["pending", "success", "stopped"])
def test_non_failure_attempt_cannot_be_reported(data, outcome):
    service, users, _ = data
    attempt = service.begin(users[0], uuid.uuid4().hex)
    if outcome != "pending":
        service.finish(attempt.id, outcome)
    with pytest.raises(FeedbackConflictError):
        service.submit_failure(users[0], attempt.id, "api_error", None)


def test_duplicate_attempt_never_reexecutes_and_terminal_is_immutable(data):
    service, users, attempt = data
    with pytest.raises(FeedbackConflictError):
        service.begin(users[0], attempt)
    service.finish(attempt, "success")
    assert service.session.get(ChatAttempt, attempt).outcome == "api_error"


def test_rating_requires_success_and_updates_single_row(data):
    service, users, _ = data
    attempt = service.begin(users[0], uuid.uuid4().hex)
    service.finish(
        attempt.id, "success", conversation_id="conv", user_message_id="question", assistant_message_id="answer"
    )
    up = service.submit_rating(users[0], "answer", "up", None, None)
    down = service.submit_rating(users[0], "answer", "down", None, "changed")
    assert up.id == down.id and down.rating == "down" and down.reason_code == "unspecified"
    assert len(service.session.exec(select(MessageRating)).all()) == 1
    with pytest.raises(FeedbackAccessError):
        service.submit_rating(users[2], "answer", "up", None, None)
    message = service.session.get(Message, "answer")
    message.status = "stopped"
    service.session.add(message)
    service.session.commit()
    with pytest.raises(FeedbackConflictError):
        service.submit_rating(users[0], "answer", "up", None, None)


def test_list_and_delete_obey_owner_and_tenant(data):
    service, users, attempt = data
    row = service.submit_failure(users[0], attempt, "api_error", "comment")
    assert service.list_records(users[1], "failure")[1] == 0
    assert service.list_records(users[3], "failure")[1] == 0
    with pytest.raises(FeedbackAccessError):
        service.list_records(users[2], "failure")
    assert service.list_records(users[2], "failure", tenant_id="a")[1] == 1
    with pytest.raises(FeedbackAccessError):
        service.delete(users[1], "failure", row.id)
    service.delete(users[0], "failure", row.id)
    assert service.list_records(users[0], "failure")[1] == 0


def test_revoked_identity_is_rejected(data):
    service, users, attempt = data
    users[0].is_active = False
    service.session.add(users[0])
    service.session.commit()
    with pytest.raises(FeedbackAccessError):
        service.submit_failure(users[0], attempt, "api_error", None)


async def test_stream_timeout_is_failure_and_disconnect_does_not_own_it(data):
    from app.agents.pipeline import AgentEvent

    service, users, _ = data
    attempt_id = uuid.uuid4().hex

    async def producer():
        service.session.info["agent_message_id"] = "question"
        yield AgentEvent("message", "question")
        await asyncio.sleep(1)

    output = [event async for event in service.track_stream(users[0], attempt_id, producer(), no_response_seconds=0.01)]
    assert any(e.type == "error" for e in output)
    assert service.session.get(ChatAttempt, attempt_id).outcome == "no_response"


async def test_stream_success_binds_messages(data):
    from app.agents.pipeline import AgentEvent

    service, users, _ = data
    attempt_id = uuid.uuid4().hex

    async def producer():
        service.session.info["agent_message_id"] = "question"
        yield AgentEvent("message", "question")
        yield AgentEvent("token", "answer")
        service.session.info["agent_assistant_message_id"] = "answer"
        yield AgentEvent("done", "answer")

    _ = [event async for event in service.track_stream(users[0], attempt_id, producer())]
    row = service.session.get(ChatAttempt, attempt_id)
    assert row.outcome == "success" and row.assistant_message_id == "answer"


async def test_consumer_failure_close_stays_api_error(data):
    from app.agents.pipeline import AgentEvent

    service, users, _ = data
    attempt_id = uuid.uuid4().hex

    async def producer():
        service.session.info["agent_message_id"] = "question"
        yield AgentEvent("token", "partial")
        await asyncio.sleep(30)

    stream = service.track_stream(users[0], attempt_id, producer())
    with pytest.raises(RuntimeError):
        async for _event in stream:
            raise RuntimeError("producer loop failed")
    await stream.aclose()
    row = service.session.get(ChatAttempt, attempt_id)
    assert row.outcome == "api_error"
    service.submit_failure(users[0], attempt_id, "api_error", None)


async def test_cancellation_before_output_is_stopped(data):
    service, users, _ = data
    identity = uuid.uuid4().hex
    entered = asyncio.Event()

    async def producer():
        service.session.info["agent_message_id"] = "question"
        service.session.info["feedback_message_ready"].set()
        entered.set()
        await asyncio.sleep(10)
        yield

    async def consume():
        return [event async for event in service.track_stream(users[0], identity, producer())]

    task = asyncio.create_task(consume())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    row = service.session.get(ChatAttempt, identity)
    assert row.outcome == "stopped" and row.assistant_message_id is None
    with pytest.raises(FeedbackConflictError):
        service.submit_failure(users[0], identity, "no_response", None)


async def test_budget_starts_at_persisted_message_before_first_event(data):
    service, users, _ = data
    identity = uuid.uuid4().hex

    async def producer():
        service.session.info["agent_message_id"] = "question"
        service.session.info["feedback_message_ready"].set()
        await asyncio.sleep(10)
        yield

    events = [event async for event in service.track_stream(users[0], identity, producer(), no_response_seconds=0.01)]
    assert len(events) == 1 and events[0].type == "error"
    assert service.session.get(ChatAttempt, identity).outcome == "no_response"


def test_concurrent_failure_submissions_are_one_durable_record(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    engine = create_engine(f"sqlite:///{tmp_path / 'concurrent.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    identity = uuid.uuid4().hex
    with Session(engine) as session:
        user = User(id="owner", tenant_id="tenant", username="owner", hashed_password="", role="member")
        session.add_all([Tenant(id="tenant", name="tenant"), user])
        session.commit()
        service = FeedbackService(session)
        service.begin(user, identity)
        service.finish(identity, "api_error")
    barrier = Barrier(4)

    def submit(index):
        with Session(engine) as session:
            user = session.get(User, "owner")
            barrier.wait()
            row = FeedbackService(session).submit_failure(user, identity, "api_error", f"comment{index}")
            return row.id, row.comment

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(submit, range(4)))
    assert len(set(results)) == 1
    with Session(engine) as session:
        assert len(session.exec(select(ChatFeedback)).all()) == 1
    engine.dispose()
