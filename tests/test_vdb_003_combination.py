"""默认 Milvus 与 Lite 的组合验收。

这里把默认配置、空库、启动选择、归属、检索不可用和非检索隔离串在同一组入口上。
注入的探测只证明规则，不代表真实 Standalone 或 Lite 已经跑通。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import Settings, settings
from app.rag.index_identity import EmbeddingIndexIdentity
from app.rag.index_registry import (
    IndexUnavailableError,
    _vector_is_probeable,
    ensure_index,
    resolve_read_index_for,
)
from app.rag.retrieval_status import RetrievalStatus
from app.rag.vectorstore import runtime
from app.rag.vectorstore.factory import get_vector_store
from app.rag.vectorstore.runtime import (
    ProbeReport,
    RuntimeSelectionError,
    choose_runtime,
    classify_probe_error,
    current_runtime,
    ensure_milvus_runtime,
    reset_runtime_for_tests,
)
from app.services.chat_retrieval import ChatRetriever
from scripts.check_production_compose import check


@pytest.fixture(autouse=True)
def _clear_runtime(monkeypatch):
    # 注入选择规则不冒充真实Lite文件连接。
    monkeypatch.setattr(runtime, "prepare_lite_runtime", lambda *a, **k: None)
    reset_runtime_for_tests()
    yield
    reset_runtime_for_tests()


@pytest.fixture
def session(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "combination.db").as_posix())
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


def test_default_configuration_is_milvus_and_unknown_value_is_rejected() -> None:
    assert Settings.model_fields["RAG_VECTOR_STORE"].default == "milvus"
    previous = settings.RAG_VECTOR_STORE
    settings.RAG_VECTOR_STORE = "chroma"
    try:
        with pytest.raises(ValueError, match="milvus"):
            get_vector_store(None)  # type: ignore[arg-type]
    finally:
        settings.RAG_VECTOR_STORE = previous


def test_empty_library_is_not_unavailable(session: Session) -> None:
    runtime._LOCKED = runtime.RuntimeChoice("lite", "data/milvus-lite/dev.db", "startup_fallback:unreachable")
    assert resolve_read_index_for(session, None, backend="milvus") is None


def test_restart_keeps_lite_after_standalone_recovers(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RAG_VECTOR_STORE", "milvus")
    monkeypatch.setattr(settings, "ENV", "development")
    monkeypatch.setattr(settings, "MILVUS_URI", "http://127.0.0.1:19530")
    monkeypatch.setattr(settings, "MILVUS_LITE_FALLBACK", True)
    monkeypatch.setattr(settings, "MILVUS_LITE_PATH", "data/milvus-lite/dev.db")
    reports = iter((ProbeReport("unreachable"), ProbeReport("ready")))

    def probe(uri: str, token: str, timeout: float) -> ProbeReport:
        return next(reports)

    monkeypatch.setattr(runtime, "probe_configured_server", probe)
    monkeypatch.setattr(runtime, "lite_package_installed", lambda: True)
    first = ensure_milvus_runtime(session)
    assert first is not None and first.mode == "lite"
    reset_runtime_for_tests()
    second = ensure_milvus_runtime(session)
    assert second is not None and second.mode == "lite"
    assert second.reason == first.reason


def test_production_timeout_does_not_select_lite() -> None:
    with pytest.raises(RuntimeSelectionError, match="禁止"):
        choose_runtime(
            env="production",
            configured_uri="http://127.0.0.1:19530",
            lite_uri="data/milvus-lite/dev.db",
            fallback_enabled=True,
            persisted=None,
            probe=lambda uri: ProbeReport("unready"),
            lite_supported=True,
        )


def test_ownership_mismatch_rejects_before_empty_result(session: Session) -> None:
    identity = EmbeddingIndexIdentity("milvus", "synthetic", "combination", 2, index_version="1")
    index = ensure_index(session, identity)
    index.status = "active"
    session.add(index)
    session.commit()
    runtime._LOCKED = runtime.RuntimeChoice("lite", "data/milvus-lite/dev.db", "startup_fallback:unreachable")
    with pytest.raises(IndexUnavailableError, match="拒绝跨实例检索"):
        resolve_read_index_for(session, identity, backend="milvus")


def test_creating_retriever_does_not_touch_vector_store() -> None:
    calls: list[str] = []

    def factory():
        calls.append("connect")
        raise ConnectionError("milvus down")

    retriever = ChatRetriever(factory)
    assert calls == []
    assert retriever.last_status == RetrievalStatus.NO_HIT


@pytest.mark.asyncio
async def test_retrieval_failure_is_unavailable_not_no_hit() -> None:
    retriever = ChatRetriever(lambda: (_ for _ in ()).throw(ConnectionError("refused")))
    text = await retriever.retrieve("合成问题", "plan")
    assert text == ""
    assert retriever.last_status == RetrievalStatus.UNAVAILABLE


def test_zero_and_non_finite_vectors_are_not_probe_successes() -> None:
    assert _vector_is_probeable([0.0, 0.0], 2) is False
    assert _vector_is_probeable([float("nan"), 1.0], 2) is False
    assert _vector_is_probeable([float("inf"), 1.0], 2) is False


def test_timeout_stays_unready_and_failed_startup_does_not_switch_store(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert classify_probe_error(TimeoutError("timed out")) == "unready"
    monkeypatch.setattr(settings, "RAG_VECTOR_STORE", "milvus")
    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr(settings, "MILVUS_URI", "http://127.0.0.1:19530")
    monkeypatch.setattr(settings, "MILVUS_LITE_FALLBACK", True)
    monkeypatch.setattr(runtime, "probe_configured_server", lambda uri, token, timeout: ProbeReport("unready"))
    monkeypatch.setattr(runtime, "lite_package_installed", lambda: True)
    with pytest.raises(RuntimeSelectionError, match="禁止"):
        ensure_milvus_runtime(session)
    assert current_runtime() is None
    assert settings.RAG_VECTOR_STORE == "milvus"
    assert runtime.load_persisted_runtime(session) is None


def test_production_compose_contract_requires_milvus() -> None:
    errors = check(Path(__file__).resolve().parents[1])
    assert errors == []
