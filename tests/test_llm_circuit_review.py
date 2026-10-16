"""Independent offline circuit lifecycle checks through fresh factory clients."""

import asyncio
import logging
from types import SimpleNamespace

import httpx
import pytest

from app.core.config import settings
from app.llm import factory, routing
from app.llm.base import ChatMessage, ChatRole
from app.llm.budget import ContextBudgetError
from app.llm.openai_compatible import OpenAICompatibleProvider

MESSAGES = [ChatMessage(role=ChatRole.USER, content="synthetic review request")]


class OfflineProvider(OpenAICompatibleProvider):
    def __init__(self, model):
        super().__init__("https://review.invalid/" + model, "review-credential-a", model)
        self.calls = 0
        self.stream_calls = 0
        self.error = None
        self.handler = None
        self.stream_gate = None
        self.stream_entered = None
        self.fail_after_token = False

    async def chat(self, messages, options=None):
        self.calls += 1
        if self.handler is not None:
            return await self.handler(self.calls)
        if self.error is not None:
            raise self.error
        return self.model

    async def stream_chat(self, messages, options=None):
        self.stream_calls += 1
        if self.stream_entered is not None:
            self.stream_entered.set()
        if self.stream_gate is not None:
            await self.stream_gate.wait()
        if self.error is not None:
            raise self.error
        yield self.model
        if self.fail_after_token:
            raise httpx.ReadTimeout("review-private-provider-detail")


@pytest.fixture
def circuit(monkeypatch):
    factory.set_llm_provider_override(None)
    routing.reset_circuits()
    now = [1000.0]
    # Replace the routing module's clock, not asyncio's shared stdlib clock.
    monkeypatch.setattr(routing, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(settings, "LLM_CIRCUIT_FAILURE_THRESHOLD", 3)
    monkeypatch.setattr(settings, "LLM_CIRCUIT_COOLDOWN_SECONDS", 30.0)
    primary = OfflineProvider("primary")
    secondary = OfflineProvider("secondary")
    monkeypatch.setattr(factory, "_real_hops", lambda task: [("chat", primary), ("fallback", secondary)])
    yield primary, secondary, now
    routing.reset_circuits()
    factory.set_llm_provider_override(None)


async def chat():
    return await factory.get_llm_provider("chat").chat(MESSAGES)


async def trip(primary):
    primary.error = httpx.ConnectError("review-private-provider-detail")
    for _ in range(3):
        assert await chat() == "secondary"
    assert primary.calls == 3


@pytest.mark.asyncio
async def test_new_factory_instances_share_open_circuit_and_only_one_cooled_probe(circuit):
    primary, secondary, now = circuit
    await trip(primary)
    assert await chat() == "secondary"
    assert primary.calls == 3
    now[0] += 31
    entered, release = asyncio.Event(), asyncio.Event()

    async def probe(_):
        entered.set()
        await release.wait()
        return "primary"

    primary.handler = probe
    pending = asyncio.create_task(chat())
    await asyncio.wait_for(entered.wait(), 1)
    assert await chat() == "secondary"
    assert primary.calls == 4
    release.set()
    assert await pending == "primary"
    primary.handler = None
    primary.error = None
    assert await chat() == "primary"
    assert secondary.calls == 5


@pytest.mark.asyncio
async def test_half_open_chat_cancel_releases_probe_without_counting_failure(circuit):
    primary, _, now = circuit
    await trip(primary)
    now[0] += 31
    entered = asyncio.Event()

    async def blocked(_):
        entered.set()
        await asyncio.Event().wait()

    primary.handler = blocked
    pending = asyncio.create_task(chat())
    await asyncio.wait_for(entered.wait(), 1)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    primary.handler = None
    primary.error = None
    assert await chat() == "primary"


@pytest.mark.asyncio
async def test_half_open_stream_first_token_is_not_success_and_aclose_releases(circuit):
    primary, _, now = circuit
    await trip(primary)
    primary.error = None
    now[0] += 31
    stream = factory.get_llm_provider().stream_chat(MESSAGES)
    assert await anext(stream) == "primary"
    assert await chat() == "secondary"
    await stream.aclose()
    replacement = factory.get_llm_provider().stream_chat(MESSAGES)
    assert await anext(replacement) == "primary"
    assert await chat() == "secondary"
    await replacement.aclose()


@pytest.mark.asyncio
async def test_half_open_stream_cancel_before_token_releases_probe(circuit):
    primary, _, now = circuit
    await trip(primary)
    primary.error = None
    primary.stream_entered = asyncio.Event()
    primary.stream_gate = asyncio.Event()
    now[0] += 31
    stream = factory.get_llm_provider().stream_chat(MESSAGES)
    pending = asyncio.create_task(anext(stream))
    await asyncio.wait_for(primary.stream_entered.wait(), 1)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    await stream.aclose()
    primary.stream_gate = None
    assert await chat() == "primary"


@pytest.mark.asyncio
async def test_old_success_cannot_close_circuit_opened_by_newer_requests(circuit):
    primary, _, _ = circuit
    entered, release = asyncio.Event(), asyncio.Event()

    async def request(number):
        if number == 1:
            entered.set()
            await release.wait()
            return "old-success"
        raise httpx.ConnectError("review-private-provider-detail")

    primary.handler = request
    old = asyncio.create_task(chat())
    await asyncio.wait_for(entered.wait(), 1)
    for _ in range(3):
        assert await chat() == "secondary"
    release.set()
    assert await old == "old-success"
    assert await chat() == "secondary"
    assert primary.calls == 4


@pytest.mark.asyncio
async def test_old_failure_cannot_poison_new_successful_probe_epoch(circuit):
    primary, _, now = circuit
    entered, release = asyncio.Event(), asyncio.Event()
    healthy = [False]

    async def request(number):
        if number == 1:
            entered.set()
            await release.wait()
            raise httpx.ConnectError("old-private-failure")
        if healthy[0]:
            return "primary"
        raise httpx.ConnectError("current-private-failure")

    primary.handler = request
    old = asyncio.create_task(chat())
    await asyncio.wait_for(entered.wait(), 1)
    for _ in range(3):
        assert await chat() == "secondary"
    now[0] += 31
    healthy[0] = True
    assert await chat() == "primary"
    release.set()
    assert await old == "secondary"
    healthy[0] = False
    for _ in range(2):
        assert await chat() == "secondary"
    healthy[0] = True
    assert await chat() == "primary"


def http_error(status):
    request = httpx.Request("POST", "https://review.invalid/chat/completions")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError("review-private-provider-detail", request=request, response=response)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    http_error(400),
    ContextBudgetError("capability_unverified", payload_tokens=1, reserved_output=1, margin=1, limit=1),
])
async def test_parameter_and_budget_errors_neither_fallback_nor_open_circuit(circuit, error):
    primary, secondary, _ = circuit
    primary.error = error
    for _ in range(3):
        with pytest.raises(type(error)):
            await chat()
    assert secondary.calls == 0
    primary.error = None
    assert await chat() == "primary"


@pytest.mark.asyncio
async def test_credential_rejections_keep_fallback_but_do_not_trip_transient_circuit(circuit):
    primary, _, _ = circuit
    primary.error = http_error(401)
    for _ in range(3):
        assert await chat() == "secondary"
    primary.error = None
    assert await chat() == "primary"


@pytest.mark.asyncio
async def test_stream_failure_after_token_never_splices_fallback(circuit):
    primary, secondary, _ = circuit
    primary.fail_after_token = True
    stream = factory.get_llm_provider().stream_chat(MESSAGES)
    assert await anext(stream) == "primary"
    with pytest.raises(routing.LLMUnavailableError):
        await anext(stream)
    assert secondary.stream_calls == secondary.calls == 0


@pytest.mark.asyncio
async def test_single_hop_is_protected_and_credential_change_isolated(monkeypatch, circuit, caplog):
    primary, _, _ = circuit
    monkeypatch.setattr(factory, "_real_hops", lambda task: [("chat", primary)])
    primary.error = httpx.ConnectError("review-private-provider-detail")
    caplog.set_level(logging.WARNING)
    for _ in range(4):
        with pytest.raises(routing.LLMUnavailableError):
            await chat()
    assert primary.calls == 3
    primary.api_key = "review-credential-b"
    primary.error = None
    provider = factory.get_llm_provider()
    assert await provider.chat(MESSAGES) == "primary"
    assert primary.calls == 4
    public = caplog.text + repr(provider)
    for secret in ("review-credential-a", "review-credential-b", "review-private-provider-detail"):
        assert secret not in public


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("base_url", "https://rotated.review.invalid/v1"), ("model", "rotated-model"),
])
async def test_open_state_does_not_follow_endpoint_or_model_rotation(circuit, field, value):
    primary, _, _ = circuit
    await trip(primary)
    setattr(primary, field, value)
    primary.error = None
    expected = value if field == "model" else "primary"
    assert await chat() == expected


@pytest.mark.asyncio
async def test_chat_open_state_does_not_block_intent_task(circuit):
    primary, _, _ = circuit
    await trip(primary)
    primary.error = None
    assert await factory.get_llm_provider("intent").chat(MESSAGES) == "primary"
    assert await chat() == "secondary"


def test_capacity_rejects_new_identity_when_all_slots_inflight_and_existing_leases_finish(monkeypatch, circuit):
    monkeypatch.setattr(routing, "MAX_CIRCUITS", 2)
    registry = routing._CircuitRegistry()
    providers = [OfflineProvider(name) for name in ("capacity-a", "capacity-b", "capacity-c")]
    first = registry.acquire("chat", "chat", providers[0])
    second = registry.acquire("chat", "chat", providers[1])
    assert first is not None and second is not None
    assert registry.acquire("chat", "chat", providers[2]) is None
    assert len(registry.states) == 2
    registry.finish(first, "success")
    registry.finish(second, "success")
    admitted = registry.acquire("chat", "chat", providers[2])
    assert admitted is not None
    assert len(registry.states) == 2
    assert first.state.inflight == second.state.inflight == 0
    registry.finish(admitted, "success")
    assert all(provider.calls == provider.stream_calls == 0 for provider in providers)


def test_lru_evicts_oldest_idle_only_and_stale_lease_cannot_modify_recreated_identity(monkeypatch, circuit):
    monkeypatch.setattr(routing, "MAX_CIRCUITS", 3)
    registry = routing._CircuitRegistry()
    providers = [OfflineProvider(name) for name in ("lru-active", "lru-b", "lru-c", "lru-d")]
    active = registry.acquire("chat", "chat", providers[0])
    older = registry.acquire("chat", "chat", providers[1])
    stale = registry.acquire("chat", "chat", providers[2])
    assert active is not None and older is not None and stale is not None
    registry.finish(older, "success")
    registry.finish(stale, "success")
    # Touch B: C is now the oldest idle entry, while the oldest overall A remains active.
    touched = registry.acquire("chat", "chat", providers[1])
    assert touched is not None
    registry.finish(touched, "success")
    latest = registry.acquire("chat", "chat", providers[3])
    assert latest is not None
    assert active.key in registry.states and older.key in registry.states
    assert stale.key not in registry.states
    registry.finish(latest, "success")
    recreated = registry.acquire("chat", "chat", providers[2])
    assert recreated is not None and recreated.state is not stale.state
    # A delayed callback for the evicted incarnation must not consume the new lease or trip its circuit.
    registry.finish(stale, "failure")
    assert recreated.state.inflight == 1
    assert recreated.state.failures == 0
    assert recreated.state.opened_until == 0
    assert registry.states[active.key] is active.state and active.state.inflight == 1
    registry.finish(recreated, "success")
    registry.finish(active, "success")
    assert len(registry.states) == 3


def test_idle_ttl_reclaims_expired_entries_but_preserves_active_leases(monkeypatch, circuit):
    _, _, now = circuit
    monkeypatch.setattr(routing, "MAX_CIRCUITS", 4)
    monkeypatch.setattr(routing, "CIRCUIT_IDLE_TTL_SECONDS", 5.0)
    registry = routing._CircuitRegistry()
    providers = [OfflineProvider(name) for name in ("ttl-active", "ttl-idle", "ttl-next", "ttl-last")]
    active = registry.acquire("chat", "chat", providers[0])
    idle = registry.acquire("chat", "chat", providers[1])
    assert active is not None and idle is not None
    registry.finish(idle, "success")
    # Effective TTL also protects two cooldown periods (2 * 30 seconds).
    now[0] += 61
    next_lease = registry.acquire("chat", "chat", providers[2])
    assert next_lease is not None
    assert idle.key not in registry.states
    assert registry.states[active.key] is active.state
    registry.finish(active, "success")
    now[0] += 61
    last = registry.acquire("chat", "chat", providers[3])
    assert last is not None
    assert active.key not in registry.states
    assert registry.states[next_lease.key] is next_lease.state
    registry.finish(next_lease, "success")
    registry.finish(last, "success")
    assert len(registry.states) == 2
