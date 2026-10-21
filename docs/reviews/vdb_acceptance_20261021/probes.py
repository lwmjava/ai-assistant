"""Offline acceptance counterexamples; never loads workspace .env."""

def main():
    import os
    import runpy

    os.environ['DATABASE_URL'] = 'sqlite:///./data/vdb-acceptance-probes-20261021.db'
    runpy.run_path('tests/conftest.py')

    from app.rag.vectorstore.runtime import ProbeReport, RuntimeChoice, choose_runtime

    saved = RuntimeChoice('lite', 'data/synthetic-lite.db', 'startup_fallback:unreachable')
    for name, changes in [
        ('disabled_fallback', {'fallback_enabled': False}),
        ('explicit_remote', {'configured_uri': 'http://synthetic.invalid:19530'}),
        ('missing_lite_package', {'lite_supported': False}),
    ]:
        params = dict(env='development', configured_uri='http://127.0.0.1:19530',
                      lite_uri='data/synthetic-lite.db', fallback_enabled=True,
                      persisted=saved, probe=lambda _: ProbeReport('ready'), lite_supported=True)
        params.update(changes)
        print(name, '=>', choose_runtime(**params).mode)

    from sqlmodel import Session, SQLModel, create_engine

    from app.rag.index_identity import EmbeddingIndexIdentity
    from app.rag.index_registry import ensure_index, resolve_read_index_for
    from app.rag.vectorstore import runtime

    db = create_engine('sqlite://')
    SQLModel.metadata.create_all(db)
    with Session(db) as session:
        identity = EmbeddingIndexIdentity('milvus', 'synthetic', 'acceptance', 2)
        runtime._LOCKED = RuntimeChoice('standalone', 'http://127.0.0.1:19530', 'ready')
        index = ensure_index(session, identity)
        index.status = 'active'
        session.add(index)
        session.commit()
        runtime._LOCKED = RuntimeChoice('standalone', 'http://synthetic.invalid:19530', 'ready')
        found = resolve_read_index_for(session, identity, backend='milvus')
        print('different_standalone_endpoint => accepted_index', found is not None)
    runtime.reset_runtime_for_tests()

    import asyncio
    from unittest.mock import patch

    from app.main import app, lifespan

    async def startup_failure():
        with patch('app.main.validate_jwt_secret', return_value=True), \
             patch('app.main.init_db'), \
             patch.object(runtime, 'ensure_milvus_runtime',
                          side_effect=runtime.RuntimeSelectionError('synthetic unready')):
            try:
                async with lifespan(app):
                    print('lifespan => entered')
            except runtime.RuntimeSelectionError:
                print('lifespan => aborted_before_serving_any_request')

    asyncio.run(startup_failure())

    import importlib.metadata
    import importlib.util
    import platform
    import socket
    print('platform', platform.platform())
    print('python', platform.python_version())
    for package in ('pymilvus', 'pytest', 'mypy', 'ruff', 'langgraph', 'sqlmodel'):
        try:
            print(package, importlib.metadata.version(package))
        except importlib.metadata.PackageNotFoundError:
            print(package, 'NOT INSTALLED')
    print('milvus_lite_installed', importlib.util.find_spec('milvus_lite') is not None)
    with socket.socket() as probe:
        probe.settimeout(2)
        print('loopback_19530_connect_ex', probe.connect_ex(('127.0.0.1', 19530)))


if __name__ == '__main__':
    main()
