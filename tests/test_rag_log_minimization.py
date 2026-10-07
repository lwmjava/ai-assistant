"""Observed regression v1: arbitrary RAG text never enters logging records.

Synthetic markers deliberately do not resemble credentials, so a secret-pattern
filter cannot substitute for removing text at the logging call site.
"""

from __future__ import annotations

import copy
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.core import log_context
from app.core.config import settings
from app.core.json_logging import JsonLogFormatter
from app.rag.backend import factory
from app.rag.embeddings.base import EmbeddingInputPolicy
from app.rag.embeddings.openai_compatible import OpenAICompatibleEmbeddingProvider
from app.rag.retriever import HybridRetriever
from app.rag.vectorstore.base import ChunkResult

QUERY = "privateBusinessQuestionZebra42"
PLAN = "privateBusinessPlanAlbatross43"
EXCERPT = "privateDocumentExcerptWalrus44"
UPSTREAM = "privateUpstreamBodyOtter45"
EXCEPTION = "privateExceptionPenguin46"
CONFIG = "privateConfigParrot47"
MARKERS = (QUERY, PLAN, EXCERPT, UPSTREAM, EXCEPTION, CONFIG)


@pytest.fixture
def source_records(monkeypatch):
    """Capture before Handler filters; preserve the application's log context."""
    records = []
    previous = (log_context.get_trace_id(), log_context.get_user_id(), log_context.get_tenant_id())
    log_context.set_trace_id("trace-log-regression")
    log_context.set_user("user-log-regression", "tenant-log-regression")
    root = logging.getLogger()
    previous_level = root.level
    # Logger.setLevel also invalidates descendant isEnabledFor caches. Direct
    # attribute assignment can retain INFO=False from an earlier logging test.
    root.setLevel(logging.DEBUG)

    def capture(_logger, record):
        records.append(copy.copy(record))

    monkeypatch.setattr(logging.Logger, "handle", capture)
    yield records
    root.setLevel(previous_level)
    log_context.set_trace_id(previous[0])
    log_context.set_user(previous[1], previous[2])


def assert_minimized(records, logger_name):
    scoped = [record for record in records if record.name == logger_name]
    assert scoped, f"Missing diagnostic event from {logger_name}"
    for record in scoped:
        raw = repr((record.msg, record.args, record.__dict__))
        plain = logging.Formatter("%(levelname)s %(message)s").format(copy.copy(record))
        rendered = JsonLogFormatter().format(copy.copy(record))
        for marker in MARKERS:
            assert marker not in raw, "Sensitive text retained in source LogRecord"
            assert marker not in plain, "Sensitive text emitted by plain formatter"
            assert marker not in rendered, "Sensitive text emitted by JSON formatter"
        assert record.exc_info is None, "Tracebacks can replay untrusted exception text"
        assert record.stack_info is None
        payload = json.loads(rendered)
        assert payload["trace_id"] == "trace-log-regression"
        assert payload["user_id"] == "user-log-regression"
        assert payload["tenant_id"] == "tenant-log-regression"
    return "\n".join(record.getMessage() for record in scoped)


@pytest.mark.asyncio
@pytest.mark.parametrize("keep_high", [False, True])
async def test_low_similarity_logs_counts_without_query_plan_or_excerpt(monkeypatch, source_records, keep_high):
    monkeypatch.setattr(settings, "RAG_MIN_SIMILARITY", 0.4)
    low = ChunkResult("low", EXCERPT, EXCERPT, "doc", 0.03, similarity=0.1)
    high = ChunkResult("high", EXCERPT, EXCERPT, "doc", 0.02, similarity=0.9)
    hits = [low, high] if keep_high else [low]
    backend = SimpleNamespace(retrieve=AsyncMock(return_value=hits))
    retriever = HybridRetriever(backend, "tenant-log-regression")
    result = await retriever.retrieve(QUERY, PLAN)
    # 本卡断言的是日志不含正文，不与检索读范围的具体取值绑定：
    # 只校验调用发生且未夹带正文，避免把 read_scope 的历史默认值写进契约。
    assert backend.retrieve.await_count == 1
    _args, kwargs = backend.retrieve.await_args
    assert kwargs["tenant_id"] == "tenant-log-regression"
    assert kwargs["top_k"] == 5
    assert "read_scope" in kwargs
    if keep_high:
        assert EXCERPT in result
        assert retriever.last_hits == [high]
    else:
        assert result == settings.RAG_REFUSE_MESSAGE
        assert retriever.last_hits == []
    message = assert_minimized(source_records, "app.rag.retriever")
    assert "rag_low_similarity_filtered" in message
    assert "0.4" in message
    assert "kept=" in message


@pytest.mark.asyncio
async def test_embedding_http_error_logs_metadata_but_preserves_exception(monkeypatch, source_records):
    from app.rag.embeddings import openai_compatible

    response = httpx.Response(
        503,
        text=UPSTREAM,
        request=httpx.Request("POST", f"https://invalid.example/{CONFIG}/embeddings"),
    )
    client = Mock()
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(openai_compatible.httpx, "AsyncClient", Mock(return_value=client))
    provider = OpenAICompatibleEmbeddingProvider(
        f"https://invalid.example/{CONFIG}", CONFIG, CONFIG,
        input_policy=EmbeddingInputPolicy(
            max_input_tokens=8192, counter=len, counting_method="synthetic-test", source="fixture",
        ),
    )
    with pytest.raises(httpx.HTTPStatusError) as raised:
        await provider.embed([QUERY, EXCERPT])
    assert raised.value.response is response
    assert raised.value.response.text == UPSTREAM
    assert client.post.await_args.kwargs["json"]["input"] == [QUERY, EXCERPT]
    message = assert_minimized(source_records, "app.rag.embeddings.openai_compatible")
    assert "503" in message
    assert "batch_size=2" in message


def test_unknown_backend_logs_no_arbitrary_config_and_keeps_native_fallback(source_records):
    assert factory.normalize_rag_backend(CONFIG) == "native"
    message = assert_minimized(source_records, "app.rag.backend.factory")
    assert "native" in message


@pytest.mark.parametrize("error_type", [RuntimeError, ImportError])
def test_optional_backend_constructor_error_keeps_fallback_without_exception_text(
    monkeypatch, source_records, error_type
):
    import sys

    broken = Mock(side_effect=error_type(EXCEPTION))
    monkeypatch.setitem(
        sys.modules,
        "app.rag.backend.llamaindex_backend",
        SimpleNamespace(LlamaIndexRagBackend=broken),
    )
    embedding, store = Mock(), Mock()
    result = factory.get_rag_backend(embedding, store, backend="llamaindex")
    assert isinstance(result, factory.NativeRagBackend)
    broken.assert_called_once()
    message = assert_minimized(source_records, "app.rag.backend.factory")
    assert error_type.__name__ in message
    assert "native" in message


@pytest.mark.asyncio
async def test_milvus_delete_failure_retains_diagnostics_without_exception_body(monkeypatch, source_records):
    from app.rag.vectorstore.milvus import MilvusVectorStore

    store = MilvusVectorStore(Mock())
    collection = Mock()
    collection.query.side_effect = RuntimeError(EXCEPTION)
    monkeypatch.setattr(store, "_connect", lambda: collection)
    assert await store.delete_by_document("document-log-regression", "tenant-log-regression") == 0
    collection.delete.assert_not_called()
    message = assert_minimized(source_records, "app.rag.vectorstore.milvus")
    assert "RuntimeError" in message


@pytest.mark.asyncio
async def test_import_failure_logs_type_not_exception_and_keeps_failed_job(monkeypatch, source_records):
    from app.models.rag import ImportJob
    from app.rag import import_jobs

    job = ImportJob(
        tenant_id="tenant-log-regression", user_id="user-log-regression", storage_path="synthetic-source-path"
    )
    session = Mock()
    monkeypatch.setattr(import_jobs, "read_source_file", Mock(side_effect=RuntimeError(EXCEPTION)))
    trace = Mock()
    monkeypatch.setattr(import_jobs, "_record_job_trace", trace)
    monkeypatch.setattr(import_jobs, "_drop_attempt_source", Mock())
    await import_jobs._process_job(session, job)
    assert job.status == "failed"
    assert job.attempt_count == 1
    assert trace.call_args.args[2].args == (EXCEPTION,)
    assert session.commit.call_count == 2
    message = assert_minimized(source_records, "app.rag.import_jobs")
    assert "RuntimeError" in message
    assert job.id in message


@pytest.mark.asyncio
async def test_import_scheduler_tick_failure_retains_type_without_traceback(monkeypatch, source_records):
    import asyncio

    from app.rag import import_scheduler

    tick = AsyncMock(side_effect=RuntimeError(EXCEPTION))
    monkeypatch.setattr(import_scheduler, "run_import_jobs_once", tick)
    # Stop after one iteration, before any real database or scheduler task runs.
    sleep = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr(import_scheduler.asyncio, "sleep", sleep)
    await import_scheduler._loop()
    tick.assert_awaited_once()
    sleep.assert_awaited_once()
    message = assert_minimized(source_records, "app.rag.import_scheduler")
    assert "RuntimeError" in message


@pytest.mark.asyncio
async def test_stream_sources_remain_in_response_but_not_logs(monkeypatch, source_records):
    from app.agents.pipeline import AgentEvent
    from app.services.chat_service import ChatService

    service = object.__new__(ChatService)
    conv = SimpleNamespace(id="conversation-log-regression")
    sources = [{"chunk_id": "chunk-log-regression", "excerpt": EXCERPT, "source": EXCERPT}]
    monkeypatch.setattr(service, "_apply_input_security", lambda *a: (None, QUERY))
    monkeypatch.setattr(service, "_accept_user_message", lambda *a: conv)
    monkeypatch.setattr(
        service, "_build_memory", AsyncMock(return_value=SimpleNamespace(recent_messages=[], memory_context=""))
    )
    monkeypatch.setattr(service, "_match_skills", lambda *a: None)
    monkeypatch.setattr(service, "_build_retriever", lambda *a: object())
    monkeypatch.setattr(service, "_build_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "_fast_route", lambda *a: object())

    async def events(*_args):
        yield AgentEvent("token", "synthetic-answer")
        yield AgentEvent("done", "")

    monkeypatch.setattr(service, "_iter_fast", events)
    monkeypatch.setattr(service, "_reply_sources", lambda *a: sources)
    monkeypatch.setattr(service, "_apply_output_security", lambda *a: None)
    persisted = Mock()
    monkeypatch.setattr(service, "_persist_assistant", persisted)
    monkeypatch.setattr(service, "_model_name", lambda: "synthetic-model")
    monkeypatch.setattr(ChatService, "_conversation_after_reply", lambda *a: None)
    result = [event async for event in service.chat_stream(Mock(), Mock(), QUERY)]
    assert json.loads(next(event.data for event in result if event.type == "sources")) == sources
    assert persisted.call_args.args[4] is sources
    message = assert_minimized(source_records, "app.services.chat_service")
    assert "rag_sources_returned" in message
    assert "1" in message


@pytest.mark.parametrize(
    "template,args,event",
    [
        ("未能删除本次新写的源文件", (), "rag_source_discard_failed"),
        ("保存源文件失败: filename=%s", (EXCERPT,), "rag_source_save_failed"),
        ("上传文档解析成功，但知识库摄取失败: filename=%s", (EXCERPT,), "rag_upload_ingest_failed"),
        ("创建上传导入任务时保存源文件失败", (), "rag_import_source_save_failed"),
        (CONFIG, (), "rag_api_event"),
    ],
)
def test_api_handler_boundary_omits_payload_and_traceback(source_records, template, args, event):
    """Existing API calls are protected at their permitted output boundary."""
    from app.security.log_redaction import RedactingLogFilter

    filtered = []

    class CaptureHandler(logging.Handler):
        def emit(self, record):
            filtered.append(copy.copy(record))

    handler = CaptureHandler()
    handler.addFilter(RedactingLogFilter())
    error = RuntimeError(EXCEPTION)
    record = logging.LogRecord(
        "app.api.routes.rag",
        logging.ERROR,
        __file__,
        1,
        template,
        args,
        (RuntimeError, error, None),
    )
    # Include already formatted traceback text to test both formatter cache paths.
    record.exc_text = f"RuntimeError: {EXCEPTION}"
    # Installation applies the filter at logger and handler boundaries. Running
    # the second copy must preserve the first event and its exception category.
    assert RedactingLogFilter().filter(record)
    once = copy.copy(record)
    handler.handle(record)
    message = assert_minimized(filtered, "app.api.routes.rag")
    assert message == f"{event} error_type=RuntimeError"
    assert filtered[0].getMessage() == once.getMessage()
    assert filtered[0].args == once.args == ()
    assert filtered[0].exc_text == once.exc_text is None
    assert_minimized([once], "app.api.routes.rag")


@pytest.fixture
def real_filtered_records(monkeypatch):
    """Real Logger.handle and Handler.handle, including formatter output."""
    from app.security.log_redaction import RedactingLogFilter

    filtered = []
    previous = (log_context.get_trace_id(), log_context.get_user_id(), log_context.get_tenant_id())
    log_context.set_trace_id("trace-log-regression")
    log_context.set_user("user-log-regression", "tenant-log-regression")

    class CaptureHandler(logging.Handler):
        def emit(self, record):
            plain = logging.Formatter("%(levelname)s %(message)s").format(copy.copy(record))
            rendered = JsonLogFormatter().format(copy.copy(record))
            captured = copy.copy(record)
            # Assert after the business call, so a leaking logging handler does
            # not itself interrupt pipeline error handling or HTTP completion.
            captured._test_plain_output = plain
            captured._test_json_output = rendered
            filtered.append(captured)

    handler = CaptureHandler()
    handler.addFilter(RedactingLogFilter())
    levels = []
    for name in ("app.agents.pipeline", "httpx"):
        logger = logging.getLogger(name)
        levels.append((logger, logger.level))
        logger.setLevel(logging.DEBUG)
        monkeypatch.setattr(logger, "handlers", [handler])
        monkeypatch.setattr(logger, "propagate", False)
        monkeypatch.setattr(logger, "filters", [RedactingLogFilter()])
    yield filtered
    for logger, level in levels:
        logger.setLevel(level)
    log_context.set_trace_id(previous[0])
    log_context.set_user(previous[1], previous[2])


def _stream_of(parts):
    """构造一个与真实 LLM 同形的 stream_chat：忽略入参，异步产出分片。"""

    async def _gen(*_args, **_kwargs):
        for part in parts:
            yield part

    return _gen


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_pipeline_final_handlers_omit_exception_text(real_filtered_records, stream):
    """管线级兜底：业务状态照旧保留异常，日志只留异常类型不落正文。

    注入点放在 LLM 而不是检索器——RAG-037 之后检索故障已收敛为终态，
    不再冒泡到管线级兜底；要守住原来的兜底路径，得让故障发生在别处。
    """
    from app.agents.pipeline import AgentPipeline, AgentState

    boom = RuntimeError(EXCEPTION)
    llm = SimpleNamespace(
        chat=AsyncMock(side_effect=boom),
        stream_chat=AsyncMock(side_effect=boom),
    )
    pipeline = AgentPipeline(llm, retriever=None)
    state = AgentState(user_input=QUERY)
    if stream:
        events = [event async for event in pipeline.run_stream(state)]
        assert any(event.type == "error" for event in events)
        assert events[-1].type == "done"
    else:
        assert await pipeline.run(state) is state
    # Business error state is unchanged; only logging output is minimized.
    assert state.error == EXCEPTION
    assert state.answer
    message = assert_minimized(real_filtered_records, "app.agents.pipeline")
    assert "RuntimeError" in message


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_pipeline_retrieval_failure_converges_without_leaking_text(real_filtered_records, stream):
    """RAG-037：检索失败收敛为 unavailable，收敛点同样不得夹带异常正文。

    「检索没跑成」不能当成请求级失败，也不能退化成「查过了、没有」；
    但收敛不等于静默——日志里必须还能看出是检索故障、以及故障类型。
    """
    from app.agents.pipeline import AgentPipeline, AgentState

    retriever = SimpleNamespace(retrieve=AsyncMock(side_effect=RuntimeError(EXCEPTION)))
    llm = SimpleNamespace(chat=AsyncMock(return_value="YES"), stream_chat=_stream_of(["YES"]))
    pipeline = AgentPipeline(llm, retriever=retriever)
    state = AgentState(user_input=QUERY)
    if stream:
        events = [event async for event in pipeline.run_stream(state)]
        assert not any(event.type == "error" for event in events)
        assert events[-1].type == "done"
    else:
        assert await pipeline.run(state) is state
    retriever.retrieve.assert_awaited_once()
    # 收敛后的业务状态：检索终态为 unavailable，管线本身没有失败。
    assert state.retrieval_status == "unavailable"
    assert state.error is None
    assert state.answer
    message = assert_minimized(real_filtered_records, "app.agents.pipeline")
    assert "RuntimeError" in message
    assert "unavailable" in message


@pytest.mark.asyncio
async def test_real_httpx_embedding_request_log_omits_configured_url(monkeypatch, real_filtered_records):
    from app.rag.embeddings import openai_compatible

    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.9]}]})

    # Inject only a transport. Real AsyncClient.request/send produce the actual
    # httpx INFO event that leaks the configured URL in the regression baseline.
    real_client = httpx.AsyncClient

    def client_with_transport(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(respond)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(openai_compatible.httpx, "AsyncClient", client_with_transport)
    provider = OpenAICompatibleEmbeddingProvider(
        f"https://invalid.example/{CONFIG}", "synthetic-key", "synthetic-model", dim=2,
        input_policy=EmbeddingInputPolicy(
            max_input_tokens=8192, counter=len, counting_method="synthetic-test", source="fixture",
        ),
    )
    assert await provider.embed([QUERY]) == [[0.1, 0.9]]
    assert len(requests) == 1
    assert CONFIG in str(requests[0].url)
    assert json.loads(requests[0].content)["input"] == [QUERY]
    message = assert_minimized(real_filtered_records, "httpx")
    assert "POST" in message
    assert "200" in message
