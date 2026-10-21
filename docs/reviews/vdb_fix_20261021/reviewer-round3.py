"""Independent Lite preparation failure-boundary checks."""


def main():
    import os
    import runpy
    from unittest.mock import patch

    os.environ['DATABASE_URL'] = 'sqlite:///./data/vdb-reviewer-round3.db'
    runpy.run_path('tests/conftest.py')

    from pymilvus import connections
    from sqlmodel import Session, SQLModel, create_engine

    from app.core.config import settings
    from app.rag.vectorstore import runtime

    engine = create_engine('sqlite://')
    SQLModel.metadata.create_all(engine)
    for disconnect_error in (None, RuntimeError('synthetic disconnect failure')):
        runtime.reset_runtime_for_tests()
        with Session(engine) as session, \
                patch.object(settings, 'RAG_VECTOR_STORE', 'milvus'), \
                patch.object(settings, 'ENV', 'development'), \
                patch.object(settings, 'MILVUS_URI', 'http://127.0.0.1:19530'), \
                patch.object(settings, 'MILVUS_LITE_FALLBACK', True), \
                patch.object(settings, 'MILVUS_LITE_PATH', 'data/t/reviewer-round3/lite.db'), \
                patch.object(runtime, 'probe_configured_server', return_value=runtime.ProbeReport('unreachable')), \
                patch.object(runtime, 'lite_package_installed', return_value=True), \
                patch.object(connections, 'connect', side_effect=OSError('synthetic open failure')), \
                patch.object(connections, 'disconnect', side_effect=disconnect_error):
            try:
                runtime.initialize_milvus_runtime(session)
                print('disconnect_error', disconnect_error is not None,
                      '=> isolated', runtime.runtime_failure(),
                      'binding', runtime.load_persisted_runtime(session))
            except RuntimeError as exc:
                print('disconnect_error', disconnect_error is not None,
                      '=> ESCAPED', type(exc).__name__, 'binding', runtime.load_persisted_runtime(session))
    runtime.reset_runtime_for_tests()

    import builtins
    original_import = builtins.__import__

    def import_without_sdk(name, *args, **kwargs):
        if name == 'pymilvus':
            raise ImportError('synthetic SDK missing')
        return original_import(name, *args, **kwargs)

    try:
        with patch('builtins.__import__', side_effect=import_without_sdk):
            runtime.prepare_lite_runtime(runtime.RuntimeChoice('lite', 'data/t/reviewer-round3/import.db', ''),
                                         persisted=False)
    except Exception as exc:
        print('missing SDK =>', type(exc).__name__)

    from pathlib import Path

    missing = Path('data/t/reviewer-round3/lost-persisted.db')
    assert not missing.exists()
    with patch.object(connections, 'connect') as connect:
        try:
            runtime.prepare_lite_runtime(runtime.RuntimeChoice('lite', str(missing), ''), persisted=True)
            raise AssertionError('lost persisted file was recreated')
        except runtime.RuntimeSelectionError:
            connect.assert_not_called()
            assert not missing.exists()
            print('lost persisted file => rejected before SDK, no replacement')

    import asyncio

    from app.rag.index_identity import EmbeddingIndexIdentity
    from app.rag.index_registry import IndexUnavailableError, ensure_index
    from app.rag.vectorstore.milvus import MilvusVectorStore

    with Session(engine) as session:
        runtime._LOCKED = runtime.RuntimeChoice('standalone', 'http://127.0.0.1:19530', 'ready')
        assert asyncio.run(MilvusVectorStore(session).hybrid_search([1.0, 0.0], ['synthetic'], 'tenant', 5)) == []
        print('actual empty library => []')
        index = ensure_index(session, EmbeddingIndexIdentity('milvus', 'synthetic', 'round3', 2))
        index.status = 'active'
        session.add(index)
        session.commit()
        runtime._LOCKED = runtime.RuntimeChoice('standalone', 'http://127.0.0.1:19531', 'ready')
        try:
            asyncio.run(MilvusVectorStore(session).hybrid_search([1.0, 0.0], ['synthetic'], 'tenant', 5))
            raise AssertionError('wrong instance swallowed as empty')
        except IndexUnavailableError:
            print('wrong instance => explicit unavailable')
    runtime.reset_runtime_for_tests()


if __name__ == '__main__':
    main()
