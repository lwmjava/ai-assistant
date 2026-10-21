"""Acceptance regressions through lifecycle and index operation boundaries."""

import sys
import types
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import settings
from app.rag.index_identity import EmbeddingIndexIdentity
from app.rag.index_registry import IndexUnavailableError, ensure_index, resolve_read_index_for
from app.rag.vectorstore import runtime
from app.rag.vectorstore.milvus import MilvusVectorStore


@pytest.fixture(autouse=True)
def clear_runtime():
    runtime.reset_runtime_for_tests()
    yield
    runtime.reset_runtime_for_tests()


@pytest.fixture
def session(tmp_path):
    engine = create_engine('sqlite:///' + (tmp_path / 'acceptance.db').as_posix())
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


def test_startup_failure_keeps_non_retrieval_api_available(monkeypatch, session):
    from app.main import app

    monkeypatch.setattr(settings, 'RAG_VECTOR_STORE', 'milvus')
    monkeypatch.setattr(settings, 'ENV', 'development')
    monkeypatch.setattr(settings, 'MILVUS_URI', 'http://127.0.0.1:19530')
    monkeypatch.setattr('app.main.validate_jwt_secret', lambda: True)
    monkeypatch.setattr('app.main.ensure_initial_admin', lambda _: None)
    probe = Mock(return_value=runtime.ProbeReport('unreachable'))
    monkeypatch.setattr(runtime, 'probe_configured_server', probe)
    monkeypatch.setattr(runtime, 'lite_package_installed', lambda: False)
    for module, function in [
        ('app.workflow.scheduler', 'start_scheduler'),
        ('app.workflow.scheduler', 'stop_scheduler'),
        ('app.rag.import_scheduler', 'start_scheduler'),
        ('app.rag.import_scheduler', 'stop_scheduler'),
        ('app.evolution.scheduler', 'start_scheduler'),
        ('app.evolution.scheduler', 'stop_scheduler'),
        ('app.services.agent_state_cleanup', 'start_cleanup'),
        ('app.services.agent_state_cleanup', 'stop_cleanup'),
    ]:
        monkeypatch.setattr(f'{module}.{function}', AsyncMock())
    with TestClient(app) as client:
        assert client.get('/').status_code == 200
        assert client.get('/api/v1/auth/me').status_code == 401
        health = client.get('/api/v1/health')
        assert health.status_code == 503
        assert health.json()['checks']['vector_store']['runtime'] == 'unavailable'
        for _ in range(2):
            with pytest.raises(runtime.RuntimeSelectionError):
                runtime.resolved_uri()
            with pytest.raises((runtime.RuntimeSelectionError, IndexUnavailableError)):
                resolve_read_index_for(session, backend='milvus')
        assert probe.call_count == 1
        assert runtime.current_runtime() is None


@pytest.mark.parametrize('changes', [
    {'fallback_enabled': False},
    {'configured_uri': 'http://synthetic.invalid:19530'},
    {'lite_supported': False},
    {'env': 'prod'},
    {'env': 'production'},
    {'lite_uri': 'data/another-lite.db'},
])
def test_persisted_lite_cannot_bypass_current_restrictions(changes):
    saved = runtime.RuntimeChoice('lite', 'data/synthetic-lite.db', 'startup_fallback:unreachable')
    probe = Mock(return_value=runtime.ProbeReport('ready'))
    params = dict(env='development', configured_uri='http://127.0.0.1:19530',
                  lite_uri='data/synthetic-lite.db', fallback_enabled=True,
                  persisted=saved, probe=probe, lite_supported=True)
    params.update(changes)
    with pytest.raises(runtime.RuntimeSelectionError):
        runtime.choose_runtime(**params)
    probe.assert_not_called()


def test_persisted_standalone_rejects_different_endpoint():
    with pytest.raises(runtime.RuntimeSelectionError):
        runtime.choose_runtime(
            env='development', configured_uri='http://synthetic.invalid:19530',
            lite_uri='data/synthetic-lite.db', fallback_enabled=True,
            persisted=runtime.RuntimeChoice('standalone', 'http://127.0.0.1:19530', 'ready'),
            probe=lambda _: runtime.ProbeReport('ready'), lite_supported=True,
        )


@pytest.mark.parametrize('mode,first,second', [
    ('standalone', 'http://127.0.0.1:19530', 'http://synthetic.invalid:19530'),
    ('lite', 'data/first.db', 'data/second.db'),
])
def test_same_mode_different_instance_rejects_read_and_write(session, mode, first, second):
    identity = EmbeddingIndexIdentity('milvus', 'synthetic', 'acceptance', 2)
    runtime._LOCKED = runtime.RuntimeChoice(mode, first, 'ready')
    index = ensure_index(session, identity)
    index.status = 'active'
    session.add(index)
    session.commit()
    index_id = index.id
    with Session(session.get_bind()) as fresh:
        assert resolve_read_index_for(fresh, identity, backend='milvus').id == index_id
    runtime._LOCKED = runtime.RuntimeChoice(mode, second, 'ready')
    with pytest.raises(IndexUnavailableError):
        resolve_read_index_for(session, identity, backend='milvus')
    with pytest.raises(IndexUnavailableError):
        ensure_index(session, identity)
    store = MilvusVectorStore(session)
    with pytest.raises(IndexUnavailableError):
        store.validate_add([], identity, target_index_id=index_id)
    with pytest.raises(IndexUnavailableError):
        store._connect(identity, index=index)


@pytest.mark.asyncio
async def test_wrong_instance_delete_is_rejected_before_remote_call(session, monkeypatch):
    identity = EmbeddingIndexIdentity('milvus', 'synthetic', 'delete-boundary', 2)
    runtime._LOCKED = runtime.RuntimeChoice('lite', 'data/first.db', 'ready')
    index = ensure_index(session, identity)
    index.status = 'active'
    session.add(index)
    session.commit()
    runtime._LOCKED = runtime.RuntimeChoice('lite', 'data/second.db', 'ready')
    store = MilvusVectorStore(session)
    connect = Mock(side_effect=AssertionError('must reject before SDK'))
    monkeypatch.setattr(store, '_connect', connect)
    with pytest.raises(IndexUnavailableError):
        await store.delete_by_document('synthetic-doc', 'synthetic-tenant',
                                       identity=identity, target_index_id=index.id)
    connect.assert_not_called()


def test_unknown_legacy_instance_is_not_adopted(session):
    identity = EmbeddingIndexIdentity('milvus', 'synthetic', 'legacy-boundary', 2)
    index = ensure_index(session, identity)
    index.status = 'active'
    session.add(index)
    session.commit()
    runtime._LOCKED = runtime.RuntimeChoice('standalone', 'http://127.0.0.1:19530', 'ready')
    with pytest.raises(IndexUnavailableError, match='历史归属未核验'):
        resolve_read_index_for(session, identity, backend='milvus')
    assert runtime.storage_for_index(session, index.id) is None


def test_persisted_standalone_outage_never_moves_existing_data_to_lite():
    with pytest.raises(runtime.RuntimeSelectionError):
        runtime.choose_runtime(
            env='development', configured_uri='http://127.0.0.1:19530',
            lite_uri='data/synthetic-lite.db', fallback_enabled=True,
            persisted=runtime.RuntimeChoice('standalone', 'http://127.0.0.1:19530', 'ready'),
            probe=lambda _: runtime.ProbeReport('unready'), lite_supported=True,
        )


@pytest.mark.parametrize('uri', [
    'http://synthetic-user:synthetic-password@127.0.0.1:19530',
    'http://127.0.0.1:19530?token=synthetic-token',
    'http://127.0.0.1:invalid',
])
def test_server_uri_rejects_credentials_and_invalid_port_without_exposing_them(uri):
    with pytest.raises(runtime.RuntimeSelectionError) as error:
        runtime.canonical_location('standalone', uri)
    assert 'synthetic-password' not in str(error.value)
    assert 'synthetic-token' not in str(error.value)


def test_runtime_identity_is_canonical_and_does_not_contain_location():
    first = runtime.RuntimeChoice('standalone', 'http://localhost:19530/', 'ready')
    second = runtime.RuntimeChoice('standalone', 'http://127.0.0.1:19530', 'ready')
    assert first.instance_id == second.instance_id
    assert 'localhost' not in first.instance_id


def test_cache_identity_distinguishes_same_mode_instances():
    from app.rag.cache import _storage_mode

    runtime._LOCKED = runtime.RuntimeChoice('lite', 'data/first.db', 'ready')
    first = _storage_mode()
    runtime._LOCKED = runtime.RuntimeChoice('lite', 'data/second.db', 'ready')
    assert _storage_mode() != first


def test_wrong_instance_write_resolution_and_activation_keep_old_active(session):
    from app.rag.index_registry import activate_index, active_index, resolve_write_index

    first = EmbeddingIndexIdentity('milvus', 'synthetic', 'active-boundary', 2)
    second = EmbeddingIndexIdentity('milvus', 'synthetic', 'target-boundary', 2)
    runtime._LOCKED = runtime.RuntimeChoice('standalone', 'http://127.0.0.1:19530', 'ready')
    old = ensure_index(session, first)
    old.status = 'active'
    session.add(old)
    session.commit()
    runtime._LOCKED = runtime.RuntimeChoice('standalone', 'http://synthetic.invalid:19530', 'ready')
    target = ensure_index(session, second)
    session.commit()
    with pytest.raises(IndexUnavailableError):
        resolve_write_index(session, first)
    runtime._LOCKED = runtime.RuntimeChoice('standalone', 'http://127.0.0.1:19530', 'ready')
    with pytest.raises(IndexUnavailableError):
        activate_index(session, target.id, allow_empty=True)
    assert active_index(session, 'milvus').id == old.id
    assert session.get(type(target), target.id).status == 'preparing'


def test_wrong_instance_legacy_adoption_does_not_modify_index(session):
    from app.rag.index_registry import adopt_legacy_chunks

    identity = EmbeddingIndexIdentity('milvus', 'synthetic', 'adopt-boundary', 2)
    runtime._LOCKED = runtime.RuntimeChoice('lite', 'data/first.db', 'ready')
    index = ensure_index(session, identity)
    session.commit()
    notes = index.notes
    runtime._LOCKED = runtime.RuntimeChoice('lite', 'data/second.db', 'ready')
    with pytest.raises(IndexUnavailableError):
        adopt_legacy_chunks(session, index, evidence='synthetic-acceptance-only')
    session.refresh(index)
    assert index.notes == notes


def test_sdk_configuration_error_never_selects_lite():
    class ConnectionConfigException(Exception):  # noqa: N818 - 名称必须与 pymilvus 异常一致
        """与 pymilvus 2.5.11 的配置异常同名，分类只看类型名和消息。"""

        def __init__(self, message: str) -> None:
            super().__init__(message)

    error = ConnectionConfigException(message='invalid connection configuration')
    kind = runtime.classify_probe_error(error)
    assert kind == 'config'
    with pytest.raises(runtime.RuntimeSelectionError):
        runtime.choose_runtime(
            env='development', configured_uri='http://127.0.0.1:19530',
            lite_uri='data/synthetic-lite.db', fallback_enabled=True, persisted=None,
            probe=lambda _: runtime.ProbeReport(kind), lite_supported=True,
        )


@pytest.mark.asyncio
async def test_empty_milvus_search_is_no_hit_without_connecting(session, monkeypatch):
    runtime._LOCKED = runtime.RuntimeChoice('standalone', 'http://127.0.0.1:19530', 'ready')
    store = MilvusVectorStore(session)
    connect = Mock(side_effect=AssertionError('empty library must not connect'))
    monkeypatch.setattr(store, '_connect', connect)
    assert await store.hybrid_search([1.0, 0.0], ['acceptance'], 'synthetic-tenant', 5) == []
    connect.assert_not_called()


def _install_fake_pymilvus(monkeypatch, *, connect, disconnect, list_collections, get_server_version=None):
    """CI 不安装可选的 pymilvus。注入同名模块，让导入发生在被测函数内部。"""
    module = types.ModuleType('pymilvus')
    module.connections = types.SimpleNamespace(connect=connect, disconnect=disconnect)
    module.utility = types.SimpleNamespace(
        list_collections=list_collections,
        get_server_version=get_server_version or (lambda **_: 'v2.5.11'),
    )
    monkeypatch.setitem(sys.modules, 'pymilvus', module)


def test_lite_prepare_creates_parent_before_sdk_and_rejects_lost_persisted_file(tmp_path, monkeypatch):
    path = tmp_path / 'new-parent' / 'nested' / 'probe.db'
    choice = runtime.RuntimeChoice('lite', str(path), 'startup_fallback:unreachable')
    calls = []

    def connect(**kwargs):
        assert path.parent.is_dir()
        calls.append(kwargs['uri'])

    _install_fake_pymilvus(
        monkeypatch,
        connect=connect,
        disconnect=lambda _: None,
        list_collections=lambda **_: [],
    )
    runtime.prepare_lite_runtime(choice, persisted=False)
    assert calls == [str(path)]
    with pytest.raises(runtime.RuntimeSelectionError):
        runtime.prepare_lite_runtime(choice, persisted=True)
    assert calls == [str(path)]


def test_lite_health_uses_supported_readonly_probe(monkeypatch):
    from app.api.routes.health import _probe_milvus

    runtime._LOCKED = runtime.RuntimeChoice('lite', 'data/synthetic-lite.db', 'ready')
    version = Mock(side_effect=NotImplementedError('Lite GetVersion unsupported'))
    listed = Mock(return_value=[])
    _install_fake_pymilvus(
        monkeypatch,
        connect=lambda **_: None,
        disconnect=lambda _: None,
        list_collections=listed,
        get_server_version=version,
    )
    _probe_milvus()
    version.assert_not_called()
    listed.assert_called_once()


@pytest.mark.parametrize('missing_sdk', [False, True])
def test_lite_import_or_cleanup_failure_is_isolated_without_persisting(
    session, tmp_path, monkeypatch, missing_sdk,
):
    import builtins

    monkeypatch.setattr(settings, 'RAG_VECTOR_STORE', 'milvus')
    monkeypatch.setattr(settings, 'ENV', 'development')
    monkeypatch.setattr(settings, 'MILVUS_URI', 'http://127.0.0.1:19530')
    monkeypatch.setattr(settings, 'MILVUS_LITE_PATH', str(tmp_path / 'nested' / 'probe.db'))
    monkeypatch.setattr(settings, 'MILVUS_LITE_FALLBACK', True)
    monkeypatch.setattr(runtime, 'probe_configured_server', lambda *a: runtime.ProbeReport('unreachable'))
    monkeypatch.setattr(runtime, 'lite_package_installed', lambda: True)
    if missing_sdk:
        original = builtins.__import__

        def import_module(name, *args, **kwargs):
            if name == 'pymilvus' or name.startswith('pymilvus.'):
                raise ModuleNotFoundError('synthetic missing SDK')
            return original(name, *args, **kwargs)

        monkeypatch.setattr(builtins, '__import__', import_module)
        monkeypatch.delitem(sys.modules, 'pymilvus', raising=False)
    else:
        _install_fake_pymilvus(
            monkeypatch,
            connect=Mock(side_effect=OSError('synthetic connect error')),
            disconnect=Mock(side_effect=RuntimeError('synthetic cleanup error')),
            list_collections=lambda **_: [],
        )
    runtime.initialize_milvus_runtime(session)
    assert runtime.runtime_failure() is not None
    assert runtime.current_runtime() is None
    assert runtime.load_persisted_runtime(session) is None
    with pytest.raises(runtime.RuntimeSelectionError):
        runtime.resolved_uri()


def test_unknown_backend_is_unavailable_never_reported_as_local(session, monkeypatch):
    from app.api.routes.health import health_check

    monkeypatch.setattr(settings, 'RAG_VECTOR_STORE', 'unsupported-synthetic')
    runtime.initialize_milvus_runtime(session)
    assert runtime.runtime_failure() is not None
    assert runtime.load_persisted_runtime(session) is None
    response = health_check()
    import json

    body = json.loads(response.body)
    assert response.status_code == 503
    assert body['checks']['vector_store']['backend'] == 'unknown'
    assert body['checks']['vector_store']['status'] == 'error'


def test_unknown_backend_cannot_create_local_index_identity(monkeypatch):
    from app.rag.index_identity import current_backend

    monkeypatch.setattr(settings, 'RAG_VECTOR_STORE', 'unsupported-synthetic')
    with pytest.raises(ValueError):
        current_backend()
