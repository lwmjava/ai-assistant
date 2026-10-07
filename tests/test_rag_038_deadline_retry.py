"""RAG-038 检索调用的统一总时限、有限退避重试与取消传播。

证据要求是**故障注入后实测到的行为**，不是「代码里写了 retry」：
- 429/5xx 的 attempt 次数等于配置上限，且总耗时有界；
- 4xx 只打一次；
- 慢查询在 deadline 处被终止，实测等待时间 ≤ deadline + 容差；
- 注入 CancelledError 后必须向上传播，不能被改写成 unavailable 或空结果；
- 多次子调用共享同一个 deadline（累加超总时限时第二次调用前就放弃）；
- 每次失败 attempt 记一条 rag_call_failed，且日志里没有 key / 正文；
- 外部服务失败时上层拿到的是 unavailable，不是「没有命中」。

时间控制一律用「注入 sleep + 可控时钟」，不靠真实睡眠拖慢 CI。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable

import httpx
import pytest

from app.core.config import (
    RETRY_BACKOFF_JITTER_RATIO,
    Settings,
    retrieval_retry_effectively_disabled,
    settings,
)
from app.rag.embeddings.base import EmbeddingInputPolicy
from app.rag.embeddings.openai_compatible import OpenAICompatibleEmbeddingProvider
from app.rag.resilience import (
    DEADLINE_EXHAUSTED_EVENT,
    FAILED_EVENT,
    SUCCEEDED_EVENT,
    Deadline,
    DeadlineExceededError,
    RetryPolicy,
    bind_deadline,
    current_deadline,
    ensure_budget,
    is_retryable,
    retry_async,
    retry_policy_from_settings,
    run_blocking_with_deadline,
)
from app.rag.retrieval_status import RetrievalStatus
from app.rag.retriever import HybridRetriever

# 实测等待时间的容差：Windows 上线程池调度与计时器精度有限，给足 0.35s。
_TOLERANCE = 0.35


# ────────────────────────── 测试替身与工具 ──────────────────────────


class FakeClock:
    """可控时钟：让「耗时有界」的断言不依赖真实睡眠。"""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class ScriptedOp:
    """按脚本抛异常的异步操作，记录每次 attempt 的序号。"""

    def __init__(self, script: list[BaseException | object]) -> None:
        self.script = script
        self.attempts = 0

    async def __call__(self) -> str:
        self.attempts += 1
        item = self.script[min(self.attempts - 1, len(self.script) - 1)]
        if isinstance(item, BaseException):
            raise item
        return "ok"


def _sleeper(log: list[float]) -> Callable[[float], object]:
    """记录等待时长但**不真的睡**，避免 CI 被退避拖慢。"""

    async def _sleep(seconds: float) -> None:
        log.append(seconds)

    return _sleep  # type: ignore[return-value]


def _policy(max_attempts: int = 3, base: float = 0.2, top: float = 1.5) -> RetryPolicy:
    return RetryPolicy(
        max_attempts=max_attempts, backoff_base=base, backoff_max=top, jitter=0.0
    )


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.invalid/v1/embeddings")
    return httpx.HTTPStatusError(
        f"status {status}", request=request, response=httpx.Response(status, request=request)
    )


def _failed_events(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if FAILED_EVENT in r.getMessage()]


# ────────────────────────── 1. 退避与次数上界 ──────────────────────────


@pytest.mark.asyncio
async def test_429_retries_up_to_configured_attempts(monkeypatch):
    """429 注入：attempt 次数 == 配置上限，且退避等待不等过 deadline。"""
    monkeypatch.setattr(settings, "RAG_RETRIEVAL_MAX_ATTEMPTS", 3)
    op = ScriptedOp([_http_error(429)])
    waits: list[float] = []
    clock = FakeClock()

    with pytest.raises(httpx.HTTPStatusError):
        await retry_async(
            op,
            deadline=Deadline(8.0, clock=clock),
            policy=_policy(max_attempts=settings.RAG_RETRIEVAL_MAX_ATTEMPTS),
            backend="embedding",
            sleep=_sleeper(waits),
        )

    assert op.attempts == 3, "429 可重试，必须打满配置上限（含首次）"
    # 退避序列：base * 2**0, base * 2**1 → 0.2 / 0.4，第三次失败后不再等。
    assert waits == [0.2, 0.4]


@pytest.mark.asyncio
async def test_5xx_retries_then_final_attempt_wins(monkeypatch):
    """5xx 注入：前两次失败、第三次成功 → 返回成功且不抛。"""
    op = ScriptedOp([_http_error(503), _http_error(500), "ok"])
    waits: list[float] = []
    result = await retry_async(
        op,
        deadline=Deadline(8.0, clock=FakeClock()),
        policy=_policy(),
        backend="embedding",
        sleep=_sleeper(waits),
    )
    assert result == "ok"
    assert op.attempts == 3
    assert waits == [0.2, 0.4]


@pytest.mark.asyncio
async def test_retry_count_is_bounded_by_config_not_by_error(monkeypatch):
    """把上限调到 5：attempt 跟着变 5，证明次数来自配置而不是写死。"""
    monkeypatch.setattr(settings, "RAG_RETRIEVAL_MAX_ATTEMPTS", 5)
    op = ScriptedOp([_http_error(500)])
    waits: list[float] = []
    with pytest.raises(httpx.HTTPStatusError):
        await retry_async(
            op,
            deadline=Deadline(60.0, clock=FakeClock()),
            policy=_policy(max_attempts=5),
            backend="embedding",
            sleep=_sleeper(waits),
        )
    assert op.attempts == 5
    assert len(waits) == 4  # 第 5 次失败后不再等


@pytest.mark.asyncio
async def test_backoff_is_capped_by_max():
    """退避按指数增长但夹到 backoff_max，不会无限膨胀。"""
    waits: list[float] = []
    await retry_async(
        ScriptedOp([_http_error(503), "ok"]),
        deadline=Deadline(60.0, clock=FakeClock()),
        policy=_policy(base=2.0, top=3.0),
        backend="embedding",
        sleep=_sleeper(waits),
    )
    assert waits == [2.0]
    # base=2, 第 4 次应为 16 → 夹到 3.0
    policy = _policy(base=2.0, top=3.0)
    assert policy.wait_for(4) == 3.0


# ────────────────────────── 2. 4xx 不重试 ──────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_4xx_is_not_retried(status):
    """4xx（除 429）是请求本身的问题，重试只会再错一次：attempt 必须为 1。"""
    op = ScriptedOp([_http_error(status)])
    waits: list[float] = []
    with pytest.raises(httpx.HTTPStatusError):
        await retry_async(
            op,
            deadline=Deadline(60.0, clock=FakeClock()),
            policy=_policy(),
            backend="embedding",
            sleep=_sleeper(waits),
        )
    assert op.attempts == 1
    assert waits == []


@pytest.mark.asyncio
async def test_business_error_is_not_retried():
    """业务性异常（维度不符等）同样不重试。"""
    op = ScriptedOp([ValueError("embedding_input_limit_exceeded")])
    waits: list[float] = []
    with pytest.raises(ValueError):
        await retry_async(
            op,
            deadline=Deadline(60.0, clock=FakeClock()),
            policy=_policy(),
            backend="embedding",
            sleep=_sleeper(waits),
        )
    assert op.attempts == 1


def test_is_retryable_matrix():
    """可重试判定表：连接/读超时/429/5xx 可重试，其余不可。"""
    assert is_retryable(httpx.ConnectError("boom"))
    assert is_retryable(httpx.ReadTimeout("slow"))
    assert is_retryable(httpx.PoolTimeout("busy"))
    assert is_retryable(_http_error(429))
    assert is_retryable(_http_error(500))
    assert is_retryable(_http_error(503))
    assert not is_retryable(_http_error(400))
    assert not is_retryable(_http_error(401))
    assert not is_retryable(_http_error(404))
    assert not is_retryable(ValueError("nope"))
    assert not is_retryable(asyncio.CancelledError())


# ────────────────────────── 3. 总时限有界 ──────────────────────────


@pytest.mark.asyncio
async def test_slow_query_is_terminated_at_deadline():
    """慢查询：实测等待 ≤ deadline + 容差，不能被拖满单次 timeout。"""
    deadline_seconds = 0.3
    started = time.monotonic()

    async def slow() -> str:
        await asyncio.sleep(5.0)  # 远超 deadline
        return "late"

    with pytest.raises(DeadlineExceededError):
        await run_blocking_with_deadline(
            lambda: time.sleep(5.0), deadline=Deadline(deadline_seconds), backend="milvus"
        )
    assert time.monotonic() - started <= deadline_seconds + _TOLERANCE

    # 异步路径同样受限：wait_for 到点就放弃，不等到底层结束。
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(slow(), timeout=deadline_seconds)
    assert time.monotonic() - started <= deadline_seconds + _TOLERANCE


@pytest.mark.asyncio
async def test_shared_deadline_aborts_second_subcall_before_running():
    """多次子调用共享同一个 deadline：每个子调用自己都正常返回（没有谁超时），
    但累加起来吃掉全部预算时，第二次调用**开始前**就放弃，而不是照跑不误。"""
    clock = FakeClock()
    deadline = Deadline(1.0, clock=clock)
    calls: list[int] = []

    async def subcall() -> str:
        calls.append(1)
        clock.advance(1.0)  # 单次自己没超时，但把总预算吃光了
        return "ok"

    assert await retry_async(
        subcall, deadline=deadline, policy=_policy(max_attempts=1), backend="embedding"
    ) == "ok"
    assert len(calls) == 1

    with pytest.raises(DeadlineExceededError):
        await retry_async(
            subcall, deadline=deadline, policy=_policy(max_attempts=1), backend="embedding"
        )
    assert len(calls) == 1, "预算已耗尽，第二个子调用不该被发起"


@pytest.mark.asyncio
async def test_backoff_does_not_sleep_past_deadline():
    """退避前检查剩余预算：不够等就不再等，直接放弃（这是耗时有界的关键）。"""
    clock = FakeClock()
    deadline = Deadline(0.5, clock=clock)
    waits: list[float] = []
    op = ScriptedOp([_http_error(503)])

    with pytest.raises(httpx.HTTPStatusError):
        await retry_async(
            op,
            deadline=deadline,
            policy=_policy(base=1.0, top=4.0),  # 第一次退避 1.0s > 剩余 0.5s
            backend="embedding",
            sleep=_sleeper(waits),
        )
    assert op.attempts == 1, "预算不够等下一次，必须放弃而不是睡过 deadline"
    assert waits == []


@pytest.mark.asyncio
async def test_total_elapsed_is_bounded_across_retries(caplog):
    """429 重试场景的总耗时有界：不会随 attempt 数线性膨胀到 deadline 之外。

    同时断言「一次失败 attempt 只记一条 rag_call_failed」——放弃等待也算一次
    attempt 的结局，不能既记 retry 又记 give_up_budget 把它记成两条。
    """
    caplog.set_level(logging.WARNING)
    deadline_seconds = 0.4
    started = time.monotonic()
    op = ScriptedOp([_http_error(429)])

    with pytest.raises((httpx.HTTPStatusError, DeadlineExceededError)):
        await retry_async(
            op,
            deadline=Deadline(deadline_seconds),
            policy=_policy(base=0.5, top=2.0),  # 退避远超剩余预算 → 应放弃等待
            backend="embedding",
        )
    elapsed = time.monotonic() - started
    assert elapsed <= deadline_seconds + _TOLERANCE, f"实测 {elapsed:.3f}s 超过总时限"
    assert op.attempts == 1
    assert len(_failed_events(caplog)) == 1
    assert "outcome=give_up_budget" in _failed_events(caplog)[0].getMessage()


def test_deadline_helpers():
    clock = FakeClock()
    d = Deadline(2.0, clock=clock)
    assert d.enforced
    assert d.remaining() == 2.0
    clock.advance(0.5)
    assert d.remaining() == 1.5
    assert d.subcall_timeout(60.0) == 1.5  # 单次 timeout 被剩余预算夹住
    clock.advance(1.5)
    assert d.expired()
    assert d.remaining() == 0.0

    off = Deadline(0)
    assert not off.enforced
    assert off.remaining() == float("inf")
    assert not off.expired()
    assert off.subcall_timeout(15.0) == 15.0


def test_ensure_budget_raises_when_expired():
    clock = FakeClock()
    d = Deadline(1.0, clock=clock)
    ensure_budget(d)  # 还有预算，不抛
    clock.advance(1.0)
    with pytest.raises(DeadlineExceededError):
        ensure_budget(d, backend="native", tenant="t1")


# ────────────────────────── 4. 取消传播 ──────────────────────────


@pytest.mark.asyncio
async def test_cancellation_propagates_from_retry_async():
    """注入 CancelledError：必须原样上抛，不能被改写成「检索不可用」。"""
    op = ScriptedOp([asyncio.CancelledError()])
    with pytest.raises(asyncio.CancelledError):
        await retry_async(
            op, deadline=Deadline(60.0, clock=FakeClock()), policy=_policy(), backend="embedding"
        )
    assert op.attempts == 1


@pytest.mark.asyncio
async def test_cancellation_during_backoff_propagates():
    """取消发生在退避等待期间，同样原样上抛。"""

    async def cancelling_sleep(_: float) -> None:
        raise asyncio.CancelledError()

    op = ScriptedOp([_http_error(503)])
    with pytest.raises(asyncio.CancelledError):
        await retry_async(
            op,
            deadline=Deadline(60.0, clock=FakeClock()),
            policy=_policy(),
            backend="embedding",
            sleep=cancelling_sleep,
        )
    assert op.attempts == 1


@pytest.mark.asyncio
async def test_cancellation_from_blocking_call_propagates(caplog):
    """pymilvus 这类同步调用被取消：CancelledError 原样上抛，不变成空结果。

    取消不是「调用失败」，因此也不该记一条 rag_call_failed——否则监控会把
    用户主动取消统计成依赖方故障。
    """
    caplog.set_level(logging.WARNING)

    def blocking() -> str:
        time.sleep(1.0)  # 远长于下面的取消时机
        return "done"

    task = asyncio.ensure_future(
        run_blocking_with_deadline(blocking, deadline=Deadline(5.0), backend="milvus")
    )
    await asyncio.sleep(0.05)  # 确保已进入线程池执行
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _failed_events(caplog) == [], "取消不该被记成一次调用失败"


@pytest.mark.asyncio
async def test_cancelled_error_is_not_converged_to_unavailable():
    """取消经 HybridRetriever 时不能被收敛成 unavailable：必须继续上抛。

    RAG-037 的契约是「检索故障 → unavailable」，但取消不是故障：把它写成
    「检索不可用」会让调用方以为依赖方挂了，还会掩盖真实的取消原因。
    """
    from app.rag.vectorstore.base import ChunkResult  # noqa: F401  仅说明类型

    class CancellingBackend:
        name = "cancel"

        async def retrieve(self, query: str, *, tenant_id: str, top_k: int, read_scope=None):
            raise asyncio.CancelledError()

    retriever = HybridRetriever(CancellingBackend(), tenant_id="t", top_k=5)  # type: ignore[arg-type]
    with pytest.raises(asyncio.CancelledError):
        await retriever.retrieve("查一下", "")
    assert retriever.last_status != RetrievalStatus.UNAVAILABLE


# ────────────────────────── 5. 失败日志 ──────────────────────────


@pytest.mark.asyncio
async def test_failed_attempt_logs_once_per_attempt_without_secrets(caplog, monkeypatch):
    """每次失败 attempt 记一条 rag_call_failed；日志里不得有 key / 正文。"""
    caplog.set_level(logging.WARNING)
    secret_key = "sk-rag038-super-secret"
    body = "机密正文：员工身份证号 110101199001011234"
    monkeypatch.setattr(settings, "RAG_RETRIEVAL_MAX_ATTEMPTS", 3)

    exc = _http_error(429)
    exc.__notes__ = []
    op = ScriptedOp([exc])
    waits: list[float] = []

    with pytest.raises(httpx.HTTPStatusError):
        await retry_async(
            op,
            deadline=Deadline(8.0, clock=FakeClock()),
            policy=_policy(max_attempts=3),
            backend="embedding",
            tenant="tenant-7",
            sleep=_sleeper(waits),
        )

    events = _failed_events(caplog)
    assert len(events) == op.attempts == 3, "失败 attempt 与日志条数必须一一对应"
    for record in caplog.records:
        message = record.getMessage()
        assert secret_key not in message
        assert body not in message
        assert "Authorization" not in message
        assert "Bearer" not in message
    for record in events:
        message = record.getMessage()
        assert "attempt=" in message and "error_type=" in message
        assert "backend=embedding" in message and "tenant=tenant-7" in message
    # 前两次是可重试的，第三次是耗尽
    outcomes = [r.getMessage().split("outcome=")[1] for r in events]
    assert outcomes == ["retry", "retry", "give_up_attempts_exhausted"]


@pytest.mark.asyncio
async def test_deadline_exhausted_logs_distinct_event(caplog):
    """预算耗尽（还没开始调用）记 rag_deadline_exhausted，与「调用失败」区分。"""
    caplog.set_level(logging.WARNING)
    clock = FakeClock()
    d = Deadline(1.0, clock=clock)
    clock.advance(1.0)
    with pytest.raises(DeadlineExceededError):
        await retry_async(
            ScriptedOp(["ok"]), deadline=d, policy=_policy(max_attempts=1), backend="milvus"
        )
    messages = [r.getMessage() for r in caplog.records]
    assert any(DEADLINE_EXHAUSTED_EVENT in m for m in messages)
    assert not any(FAILED_EVENT in m for m in messages), "没发起调用就不该记调用失败"


# ────────────────────────── 6. 不伪造空知识 ──────────────────────────


@pytest.mark.asyncio
async def test_external_failure_converges_to_unavailable_not_no_hit():
    """外部服务失败：上层拿到的是 unavailable，而不是「查过了、没有」。"""
    from app.rag.vectorstore.base import ChunkResult  # noqa: F401

    class FailingBackend:
        name = "failing"

        async def retrieve(self, query: str, *, tenant_id: str, top_k: int, read_scope=None):
            raise RuntimeError("Milvus 检索失败")

    class EmptyBackend:
        name = "empty"

        async def retrieve(self, query: str, *, tenant_id: str, top_k: int, read_scope=None):
            return []

    failing = HybridRetriever(FailingBackend(), tenant_id="t", top_k=5)  # type: ignore[arg-type]
    assert await failing.retrieve("查一下", "") == ""
    assert failing.last_status is RetrievalStatus.UNAVAILABLE
    assert failing.last_outcome.error_type == "RuntimeError"

    empty = HybridRetriever(EmptyBackend(), tenant_id="t", top_k=5)  # type: ignore[arg-type]
    await empty.retrieve("查一下", "")
    assert empty.last_status is RetrievalStatus.NO_HIT
    assert empty.last_outcome.error_type == "", "真没命中不该带 error_type"


@pytest.mark.asyncio
async def test_retry_never_returns_empty_fallback():
    """retry_async 失败时抛而不是返回空值：返回空列表等于冒充「查过了」。"""
    result = await retry_async(
        ScriptedOp(["ok"]),
        deadline=Deadline(1.0, clock=FakeClock()),
        policy=_policy(max_attempts=1),
        backend="embedding",
    )
    assert result == "ok"

    with pytest.raises(httpx.HTTPStatusError):
        await retry_async(
            ScriptedOp([_http_error(503)]),
            deadline=Deadline(1.0, clock=FakeClock()),
            policy=_policy(max_attempts=1),
            backend="embedding",
        )


@pytest.mark.asyncio
async def test_run_blocking_never_returns_empty_on_timeout():
    """同步调用超时同样抛，不返回空列表。"""
    with pytest.raises(DeadlineExceededError):
        await run_blocking_with_deadline(
            lambda: time.sleep(2.0), deadline=Deadline(0.2), backend="milvus"
        )


# ────────────────────────── 8. 真实调用链接线 ──────────────────────────

# 必须在任何 monkeypatch 之前抓取，否则第二次打桩会把自己再包一层。
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _embedding_provider(
    monkeypatch: pytest.MonkeyPatch,
    respond: Callable[[httpx.Request], httpx.Response],
    *,
    dim: int = 2,
    timeout: float = 15.0,
    batch_size: int = 10,
) -> tuple[OpenAICompatibleEmbeddingProvider, list[httpx.Request]]:
    """构造走 MockTransport 的真实 provider，并返回请求记录。

    打在 ``httpx.AsyncClient`` 上（与既有日志最小化测试一致），保证测的是
    provider 内部的真实 httpx 调用链，不是替身。
    """
    real_client = _REAL_ASYNC_CLIENT
    requests: list[httpx.Request] = []

    def client_with_transport(*args, **kwargs):
        def wrapped(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return respond(request)

        kwargs["transport"] = httpx.MockTransport(wrapped)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_with_transport)
    provider = OpenAICompatibleEmbeddingProvider(
        "https://embeddings.invalid/v1",
        "sk-rag038-must-not-appear-in-logs",
        "text-embedding-v3",
        dim=dim,
        timeout=timeout,
        batch_size=batch_size,
        input_policy=EmbeddingInputPolicy(
            max_input_tokens=8192,
            counter=len,
            counting_method="synthetic-test",
            source="fixture",
        ),
    )
    return provider, requests


class _RecordingEmbedding:
    """可控耗时的嵌入替身：记录调用次数，并推进共享 deadline 的时钟。"""

    model = "fake-embedding"
    dim = 2

    def __init__(self, cost: float = 0.0, clock: FakeClock | None = None) -> None:
        self.calls = 0
        self._cost = cost
        self._clock = clock

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if self._clock is not None:
            self._clock.advance(self._cost)
        return [[0.1, 0.9] for _ in texts]


class _RecordingStore:
    """记录被调用与否的向量库替身。"""

    def __init__(self) -> None:
        self.calls = 0

    async def hybrid_search(self, *args, **kwargs):
        self.calls += 1
        return []


@pytest.mark.asyncio
async def test_embedding_write_path_many_batches_are_not_cut_off(monkeypatch, caplog):
    """写入 / 导入路径：100 块分 10 批、每批都成功，不得被**检索**总时限打断。

    写入路径不绑定 deadline，因此每批各拿一个 ``RAG_EMBED_BATCH_DEADLINE_SECONDS``。
    若按 High-01 的写法（整通调用共用一个检索总时限），这里会在第 4 批左右抛
    ``DeadlineExceededError`` 并刷 ``rag_deadline_exhausted``，正常导入失败。

    实测（改前 ``e978819``）：100 块 / 10 批 × 1.0s → 8.08s 抛错、0 向量；
    父提交 ``e978819^`` 同场景 10.11s、100 向量成功。
    """
    caplog.set_level(logging.WARNING)
    # 检索总时限故意设成远小于整通耗时（1.0s）：一旦被误用就会立刻暴露。
    monkeypatch.setattr(settings, "RAG_RETRIEVAL_DEADLINE_SECONDS", 1.0)
    monkeypatch.setattr(settings, "RAG_EMBED_BATCH_DEADLINE_SECONDS", 20.0)

    async def respond(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.3)  # 每批都成功，只是不快
        size = len(json.loads(request.content)["input"])
        return httpx.Response(
            200, json={"data": [{"index": i, "embedding": [0.1, 0.9]} for i in range(size)]}
        )

    provider, requests = _embedding_provider(monkeypatch, respond)
    vectors = await provider.embed([f"块-{i}" for i in range(100)])

    assert len(requests) == 10, f"应发出 10 批，实际 {len(requests)}"
    assert len(vectors) == 100
    exhausted = [r for r in caplog.records if DEADLINE_EXHAUSTED_EVENT in r.getMessage()]
    assert exhausted == [], "写入路径不该出现预算耗尽事件"


@pytest.mark.asyncio
async def test_embedding_read_path_batches_share_one_deadline(monkeypatch):
    """检索路径：绑定 1.0s 后各批必须**共享**同一个预算。

    判据是每批的读超时被剩余预算逐批压缩（单调不增且最后一批明显变小），
    而不是每批都拿到完整的 ``self.timeout``。
    """
    read_timeouts: list[float] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        read_timeouts.append(float((request.extensions.get("timeout") or {}).get("read")))
        await asyncio.sleep(0.2)  # 每批都成功，但会吃掉共享预算
        size = len(json.loads(request.content)["input"])
        return httpx.Response(
            200, json={"data": [{"index": i, "embedding": [0.1, 0.9]} for i in range(size)]}
        )

    provider, requests = _embedding_provider(monkeypatch, respond, batch_size=1)
    with bind_deadline(Deadline(1.0)):
        vectors = await provider.embed(["a", "b", "c", "d"])

    assert len(requests) == 4
    assert len(vectors) == 4
    assert read_timeouts, "必须真的带着超时发出请求"
    assert max(read_timeouts) <= 1.0, f"读超时未被共享 deadline 夹住：{read_timeouts}"
    assert read_timeouts == sorted(read_timeouts, reverse=True), (
        f"后续批次的读超时应被剩余预算压缩：{read_timeouts}"
    )
    assert read_timeouts[-1] < read_timeouts[0], f"末批未被压缩：{read_timeouts}"


@pytest.mark.asyncio
async def test_native_backend_cancellation_propagates_through_real_retrieve():
    """取消注入必须穿过**真实**的 ``NativeRagBackend.retrieve``。

    这里同时存在「超时改写」与「取消上抛」两种语义（``asyncio.wait_for`` +
    ``except TimeoutError``），是全链路最容易被顺手改写取消的地方；
    复审的变异 M14（把 ``except TimeoutError`` 扩成
    ``except (TimeoutError, CancelledError)``）曾存活，说明此前没有守护。
    """
    from app.rag.backend.native import NativeRagBackend

    class CancellingEmbedding:
        model = "cancel-embedding"
        dim = 2

        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise asyncio.CancelledError()

    store = _RecordingStore()
    backend = NativeRagBackend(CancellingEmbedding(), store, tokenizer=lambda q: [])  # type: ignore[arg-type]
    with pytest.raises(asyncio.CancelledError):
        await backend.retrieve("查一下", tenant_id="t1", top_k=5)
    assert store.calls == 0, "取消后不该再发起向量检索"


@pytest.mark.asyncio
async def test_milvus_reuses_bound_deadline_not_settings(monkeypatch):
    """Milvus 必须用**绑定**的 deadline，而不是按配置新起一个。"""
    from app.rag.vectorstore.milvus import MilvusUnavailableError, MilvusVectorStore

    # 配置给 3.0s，绑定给 0.2s：若用了配置值，实测耗时会接近 3s 而不是 0.2s。
    monkeypatch.setattr(settings, "RAG_RETRIEVAL_DEADLINE_SECONDS", 3.0)

    class FakeIndex:
        id = "idx-1"
        name = "coll-1"
        dim = 2

    class HangingCollection:
        indexes: list = []

        def search(self, **kwargs):
            time.sleep(5.0)
            return [[]]

    store = MilvusVectorStore(session=None)  # type: ignore[arg-type]
    monkeypatch.setattr(store, "_resolve_index", lambda *a, **k: FakeIndex())
    monkeypatch.setattr(store, "_connect", lambda *a, **k: HangingCollection())

    started = time.monotonic()
    with bind_deadline(Deadline(0.2)):
        with pytest.raises(MilvusUnavailableError):
            await store.hybrid_search([0.1, 0.9], ["查"], "t1", 5)
    elapsed = time.monotonic() - started
    assert elapsed <= 0.2 + _TOLERANCE, f"实测 {elapsed:.3f}s 说明用的是配置值而非绑定值"


@pytest.mark.asyncio
async def test_success_event_records_attempt(caplog):
    """成功也要记一条：首次成功 attempt=1，重试后成功 attempt=N。

    只记失败 attempt 会让依赖方抖动期不可观测——看不到「重试了几次才成功」，
    也分不清「一直很稳」和「抖完自己好了」。
    """
    caplog.set_level(logging.INFO)
    policy = _policy(max_attempts=3)

    await retry_async(
        ScriptedOp(["ok"]),
        deadline=Deadline(8.0, clock=FakeClock()),
        policy=policy,
        backend="embedding",
        tenant="t9",
        sleep=_sleeper([]),
    )
    await retry_async(
        ScriptedOp([_http_error(429), _http_error(503), "ok"]),
        deadline=Deadline(8.0, clock=FakeClock()),
        policy=policy,
        backend="embedding",
        tenant="t9",
        sleep=_sleeper([]),
    )
    ok_events = [r for r in caplog.records if SUCCEEDED_EVENT in r.getMessage()]
    assert len(ok_events) == 2
    attempts = [r.getMessage().split("attempt=")[1].split()[0] for r in ok_events]
    assert attempts == ["1", "3"]
    for record in ok_events:
        assert "backend=embedding" in record.getMessage()
        assert "tenant=t9" in record.getMessage()


@pytest.mark.asyncio
async def test_embedding_success_logs_attempt_one(monkeypatch, caplog):
    """真实嵌入链路：首次就成功也记 ``rag_call_succeeded attempt=1``。"""
    caplog.set_level(logging.INFO)

    async def respond(request: httpx.Request) -> httpx.Response:
        size = len(json.loads(request.content)["input"])
        return httpx.Response(
            200, json={"data": [{"index": i, "embedding": [0.1, 0.9]} for i in range(size)]}
        )

    provider, requests = _embedding_provider(monkeypatch, respond)
    await provider.embed(["合成文本"])
    assert len(requests) == 1
    ok_events = [r for r in caplog.records if SUCCEEDED_EVENT in r.getMessage()]
    assert len(ok_events) == 1
    assert "attempt=1" in ok_events[0].getMessage()


def test_retry_effectively_disabled_predicate():
    """「重试实际已禁用」的判据：第一次退避等待的上界必须严格小于总时限。"""
    assert retrieval_retry_effectively_disabled(
        deadline_seconds=0.5, max_attempts=3, backoff_base=0.5, backoff_max=2.0
    ), "0.5s 放不下 min(0.5,2.0)*1.2=0.6s 的退避"
    assert not retrieval_retry_effectively_disabled(
        deadline_seconds=8.0, max_attempts=3, backoff_base=0.2, backoff_max=1.5
    ), "默认配置下重试必须有效"
    assert retrieval_retry_effectively_disabled(
        deadline_seconds=8.0, max_attempts=1, backoff_base=0.2, backoff_max=1.5
    ), "MAX_ATTEMPTS=1 等价于不重试"
    assert retrieval_retry_effectively_disabled(
        deadline_seconds=8.0, max_attempts=3, backoff_base=0.2, backoff_max=1.5, enabled=False
    ), "总开关关闭等价于不重试"
    # 关闭时限（0）时每次 attempt 都能打出去，重试本身仍有效
    assert not retrieval_retry_effectively_disabled(
        deadline_seconds=0, max_attempts=3, backoff_base=0.2, backoff_max=1.5
    )
    # 判据用的是抖动后的上界：退避基数略小于 deadline/1.2 时仍算放得下
    ratio = 1.0 + RETRY_BACKOFF_JITTER_RATIO
    assert not retrieval_retry_effectively_disabled(
        deadline_seconds=1.0, max_attempts=3, backoff_base=1.0 / ratio - 0.01, backoff_max=2.0
    )
    assert retrieval_retry_effectively_disabled(
        deadline_seconds=1.0, max_attempts=3, backoff_base=1.0 / ratio + 0.01, backoff_max=2.0
    )


def test_startup_warns_when_retry_effectively_disabled(caplog):
    """启动即告警：总时限放不下一次退避时，配置装载必须说清楚。"""
    caplog.set_level(logging.WARNING)
    bad = Settings(
        RAG_RETRIEVAL_DEADLINE_SECONDS=0.5,
        RAG_RETRIEVAL_BACKOFF_BASE_SECONDS=0.5,
        RAG_RETRIEVAL_BACKOFF_MAX_SECONDS=2.0,
        RAG_RETRIEVAL_MAX_ATTEMPTS=3,
        RAG_RETRIEVAL_RESILIENCE_ENABLED=True,
    )
    assert retrieval_retry_effectively_disabled(
        deadline_seconds=bad.RAG_RETRIEVAL_DEADLINE_SECONDS,
        max_attempts=bad.RAG_RETRIEVAL_MAX_ATTEMPTS,
        backoff_base=bad.RAG_RETRIEVAL_BACKOFF_BASE_SECONDS,
        backoff_max=bad.RAG_RETRIEVAL_BACKOFF_MAX_SECONDS,
    )
    messages = [r.getMessage() for r in caplog.records]
    assert any("rag_retry_effectively_disabled" in m for m in messages), messages
    assert any("重试实际已禁用" in m for m in messages), messages

    caplog.clear()
    good = Settings(
        RAG_RETRIEVAL_DEADLINE_SECONDS=8.0,
        RAG_RETRIEVAL_BACKOFF_BASE_SECONDS=0.2,
        RAG_RETRIEVAL_BACKOFF_MAX_SECONDS=1.5,
        RAG_RETRIEVAL_MAX_ATTEMPTS=3,
        RAG_RETRIEVAL_RESILIENCE_ENABLED=True,
    )
    assert not retrieval_retry_effectively_disabled(
        deadline_seconds=good.RAG_RETRIEVAL_DEADLINE_SECONDS,
        max_attempts=good.RAG_RETRIEVAL_MAX_ATTEMPTS,
        backoff_base=good.RAG_RETRIEVAL_BACKOFF_BASE_SECONDS,
        backoff_max=good.RAG_RETRIEVAL_BACKOFF_MAX_SECONDS,
    )
    assert not [r for r in caplog.records if "rag_retry_effectively_disabled" in r.getMessage()]


@pytest.mark.asyncio
async def test_embedding_429_retries_to_configured_limit(monkeypatch):
    """真实嵌入链路：429 注入 → attempt 次数 == 配置上限，总耗时有界。"""
    monkeypatch.setattr(settings, "RAG_RETRIEVAL_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(settings, "RAG_RETRIEVAL_BACKOFF_BASE_SECONDS", 0.01)
    monkeypatch.setattr(settings, "RAG_RETRIEVAL_BACKOFF_MAX_SECONDS", 0.02)
    monkeypatch.setattr(settings, "RAG_RETRIEVAL_DEADLINE_SECONDS", 8.0)

    provider, requests = _embedding_provider(monkeypatch, lambda _: httpx.Response(429, json={"error": "rate"}))
    started = time.monotonic()
    with pytest.raises(httpx.HTTPStatusError):
        await provider.embed(["合成文本"])
    elapsed = time.monotonic() - started

    assert len(requests) == 3, "429 可重试，真实链路必须打满配置上限"
    assert elapsed <= 8.0, f"总耗时 {elapsed:.3f}s 必须受总时限约束"


@pytest.mark.asyncio
async def test_embedding_4xx_is_not_retried(monkeypatch):
    """真实嵌入链路：400 / 401 只发一次请求。"""
    monkeypatch.setattr(settings, "RAG_RETRIEVAL_MAX_ATTEMPTS", 3)
    for status in (400, 401):
        provider, requests = _embedding_provider(
            monkeypatch,
            lambda _, s=status: httpx.Response(s, json={"error": "bad"})
        )
        with pytest.raises(httpx.HTTPStatusError):
            await provider.embed(["合成文本"])
        assert len(requests) == 1, f"{status} 不可重试，attempt 必须为 1"


@pytest.mark.asyncio
async def test_embedding_reuses_the_bound_deadline(monkeypatch):
    """嵌入必须复用外层绑定的 deadline，而不是自己另起一个。

    判据是单次请求的读超时：它被「剩余预算」夹住（≤ 0.5s），而不是用自己配的
    15s / 按配置新起的 8s。若嵌入各起一个 deadline，这里会看到 8.0 而不是 0.5。
    """
    read_timeouts: list[float] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        read_timeouts.append(float((request.extensions.get("timeout") or {}).get("read")))
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.9]}]})

    provider, _ = _embedding_provider(monkeypatch, respond)
    with bind_deadline(Deadline(0.5)):
        await provider.embed(["合成文本"])
    assert read_timeouts, "必须真的带着超时发出请求"
    assert max(read_timeouts) <= 0.5, f"读超时 {max(read_timeouts)} 未被共享 deadline 夹住"


@pytest.mark.asyncio
async def test_embedding_5xx_exhausts_then_raises(monkeypatch):
    """真实嵌入链路：5xx 重试耗尽后向上抛，绝不返回空向量冒充成功。"""
    monkeypatch.setattr(settings, "RAG_RETRIEVAL_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(settings, "RAG_RETRIEVAL_BACKOFF_BASE_SECONDS", 0.01)
    monkeypatch.setattr(settings, "RAG_RETRIEVAL_BACKOFF_MAX_SECONDS", 0.02)
    provider, requests = _embedding_provider(monkeypatch, lambda _: httpx.Response(503, json={"error": "down"}))
    with pytest.raises(httpx.HTTPStatusError):
        await provider.embed(["合成文本"])
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_embedding_slow_query_aborts_at_deadline(monkeypatch):
    """慢查询：**写入侧单批预算**把单次超时夹住，实测等待 ≤ deadline + 容差。

    这条走的是未绑定（写入 / 导入）路径，因此夹的是
    ``RAG_EMBED_BATCH_DEADLINE_SECONDS``；检索路径见下一个用例。

    ``httpx.MockTransport`` 不走真实传输层、也不会自己应用 timeout，所以这里
    由 handler 读取 httpx 写入 ``request.extensions["timeout"]`` 的读超时，
    并按真实传输层的行为模拟「等过 read timeout 就抛 ReadTimeout」。
    这样既断言了「超时值被 deadline 夹住」，也断言了「实测耗时有界」。
    """
    monkeypatch.setattr(settings, "RAG_RETRIEVAL_DEADLINE_SECONDS", 0.3)
    monkeypatch.setattr(settings, "RAG_EMBED_BATCH_DEADLINE_SECONDS", 0.3)
    monkeypatch.setattr(settings, "RAG_RETRIEVAL_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(settings, "RAG_RETRIEVAL_BACKOFF_BASE_SECONDS", 0.01)
    monkeypatch.setattr(settings, "RAG_RETRIEVAL_BACKOFF_MAX_SECONDS", 0.02)

    read_timeouts: list[float] = []

    async def slow(request: httpx.Request) -> httpx.Response:
        timeout = (request.extensions.get("timeout") or {}).get("read")
        assert timeout is not None, "provider 必须给出显式的读超时"
        read_timeouts.append(float(timeout))
        # 真实传输层的行为：等过读超时就抛 ReadTimeout（可重试故障）。
        # 这里把模拟等待夹到 1s：即使有人把超时改回 15s，测试也不会挂住，
        # 而是靠「实测耗时超上界」判红。
        await asyncio.sleep(min(float(timeout) + 0.01, 1.0))
        raise httpx.ReadTimeout("read timeout", request=request)

    provider, requests = _embedding_provider(monkeypatch, slow)
    started = time.monotonic()
    with pytest.raises((httpx.ReadTimeout, DeadlineExceededError)):
        await provider.embed(["合成文本"])
    elapsed = time.monotonic() - started

    # 单次超时被夹进剩余预算：绝不会是 self.timeout=15s。
    assert read_timeouts, "必须真的带着超时发出请求"
    assert max(read_timeouts) <= 0.3, f"单次读超时 {max(read_timeouts)} 未被总时限夹住"
    assert elapsed <= 0.3 + _TOLERANCE, f"实测 {elapsed:.3f}s 超过总时限 0.3s"
    # 预算耗尽后不再重试：慢查询不能靠「多试几次」把时间翻倍。
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_embedding_slow_query_aborts_at_bound_deadline(monkeypatch):
    """检索路径的慢查询：绑定 0.3s 后单次超时被夹住，实测等待 ≤ 0.3 + 容差。"""
    read_timeouts: list[float] = []

    async def slow(request: httpx.Request) -> httpx.Response:
        read_timeouts.append(float((request.extensions.get("timeout") or {}).get("read")))
        await asyncio.sleep(min(float(read_timeouts[-1]) + 0.01, 1.0))
        raise httpx.ReadTimeout("read timeout", request=request)

    provider, requests = _embedding_provider(monkeypatch, slow)
    started = time.monotonic()
    with bind_deadline(Deadline(0.3)):
        with pytest.raises((httpx.ReadTimeout, DeadlineExceededError)):
            await provider.embed(["合成文本"])
    elapsed = time.monotonic() - started

    assert read_timeouts and max(read_timeouts) <= 0.3, f"读超时未被夹住：{read_timeouts}"
    assert elapsed <= 0.3 + _TOLERANCE, f"实测 {elapsed:.3f}s 超过总时限 0.3s"
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_embedding_failure_log_has_no_key_or_body(caplog, monkeypatch):
    """真实链路失败日志：条数 == attempt，且不含密钥 / 请求正文。"""
    caplog.set_level(logging.WARNING)
    monkeypatch.setattr(settings, "RAG_RETRIEVAL_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(settings, "RAG_RETRIEVAL_BACKOFF_BASE_SECONDS", 0.01)
    monkeypatch.setattr(settings, "RAG_RETRIEVAL_BACKOFF_MAX_SECONDS", 0.02)

    secret = "sk-rag038-must-not-appear-in-logs"
    body = "机密正文：合同金额 1,234,567 元"
    provider, requests = _embedding_provider(monkeypatch, lambda _: httpx.Response(500, json={"error": "boom"}))
    with pytest.raises(httpx.HTTPStatusError):
        await provider.embed([body])

    events = _failed_events(caplog)
    assert len(events) == len(requests) == 2
    # 密钥 / 正文的排查不能只盯 rag_call_failed 一种事件：任何一条日志漏出去
    # 都是漏，因此这里扫全部记录（变异验证 (g) 就是靠这条才被抓住的）。
    for record in caplog.records:
        message = record.getMessage()
        assert secret not in message
        assert body not in message
        assert "Authorization" not in message
        assert "Bearer" not in message


@pytest.mark.asyncio
async def test_native_backend_shares_one_deadline_between_embed_and_search(monkeypatch):
    """后端组合点：嵌入与向量检索共用同一个 deadline。

    嵌入吃掉全部预算后，向量检索**不会被发起**——若两者各起一个 deadline，
    这里会照常调用 store，总耗时变成 2 × deadline。
    """
    monkeypatch.setattr(settings, "RAG_RETRIEVAL_DEADLINE_SECONDS", 1.0)
    clock = FakeClock()
    embedding = _RecordingEmbedding(cost=1.0, clock=clock)
    store = _RecordingStore()
    from app.rag.backend.native import NativeRagBackend

    backend = NativeRagBackend(embedding, store, tokenizer=lambda q: q.split())  # type: ignore[arg-type]
    with bind_deadline(Deadline(1.0, clock=clock)):
        with pytest.raises(DeadlineExceededError):
            await backend.retrieve("查一下", tenant_id="t1", top_k=5)
    assert embedding.calls == 1
    assert store.calls == 0, "预算已被嵌入吃光，向量检索不该再发起"


@pytest.mark.asyncio
async def test_native_backend_total_elapsed_is_bounded(monkeypatch):
    """真实时钟下的总耗时有界：嵌入 0.1s + 向量检索 5s，总时限 0.4s 就放弃。"""
    from app.rag.backend.native import NativeRagBackend

    monkeypatch.setattr(settings, "RAG_RETRIEVAL_DEADLINE_SECONDS", 0.4)

    class SlowEmbedding:
        model = "slow"
        dim = 2

        async def embed(self, texts: list[str]) -> list[list[float]]:
            await asyncio.sleep(0.1)
            return [[0.1, 0.9] for _ in texts]

    class HangingStore:
        async def hybrid_search(self, *args, **kwargs):
            await asyncio.sleep(5.0)
            return []

    backend = NativeRagBackend(SlowEmbedding(), HangingStore(), tokenizer=lambda q: [])  # type: ignore[arg-type]
    started = time.monotonic()
    with pytest.raises(DeadlineExceededError):
        await backend.retrieve("查一下", tenant_id="t1", top_k=5)
    elapsed = time.monotonic() - started
    assert elapsed <= 0.4 + _TOLERANCE, f"实测 {elapsed:.3f}s 超过总时限 0.4s"


@pytest.mark.asyncio
async def test_native_backend_propagates_embedding_failure(monkeypatch):
    """嵌入失败：后端向上抛，不返回空列表冒充「查过了、没有」。"""
    from app.rag.backend.native import NativeRagBackend

    class FailingEmbedding:
        model = "failing"
        dim = 2

        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise httpx.ConnectError("connection refused")

    backend = NativeRagBackend(FailingEmbedding(), _RecordingStore(), tokenizer=lambda q: [])  # type: ignore[arg-type]
    with pytest.raises(httpx.ConnectError):
        await backend.retrieve("查一下", tenant_id="t1", top_k=5)


@pytest.mark.asyncio
async def test_milvus_slow_search_is_bounded_and_never_empty(monkeypatch):
    """Milvus 慢查询：总时限到期就放弃，抛 MilvusUnavailableError，不回空列表。"""
    from app.rag.vectorstore.milvus import MilvusUnavailableError, MilvusVectorStore

    monkeypatch.setattr(settings, "RAG_RETRIEVAL_DEADLINE_SECONDS", 0.3)

    class FakeIndex:
        id = "idx-1"
        name = "coll-1"
        dim = 2

    class FakeCollection:
        indexes: list = []

        def search(self, **kwargs):
            time.sleep(3.0)  # 远超 deadline
            return [[]]

    store = MilvusVectorStore(session=None)  # type: ignore[arg-type]
    monkeypatch.setattr(store, "_resolve_index", lambda *a, **k: FakeIndex())
    monkeypatch.setattr(store, "_connect", lambda *a, **k: FakeCollection())

    started = time.monotonic()
    with pytest.raises(MilvusUnavailableError) as excinfo:
        await store.hybrid_search([0.1, 0.9], ["查"], "t1", 5)
    elapsed = time.monotonic() - started

    assert elapsed <= 0.3 + _TOLERANCE, f"实测 {elapsed:.3f}s 超过总时限 0.3s"
    # 超时原因可追溯：包的是 DeadlineExceededError，而不是被吞成空结果。
    assert isinstance(excinfo.value.__cause__, DeadlineExceededError)


@pytest.mark.asyncio
async def test_milvus_cancellation_is_not_rewritten_as_empty(monkeypatch):
    """Milvus 查询被取消：CancelledError 原样上抛，不变成 unavailable / 空结果。"""
    from app.rag.vectorstore.milvus import MilvusVectorStore

    class FakeIndex:
        id = "idx-1"
        name = "coll-1"
        dim = 2

    class FakeCollection:
        indexes: list = []

        def search(self, **kwargs):
            time.sleep(1.0)
            return [[]]

    store = MilvusVectorStore(session=None)  # type: ignore[arg-type]
    monkeypatch.setattr(store, "_resolve_index", lambda *a, **k: FakeIndex())
    monkeypatch.setattr(store, "_connect", lambda *a, **k: FakeCollection())

    task = asyncio.ensure_future(store.hybrid_search([0.1, 0.9], ["查"], "t1", 5))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_unavailable_not_no_hit_through_native_and_retriever(monkeypatch):
    """端到端：真实后端 + 真实检索契约，外部故障 → unavailable 而非 no_hit。"""
    from app.rag.backend.native import NativeRagBackend

    class FailingStore:
        async def hybrid_search(self, *args, **kwargs):
            raise RuntimeError("Milvus 检索失败")

    backend = NativeRagBackend(
        _RecordingEmbedding(), FailingStore(), tokenizer=lambda q: []  # type: ignore[arg-type]
    )
    retriever = HybridRetriever(backend, tenant_id="t1", top_k=5)
    assert await retriever.retrieve("查一下", "") == ""
    assert retriever.last_status is RetrievalStatus.UNAVAILABLE
    assert retriever.last_outcome.error_type == "RuntimeError"


# ────────────────────────── 7. 开关与配置 ──────────────────────────


@pytest.mark.asyncio
async def test_deadline_disabled_falls_back_to_plain_call(monkeypatch):
    """总时限关掉（0）：不限时也不重试，行为退回 RAG-038 之前但不出错。"""
    from app.rag.backend.native import NativeRagBackend

    monkeypatch.setattr(settings, "RAG_RETRIEVAL_DEADLINE_SECONDS", 0)
    assert (
        await run_blocking_with_deadline(
            lambda: "ok", deadline=Deadline(0), backend="milvus"
        )
    ) == "ok"

    embedding = _RecordingEmbedding()
    store = _RecordingStore()
    backend = NativeRagBackend(embedding, store, tokenizer=lambda q: [])  # type: ignore[arg-type]
    assert await backend.retrieve("查一下", tenant_id="t1", top_k=5) == []
    assert embedding.calls == 1
    assert store.calls == 1


@pytest.mark.asyncio
async def test_switch_off_disables_retry(monkeypatch):
    """总开关关闭：不重试（attempt == 1），行为退回 RAG-038 之前。"""
    monkeypatch.setattr(settings, "RAG_RETRIEVAL_RESILIENCE_ENABLED", False)
    policy = retry_policy_from_settings()
    assert policy.attempts() == 1

    op = ScriptedOp([_http_error(503)])
    waits: list[float] = []
    with pytest.raises(httpx.HTTPStatusError):
        await retry_async(
            op,
            deadline=Deadline(60.0, clock=FakeClock()),
            policy=policy,
            backend="embedding",
            sleep=_sleeper(waits),
        )
    assert op.attempts == 1
    assert waits == []


def test_policy_from_settings_reads_live_config(monkeypatch):
    monkeypatch.setattr(settings, "RAG_RETRIEVAL_MAX_ATTEMPTS", 4)
    monkeypatch.setattr(settings, "RAG_RETRIEVAL_BACKOFF_BASE_SECONDS", 0.5)
    monkeypatch.setattr(settings, "RAG_RETRIEVAL_BACKOFF_MAX_SECONDS", 2.0)
    policy = retry_policy_from_settings()
    assert policy.attempts() == 4
    # 默认带 ±20% 抖动，用区间断言而不是等值。
    assert policy.wait_for(1) == pytest.approx(0.5, abs=0.1)
    # 0.5 * 2**2 = 2.0 已到 backoff_max：抖动只会把它往下压，不会顶穿上界。
    assert 1.6 <= policy.wait_for(3) <= 2.0
    assert policy.wait_for(10) <= 2.0


def test_bind_deadline_is_scoped():
    """bind_deadline 只在 with 块内可见，退出后恢复，不泄漏到后续调用。"""
    assert current_deadline() is None
    d = Deadline(5.0)
    with bind_deadline(d) as bound:
        assert bound is d
        assert current_deadline() is d
    assert current_deadline() is None
