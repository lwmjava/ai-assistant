"""检索调用的**总**时限、有限退避重试与取消传播（RAG-038）。

背景（改前实测，复现脚本见实现说明）：检索路径只有**逐次** HTTP timeout，
即嵌入与向量检索各持一个 ``LLM_TIMEOUT=60`` 预算，一次检索最坏可拖 120s；
429 / 5xx 不重试，慢查询没有上界，取消语义没有显式约定。

本模块给出四个构件，全部基于标准库（``asyncio`` / ``time`` / ``random``），
不引入 tenacity、backoff 之类的新依赖：

- :class:`Deadline`：基于 ``time.monotonic()`` 的**总**时限句柄。一次检索内的
  所有子调用共享同一个实例，因此总耗时有界，而不是每个子调用各自一个上界。
- :func:`retry_async`：只对可重试故障（连接错误 / 读超时 / 429 / 5xx）做指数
  退避 + 抖动重试；4xx（除 429）与其它业务错误一律不重试；剩余预算不够等待
  下一次时**直接放弃**，不去睡过 deadline。
- :func:`run_blocking_with_deadline`：给 pymilvus 这类**同步**客户端套总时限。
- :func:`ensure_budget`：子调用开始前先看预算，没了就不开始。

两条硬性约定：

1. **取消不改写。** ``asyncio.CancelledError`` 在 Python 3.8+ 继承
   ``BaseException``，本模块所有 ``except`` 都只捕获 ``Exception`` 并显式重抛
   取消，绝不把「上游/用户取消了」改写成「检索不可用」。同理，调用方若把
   ``except Exception`` 放宽成 ``except BaseException``，取消就会被静默吞掉——
   专项测试对此有断言。
2. **故障不伪造空知识。** 重试耗尽 / 超时一律向上抛，绝不返回空列表让上层
   以为「查过了、没有」。收敛为 ``unavailable`` 由 RAG-037 的检索状态契约负责，
   本模块只保证不吞错、不造假。

两条必须说清的**粒度**（复审后补，避免被字面意思误导）：

1. **「总时限」的粒度是「一次检索」，不是「一次用户请求」。**
   :func:`bind_deadline` 用的是 ``ContextVar``：``asyncio`` 每次
   ``create_task`` 会复制当前 context，因此 ``NativeRagBackend.retrieve`` 内
   的绑定只影响**该次调用**派生的子协程（嵌入、向量检索）；``asyncio.gather``
   并发的多个子调用共享父任务的 deadline，这正是「一次检索一个总预算」的预期
   语义。但调用方不绑定时，每调一次 ``retrieve`` 就新起一个预算——一次用户请求
   触发 N 次检索，总耗时按 ``N × deadline`` 累加。当前三条线上路径
   （fast_path / pipeline / supervisor）每请求只调一次检索，因此现网不受影响；
   若将来出现「一请求多检索」，需要由调用方显式 :func:`bind_deadline` 包一层。
2. **写入路径不复用检索预算。** :func:`current_or_new_deadline` 新起的那个
   deadline 覆盖的是**这一次调用本身**。``embed()`` 一次调用可能含几十批，
   因此嵌入入口**不能**直接用它：未绑定时每批各拿一个
   ``RAG_EMBED_BATCH_DEADLINE_SECONDS``，详见
   ``app/rag/embeddings/openai_compatible.py``。

另外一条运行期约束：总时限必须**严格大于**第一次退避等待的上界，否则第一次
失败后必然走进「预算不够等 → 放弃」，一次重试都不会发生。配置层
（``app.core.config.retrieval_retry_effectively_disabled``）在启动时会校验并告警。
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TypeVar

from app.core.config import RETRY_BACKOFF_JITTER_RATIO, settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: 失败 attempt 的统一事件名。字段固定，便于按 attempt / backend 聚合与告警。
FAILED_EVENT = "rag_call_failed"
#: 成功 attempt 的统一事件名。抖动期只有失败日志是看不见「重试后好了」的，
#: 首次就成功（attempt=1）也记一条，否则无法区分「没抖动」和「抖了又好了」。
SUCCEEDED_EVENT = "rag_call_succeeded"
#: 预算耗尽（还没开始调用就已经没有时间了）的事件名，独立于「某次调用失败」。
DEADLINE_EXHAUSTED_EVENT = "rag_deadline_exhausted"

#: 单次子调用的最小超时（秒）。httpx 的 ``timeout=0`` 含义与「立刻超时」不同，
#: 这里显式夹一个下限，避免出现无超时连接。
_MIN_SUBCALL_TIMEOUT = 0.01

try:  # pragma: no cover - httpx 是本仓既有依赖；做成可选以便纯算法单测
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]


class DeadlineExceededError(RuntimeError):
    """检索在共享总时限内没跑完。

    继承 ``RuntimeError``（即 ``Exception``），因此会被 RAG-037 的检索状态契约
    收敛为 ``unavailable`` 并强制披露，不会被当成「没有命中」。
    """


class Deadline:
    """一次检索的**共享**总时限。

    与「逐次 timeout」的区别：逐次 timeout 是每个子调用各一个上界，N 个子调用
    最坏耗时是 N × timeout；``Deadline`` 是整条检索链路共用一个预算，子调用按
    :meth:`remaining` 夹取自己的超时，任何一环拖沓都会压缩后面所有环节，
    因此**总**耗时有界。

    ``total <= 0`` 表示关闭时限（排障用）：:meth:`remaining` 返回
    ``inf``，:meth:`expired` 恒为假，行为退回改前。
    """

    def __init__(self, total: float, *, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self.total = float(total)
        self._started = self._clock()

    @property
    def enforced(self) -> bool:
        """是否真的在限时（``total <= 0`` 时为假）。"""
        return self.total > 0

    def elapsed(self) -> float:
        """已经消耗的秒数。"""
        return max(0.0, self._clock() - self._started)

    def remaining(self) -> float:
        """剩余预算（秒）。关闭时限时返回 ``inf``。"""
        if not self.enforced:
            return math.inf
        return max(0.0, self.total - self.elapsed())

    def expired(self) -> bool:
        """预算是否已经耗尽。"""
        return self.enforced and self.remaining() <= 0.0

    def subcall_timeout(self, configured: float) -> float:
        """把「本环节自己配的超时」夹到剩余预算内。

        剩余预算不足时返回 ``_MIN_SUBCALL_TIMEOUT`` 而不是 0：0 在 httpx 里不是
        「立刻超时」而是另一套语义，会给排查带来歧义。
        """
        if not self.enforced:
            return configured
        return max(min(configured, self.remaining()), _MIN_SUBCALL_TIMEOUT)

    def __repr__(self) -> str:  # pragma: no cover - 仅调试
        return f"Deadline(total={self.total}, remaining={self.remaining():.3f})"


_CURRENT_DEADLINE: ContextVar[Deadline | None] = ContextVar(
    "rag_retrieval_deadline", default=None
)


@contextmanager
def bind_deadline(deadline: Deadline) -> Iterator[Deadline]:
    """把 deadline 绑到当前上下文，供链路下游的子调用共享。

    用 ContextVar 而不是给每个 provider / store 加形参，是为了不改
    ``EmbeddingProvider.embed`` / ``VectorStore.hybrid_search`` 的协议签名
    （两者都被多张卡共用的调用方使用）。组合点只有一处（后端
    ``retrieve``），在那里绑定即可，语义与 Go 的 ``context`` 传播一致。
    """
    token = _CURRENT_DEADLINE.set(deadline)
    try:
        yield deadline
    finally:
        _CURRENT_DEADLINE.reset(token)


def current_deadline() -> Deadline | None:
    """取当前上下文绑定的 deadline；未绑定返回 ``None``。"""
    return _CURRENT_DEADLINE.get()


def deadline_from_settings() -> Deadline:
    """按配置新起一个 deadline。"""
    return Deadline(settings.RAG_RETRIEVAL_DEADLINE_SECONDS)


def current_or_new_deadline() -> Deadline:
    """优先复用当前检索共享的 deadline，没有则按配置新起一个。

    ⚠️ 这里新起的 deadline 覆盖的是**这一次调用本身**，不是「一次业务操作」。
    仅适用于「一次调用 = 一次远程往返」的入口（如 Milvus 检索）。

    ``embed()`` 这类「一次调用含 N 批往返」的入口**不要**直接用它：那会让
    N 批共用一个 8s 的**检索**预算，正常导入也会被中途打断（复审 High-01）。
    那里应当每批各拿一个 ``RAG_EMBED_BATCH_DEADLINE_SECONDS``。
    """
    return current_deadline() or deadline_from_settings()


@dataclass(frozen=True)
class RetryPolicy:
    """退避重试策略。

    ``max_attempts`` **含首次**：3 表示「最多打 3 次」，即最多 2 次重试。
    ``jitter`` 为退避抖动比例，置 0 可让测试完全确定。

    默认值直接取自配置（``RAG_RETRIEVAL_*``），避免在两处各写一份默认数字。
    """

    # 默认值读的是**导入时**的配置快照；运行期改配置请走
    # ``retry_policy_from_settings()``，它每次都重读。
    max_attempts: int = settings.RAG_RETRIEVAL_MAX_ATTEMPTS
    backoff_base: float = settings.RAG_RETRIEVAL_BACKOFF_BASE_SECONDS
    backoff_max: float = settings.RAG_RETRIEVAL_BACKOFF_MAX_SECONDS
    jitter: float = RETRY_BACKOFF_JITTER_RATIO
    enabled: bool = settings.RAG_RETRIEVAL_RESILIENCE_ENABLED

    def attempts(self) -> int:
        """实际允许的 attempt 总数；关闭重试时为 1。"""
        if not self.enabled:
            return 1
        return max(1, int(self.max_attempts))

    def wait_for(self, attempt: int) -> float:
        """第 ``attempt`` 次失败后应等待的秒数（指数退避 + 抖动，夹到上限）。"""
        if not self.enabled or attempt < 1:
            return 0.0
        raw = min(self.backoff_base * (2 ** (attempt - 1)), self.backoff_max)
        if self.jitter:
            raw *= random.uniform(1.0 - self.jitter, 1.0 + self.jitter)
        return max(0.0, min(raw, self.backoff_max))


def retry_policy_from_settings() -> RetryPolicy:
    """按当前配置构造策略（每次调用都重读，便于测试改配置后即生效）。"""
    return RetryPolicy(
        max_attempts=settings.RAG_RETRIEVAL_MAX_ATTEMPTS,
        backoff_base=settings.RAG_RETRIEVAL_BACKOFF_BASE_SECONDS,
        backoff_max=settings.RAG_RETRIEVAL_BACKOFF_MAX_SECONDS,
        enabled=settings.RAG_RETRIEVAL_RESILIENCE_ENABLED,
    )


def _status_code(exc: BaseException) -> int | None:
    """从异常上取 HTTP 状态码（httpx 的 ``HTTPStatusError.response.status_code``）。"""
    for holder in (getattr(exc, "response", None), exc):
        code = getattr(holder, "status_code", None)
        if isinstance(code, int):
            return code
    return None


def is_retryable(exc: BaseException) -> bool:
    """判断异常是否值得重试。

    可重试：连接错误 / 读超时等传输层故障、``429``、``5xx``——这些是依赖方抖动，
    换一次调用可能就好。
    不可重试：``4xx``（除 429）是请求本身有问题，重试只会再错一次；业务性异常
    （维度不符、输入超限）同理。
    """
    status = _status_code(exc)
    if status is not None:
        return status == 429 or 500 <= status < 600
    if httpx is not None and isinstance(exc, httpx.TransportError):
        return True
    return False


def _log_failure(
    *,
    attempt: int,
    elapsed: float,
    error_type: str,
    backend: str,
    tenant: str,
    outcome: str,
) -> None:
    """记一条失败 attempt。

    字段固定为 attempt / elapsed_ms / error_type / backend / tenant / outcome。
    **严禁**写入 Authorization、api key、请求正文或命中正文：这些一旦落盘，
    日志就变成第二份密钥库。
    """
    logger.warning(
        "%s attempt=%s elapsed_ms=%s error_type=%s backend=%s tenant=%s outcome=%s",
        FAILED_EVENT,
        attempt,
        int(elapsed * 1000),
        error_type,
        backend or "-",
        tenant or "-",
        outcome,
    )


def _log_success(
    *,
    attempt: int,
    elapsed: float,
    backend: str,
    tenant: str,
) -> None:
    """记一条成功调用，字段与失败事件对齐（只差 outcome / error_type）。

    只记失败 attempt 会让依赖方抖动期不可观测：看不到「重试了几次才成功」，
    也分不清「一直很稳」和「抖完自己好了」。首次就成功同样记一条
    ``attempt=1``，作为抖动期的基线。
    """
    logger.info(
        "%s attempt=%s elapsed_ms=%s backend=%s tenant=%s outcome=ok",
        SUCCEEDED_EVENT,
        attempt,
        int(elapsed * 1000),
        backend or "-",
        tenant or "-",
    )


def _log_deadline_exhausted(*, backend: str, tenant: str, elapsed: float) -> None:
    logger.warning(
        "%s elapsed_ms=%s backend=%s tenant=%s",
        DEADLINE_EXHAUSTED_EVENT,
        int(elapsed * 1000),
        backend or "-",
        tenant or "-",
    )


def ensure_budget(deadline: Deadline, *, backend: str = "", tenant: str = "") -> None:
    """子调用开始前检查共享预算；耗尽就抛，不开始新的调用。

    这是「总耗时有界」的另一半：光有子调用超时不够，预算用完后必须拒绝
    发起下一个子调用，否则 N 个「都没超单次 timeout」的子调用累加起来
    仍然可以远超总时限。
    """
    if deadline.expired():
        _log_deadline_exhausted(backend=backend, tenant=tenant, elapsed=deadline.elapsed())
        raise DeadlineExceededError(
            f"检索总时限 {deadline.total:.3f}s 已耗尽，不再发起后续子调用"
        )


async def retry_async(
    operation: Callable[[], Awaitable[T]],
    *,
    deadline: Deadline,
    policy: RetryPolicy,
    backend: str = "",
    tenant: str = "",
    on_attempt: Callable[[int, BaseException], None] | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> T:
    """在共享 deadline 内以有限退避执行 ``operation``。

    - 次数上界：``policy.attempts()``（含首次），达到即抛出，绝不无限重试。
    - 时间上界：每次 attempt 前先 :func:`ensure_budget`；退避等待前检查剩余
      预算，不够就**放弃等待直接抛**，不睡过 deadline。``backoff_base<=0`` 是
      「零退避」：``wait=0`` 视为无等待、立即重试（``await sleeper(0)`` 让出一次），
      不算放弃——配置判据 ``retrieval_retry_effectively_disabled`` 在 base=0 时也
      返回 False（认为重试有效），二者一致。
    - 取消传播：``asyncio.CancelledError`` 原样上抛，不被任何分支改写。

    Args:
        operation: 无参协程工厂。每次 attempt 重新调用一次。
        deadline: 与其它子调用共享的总时限。
        policy: 退避重试策略。
        backend: 故障归属（``embedding`` / ``milvus`` …），只进日志。
        tenant: 租户标识，只进日志。
        on_attempt: 每次失败 attempt 后回调 ``(attempt, exc)``，测试用它计数。
        sleep: 可注入的等待函数，测试用它避免真实睡眠。

    Raises:
        DeadlineExceededError: 预算耗尽（未开始或退避预算不足）。
        BaseException: 最后一次 attempt 的原始异常（不可重试或次数耗尽）。
    """
    sleeper = sleep if sleep is not None else asyncio.sleep
    max_attempts = policy.attempts()
    attempt = 0
    while True:
        ensure_budget(deadline, backend=backend, tenant=tenant)
        attempt += 1
        started = deadline.elapsed()
        try:
            result = await operation()
        except asyncio.CancelledError:
            # 取消不是「检索失败」：原样上抛，绝不收敛成 unavailable。
            raise
        except Exception as exc:
            elapsed = deadline.elapsed() - started
            if not is_retryable(exc):
                outcome = "give_up_not_retryable"
            elif attempt >= max_attempts:
                outcome = "give_up_attempts_exhausted"
            else:
                wait = policy.wait_for(attempt)
                # 关键：预算不够等下一次就不再等。等过去必然睡过 deadline，
                # 「有界」就名存实亡了。
                # wait<=0（backoff_base<=0）表示**零退避**：立即重试，只 await
                # sleeper(0) 让出一次事件循环即可，不是「放弃」。只有当等待大于零
                # 且剩余预算连这点等待都不够时，才 give_up_budget。
                if wait > 0 and wait >= deadline.remaining():
                    outcome = "give_up_budget"
                else:
                    outcome = "retry"
            _log_failure(
                attempt=attempt,
                elapsed=elapsed,
                error_type=type(exc).__name__,
                backend=backend,
                tenant=tenant,
                outcome=outcome,
            )
            if on_attempt is not None:
                on_attempt(attempt, exc)
            if outcome != "retry":
                raise
            try:
                await sleeper(wait)
            except asyncio.CancelledError:
                raise
            continue
        _log_success(
            attempt=attempt,
            elapsed=deadline.elapsed() - started,
            backend=backend,
            tenant=tenant,
        )
        return result


async def run_blocking_with_deadline(
    func: Callable[..., T],
    *args: object,
    deadline: Deadline,
    backend: str = "",
    tenant: str = "",
    **kwargs: object,
) -> T:
    """在共享 deadline 内执行**同步**阻塞调用（如 pymilvus）。

    pymilvus 是同步客户端，直接在事件循环里调用会阻塞整个进程；放进线程后再用
    ``asyncio.wait_for`` 限时。

    ⚠️ 边界：``wait_for`` 超时只能让**协程**放弃等待，**无法真正中断**线程里的
    底层调用——线程会继续跑到底才释放。因此这里的「有界」是「调用方不再等」，
    不是「服务端调用被取消」。被放弃的调用不会污染结果（返回值被丢弃），
    也不会被写成空结果：超时按 ``DeadlineExceededError`` 上抛。
    """
    ensure_budget(deadline, backend=backend, tenant=tenant)
    started = deadline.elapsed()
    # 关闭时限时（total <= 0）传 None 而不是 inf：inf 会让 wait_for 的定时器溢出。
    timeout = deadline.remaining() if deadline.enforced else None
    try:
        raw = await asyncio.wait_for(
            asyncio.to_thread(func, *args, **kwargs), timeout=timeout
        )
    except asyncio.CancelledError:
        raise
    except TimeoutError as exc:
        _log_failure(
            attempt=1,
            elapsed=deadline.elapsed() - started,
            error_type=type(exc).__name__,
            backend=backend,
            tenant=tenant,
            outcome="give_up_budget",
        )
        raise DeadlineExceededError(
            f"{backend or '检索'} 调用在总时限 {deadline.total:.3f}s 内未返回"
        ) from exc
    except Exception as exc:
        _log_failure(
            attempt=1,
            elapsed=deadline.elapsed() - started,
            error_type=type(exc).__name__,
            backend=backend,
            tenant=tenant,
            outcome="give_up_not_retryable",
        )
        raise
    _log_success(
        attempt=1,
        elapsed=deadline.elapsed() - started,
        backend=backend,
        tenant=tenant,
    )
    return raw
