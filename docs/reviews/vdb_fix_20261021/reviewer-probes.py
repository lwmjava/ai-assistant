"""Independent domain-entry probes; never loads workspace secrets."""

def main():
    import os
    import runpy

    os.environ['DATABASE_URL'] = 'sqlite:///./data/vdb-reviewer-20261021.db'
    runpy.run_path('tests/conftest.py')

    from sqlmodel import Session, SQLModel, create_engine

    from app.rag.index_identity import EmbeddingIndexIdentity
    from app.rag.index_registry import (
        IndexUnavailableError,
        activate_index,
        adopt_legacy_chunks,
        ensure_index,
        resolve_read_index_for,
        resolve_write_index,
    )
    from app.rag.vectorstore import runtime

    engine = create_engine('sqlite://')
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        a = runtime.RuntimeChoice('standalone', 'http://127.0.0.1:19530', 'ready')
        b = runtime.RuntimeChoice('standalone', 'http://127.0.0.1:19531', 'ready')
        identity = EmbeddingIndexIdentity('milvus', 'synthetic', 'review', 2)
        runtime._LOCKED = a
        row = ensure_index(session, identity)
        row.status = 'active'
        session.add(row)
        target = ensure_index(session, EmbeddingIndexIdentity('milvus', 'synthetic', 'next', 2))
        session.commit()
        runtime._LOCKED = b
        try:
            resolve_read_index_for(session, identity, backend='milvus')
            print('read wrong instance: ACCEPTED')
        except IndexUnavailableError:
            print('read wrong instance: rejected')
        try:
            result = resolve_write_index(session, identity)
            print('resolve_write wrong instance: ACCEPTED', result.id == row.id)
        except IndexUnavailableError:
            print('resolve_write wrong instance: rejected')
        try:
            result = activate_index(session, target.id, allow_empty=True)
            print('activate wrong instance: ACCEPTED', result.status, 'old=', session.get(type(row), row.id).status)
        except IndexUnavailableError:
            print('activate wrong instance: rejected')
        assert session.get(type(row), row.id).status == 'active'
        assert session.get(type(target), target.id).status == 'preparing'
        previous_notes = target.notes
        try:
            adopt_legacy_chunks(session, target, evidence='synthetic-review-evidence')
            raise AssertionError('wrong-instance adoption accepted')
        except IndexUnavailableError:
            print('adopt wrong instance: rejected')
        session.refresh(target)
        assert target.notes == previous_notes
    runtime.reset_runtime_for_tests()

    from pymilvus.exceptions import ConnectionConfigException
    configuration_error = ConnectionConfigException(message='invalid connection configuration')
    classified = runtime.classify_probe_error(configuration_error)
    print('SDK ConnectionConfigException classification:', classified)
    try:
        selected = runtime.choose_runtime(
            env='development', configured_uri='http://127.0.0.1:19530',
            lite_uri='data/reviewer-synthetic.db', fallback_enabled=True, persisted=None,
            probe=lambda _: runtime.ProbeReport(classified), lite_supported=True,
        )
        raise AssertionError('SDK configuration error selected ' + selected.mode)
    except runtime.RuntimeSelectionError:
        print('SDK configuration error selection: rejected')


if __name__ == '__main__':
    main()
