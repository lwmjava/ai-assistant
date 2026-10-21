"""开发环境在启动时选择 Milvus 实例，并拒绝跨实例读取。"""

from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import settings
from app.rag.index_identity import EmbeddingIndexIdentity
from app.rag.index_registry import IndexUnavailableError, ensure_index, resolve_read_index_for
from app.rag.vectorstore import runtime
from app.rag.vectorstore.runtime import (
    ProbeReport,
    RuntimeChoice,
    RuntimeSelectionError,
    choose_runtime,
    classify_probe_error,
    ensure_milvus_runtime,
    reset_runtime_for_tests,
)


@pytest.fixture(autouse=True)
def _clear_process_runtime(monkeypatch):
    # 本文件只验证选择规则，真实文件/SDK探针由real_modes验收。
    monkeypatch.setattr(runtime, "prepare_lite_runtime", lambda *a, **k: None)
    reset_runtime_for_tests()
    yield
    reset_runtime_for_tests()


@pytest.fixture
def session(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "runtime.db").as_posix())
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


def _choose(**overrides):
    values = {
        "env": "development",
        "configured_uri": "http://127.0.0.1:19530",
        "lite_uri": "data/milvus-lite/dev.db",
        "fallback_enabled": True,
        "persisted": None,
        "probe": lambda uri: ProbeReport("unreachable"),
        "lite_supported": True,
    }
    values.update(overrides)
    return choose_runtime(**values)


def test_ready_standalone_is_preferred() -> None:
    choice = _choose(probe=lambda uri: ProbeReport("ready"))
    assert choice.mode == "standalone"
    assert choice.reason == "standalone_ready"


def test_unreachable_local_development_selects_lite() -> None:
    choice = _choose()
    assert choice.mode == "lite"
    assert choice.reason == "startup_fallback:unreachable"


def test_timeout_is_unready_and_may_fall_back() -> None:
    assert classify_probe_error(TimeoutError()) == "unready"
    choice = _choose(probe=lambda uri: ProbeReport("unready"))
    assert choice.mode == "lite"


def test_auth_schema_and_config_errors_do_not_fall_back() -> None:
    assert classify_probe_error(RuntimeError("illegal token")) == "auth"
    assert classify_probe_error(RuntimeError("missing field index_id")) == "schema"
    for kind in ("auth", "schema", "config"):
        with pytest.raises(RuntimeSelectionError, match=kind):
            _choose(probe=lambda uri, kind=kind: ProbeReport(kind))


def test_production_and_remote_uri_reject_lite() -> None:
    with pytest.raises(RuntimeSelectionError, match="禁止"):
        _choose(env="production")
    with pytest.raises(RuntimeSelectionError, match="禁止"):
        _choose(configured_uri="http://milvus.internal:19530")


def test_missing_lite_runtime_does_not_select_local() -> None:
    with pytest.raises(RuntimeSelectionError, match="Lite"):
        _choose(lite_supported=False)


def test_persisted_lite_survives_later_standalone_recovery() -> None:
    persisted = RuntimeChoice("lite", "data/milvus-lite/dev.db", "startup_fallback:unreachable")

    def unexpected(uri: str) -> ProbeReport:
        raise AssertionError(uri)

    choice = _choose(persisted=persisted, probe=unexpected)
    assert choice == persisted


def test_process_keeps_the_first_choice(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RAG_VECTOR_STORE", "milvus")
    monkeypatch.setattr(settings, "ENV", "development")
    monkeypatch.setattr(settings, "MILVUS_URI", "http://127.0.0.1:19530")
    monkeypatch.setattr(settings, "MILVUS_LITE_FALLBACK", True)
    monkeypatch.setattr(settings, "MILVUS_LITE_PATH", "data/milvus-lite/dev.db")
    calls = {"n": 0}

    def probe(uri: str, token: str, timeout: float) -> ProbeReport:
        calls["n"] += 1
        if calls["n"] == 1:
            return ProbeReport("unreachable")
        return ProbeReport("ready")

    monkeypatch.setattr(runtime, "probe_configured_server", probe)
    monkeypatch.setattr(runtime, "lite_package_installed", lambda: True)
    first = ensure_milvus_runtime(session)
    second = ensure_milvus_runtime(session)
    assert first is not None and first.mode == "lite"
    assert second == first
    assert calls["n"] == 1
    again = runtime.load_persisted_runtime(session)
    assert again is not None and again.mode == "lite"


def test_storage_mismatch_rejects_read_instead_of_empty_hit(session: Session) -> None:
    identity = EmbeddingIndexIdentity("milvus", "synthetic", "boundary", 2, index_version="1")
    index = ensure_index(session, identity)
    index.status = "active"
    session.add(index)
    session.commit()
    runtime._LOCKED = RuntimeChoice("lite", "data/milvus-lite/dev.db", "startup_fallback:unreachable")
    with pytest.raises(IndexUnavailableError, match="属于 standalone"):
        resolve_read_index_for(session, identity, backend="milvus")


def test_matching_lite_storage_can_be_read(session: Session) -> None:
    identity = EmbeddingIndexIdentity("milvus", "synthetic", "owned", 2, index_version="2")
    runtime._LOCKED = RuntimeChoice("lite", "data/milvus-lite/dev.db", "startup_fallback:unreachable")
    index = ensure_index(session, identity)
    index.status = "active"
    session.add(index)
    session.commit()
    found = resolve_read_index_for(session, identity, backend="milvus")
    assert found is not None and found.id == index.id
