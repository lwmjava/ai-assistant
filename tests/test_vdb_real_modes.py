"""Opt-in real Milvus lifecycle probe with synthetic embeddings, never quality metrics.

Run as a script in an isolated Linux container. Two separate processes exercise
persisted SQL/runtime/vector state; all files belong to the supplied scratch root.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
import platform
import uuid
from pathlib import Path


def isolated_imports() -> None:
    """Do not read host credentials from a mounted workspace dotenv file."""
    from pydantic_settings.sources import DotEnvSettingsSource

    DotEnvSettingsSource._read_env_file = lambda self, file_path: {}
    for key in ('LLM_API_KEY', 'LLM_INTENT_API_KEY', 'LLM_FALLBACK_API_KEY',
                'LLM_RERANK_API_KEY', 'EMBEDDING_API_KEY'):
        os.environ[key] = ''


async def exercise(mode: str, uri: str, scratch: Path, phase: str) -> dict:
    isolated_imports()
    from pymilvus import Collection, connections, utility
    from sqlmodel import Session, SQLModel, col, create_engine, select

    from app.core.config import settings
    from app.core.security import Role
    from app.models.rag import Document, DocumentChunk
    from app.models.user import Tenant, User
    from app.rag.document_parsers.base import ParsedDocument
    from app.rag.embeddings.base import EmbeddingInputPolicy, EmbeddingProvider
    from app.rag.index_registry import active_index
    from app.rag.service import RAGService
    from app.rag.vectorstore import runtime

    scratch.mkdir(parents=True, exist_ok=True)
    state_file = scratch / 'probe-state.json'
    state = json.loads(state_file.read_text()) if phase != 'prepare' else {'version': uuid.uuid4().hex}
    settings.RAG_VECTOR_STORE = 'milvus'
    settings.RAG_BACKEND = 'native'
    settings.ENV = 'development'
    settings.MILVUS_URI = uri
    settings.MILVUS_TOKEN = ''
    # The first startup must create the configured parent, as a fresh default
    # data/milvus-lite directory is absent on a clean checkout as well.
    settings.MILVUS_LITE_PATH = str(scratch / 'nested' / 'milvus' / 'dev.db')
    settings.MILVUS_LITE_FALLBACK = True
    settings.MILVUS_STARTUP_PROBE_TIMEOUT_SECONDS = 2
    settings.EMBEDDING_INDEX_VERSION = state['version']
    settings.EMBEDDING_PROVIDER = 'synthetic'
    settings.RETRIEVAL_CACHE_ENABLED = False

    class SyntheticEmbedding(EmbeddingProvider):
        model = 'synthetic-vdb-lifecycle-only'
        dim = 8
        input_policy = EmbeddingInputPolicy(
            max_input_tokens=None, counting_method='offline-unlimited', source='synthetic-vdb-acceptance'
        )

        async def embed(self, texts):
            vectors = []
            for text in texts:
                raw = hashlib.sha256(text.encode()).digest()
                vectors.append([float(raw[i] + 1) for i in range(self.dim)])
            return vectors

    engine = create_engine('sqlite:///' + str(scratch / 'application.db'))
    SQLModel.metadata.create_all(engine)
    runtime.reset_runtime_for_tests()
    results = {'mode': mode, 'phase': phase, 'python': platform.python_version(),
               'platform': platform.platform(), 'pymilvus': importlib.metadata.version('pymilvus'),
               'milvus_lite': importlib.metadata.version('milvus-lite'),
               'embedding': {'provider': 'synthetic', 'model': SyntheticEmbedding.model, 'dim': 8},
               'quality_claim': False, 'checks': []}
    with Session(engine) as session:
        if phase == 'missing-file':
            assert mode == 'lite'
            original = Path(settings.MILVUS_LITE_PATH)
            preserved = original.with_suffix('.preserved')
            assert original.is_file() and not preserved.exists()
            original.rename(preserved)
            try:
                try:
                    runtime.ensure_milvus_runtime(session)
                except runtime.RuntimeSelectionError:
                    assert not original.exists() and preserved.is_file()
                    results['checks'].append('missing_persisted_lite_rejected_without_empty_replacement')
                else:
                    raise AssertionError('missing persisted Lite file must be rejected')
            finally:
                preserved.rename(original)
            engine.dispose()
            return results
        selected = runtime.ensure_milvus_runtime(session)
        assert selected is not None and selected.mode == mode, selected
        results['checks'].append('actual_startup_mode')
        provider = SyntheticEmbedding()
        if phase == 'prepare':
            for tenant_id in ('acceptance-a', 'acceptance-b'):
                session.add(Tenant(id=tenant_id, name=tenant_id))
                session.add(User(id=tenant_id, tenant_id=tenant_id, username=tenant_id,
                                 hashed_password='', role=Role.TENANT_ADMIN.value))
            session.commit()
            user = session.get(User, 'acceptance-a')
            service = RAGService(session, 'acceptance-a', embedding_provider=provider, reader=user)
            assert await service.search('alpha acceptance') == []
            document = await service.ingest_text('alpha acceptance original evidence', 'original',
                                                 'original.txt', user.id, strategy='recursive')
            foreign = RAGService(session, 'acceptance-b', embedding_provider=provider,
                                 reader=session.get(User, 'acceptance-b'))
            foreign_doc = await foreign.ingest_text('beta foreign confidential evidence', 'foreign',
                                                    'foreign.txt', 'acceptance-b', strategy='recursive')
            # Standalone defaults to bounded consistency; await visible writes
            # without changing the application's query/index/embedding settings.
            hits = []
            for attempt in range(40):
                hits = await service.search('alpha acceptance original evidence')
                if hits:
                    break
                await asyncio.sleep(0.25)
            results['initial_search_attempts'] = attempt + 1
            assert hits and all(hit.document_id == document.id for hit in hits)
            assert all(hit.document_id != foreign_doc.id for hit in hits)
            index = active_index(session, 'milvus')
            collection = Collection(index.name)
            collection.flush()
            state.update(document_id=document.id, foreign_id=foreign_doc.id, collection=index.name,
                         old_ids=[row.id for row in session.exec(select(DocumentChunk).where(
                             col(DocumentChunk.document_id) == document.id)).all()])
            state_file.write_text(json.dumps(state), encoding='utf-8')
            results['checks'] += ['empty_library', 'first_ingest_registered_written_searchable', 'tenant_isolation']
        else:
            user = session.get(User, 'acceptance-a')
            service = RAGService(session, 'acceptance-a', embedding_provider=provider, reader=user)
            hits = await service.search('alpha acceptance original evidence')
            assert hits and all(hit.document_id == state['document_id'] for hit in hits)
            results['checks'].append('new_process_vector_and_sql_persistence')
            document = session.get(Document, state['document_id'])
            parsed = ParsedDocument(text='gamma acceptance reparsed evidence', title='reparsed',
                                    source='original.txt', extension='.txt', content_type='text/plain')
            await service.reindex_document_in_place(document, parsed, content_hash='synthetic-reparse')
            collection = Collection(state['collection'])
            collection.flush()
            expr = 'document_id == "' + state['document_id'] + '"'
            rows = collection.query(expr, output_fields=['id'], consistency_level='Strong')
            new_ids = {row['id'] for row in rows}
            assert new_ids and not new_ids.intersection(state['old_ids'])
            hits = await service.search('gamma acceptance reparsed evidence')
            assert hits and all('gamma' in hit.content and 'original evidence' not in hit.content for hit in hits)
            results['checks'].append('reparse_removes_old_vectors_and_reads_new_content')
            assert await service.delete_document(document.id, user)
            assert await service.search('gamma acceptance reparsed evidence') == []
            results['checks'].append('soft_delete_no_longer_retrievable')
            count = await service._vector_store.delete_by_document(document.id, user.tenant_id)
            collection.flush()
            assert count == len(new_ids)
            assert collection.query(expr, output_fields=['id'], consistency_level='Strong') == []
            results['checks'].append('explicit_vector_delete_cleans_test_document')
            foreign = RAGService(session, 'acceptance-b', embedding_provider=provider,
                                 reader=session.get(User, 'acceptance-b'))
            assert await foreign.search('beta foreign confidential evidence')
            results['checks'].append('delete_preserves_other_tenant')
        from app.api.routes.health import _probe_milvus

        _probe_milvus()
        results['checks'].append('actual_health_connectivity_probe')
        if mode == 'standalone':
            results['server_version'] = utility.get_server_version()
        else:
            results['server_version'] = 'Lite GetVersion unsupported; package version recorded'
    connections.disconnect('default')
    engine.dispose()
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['standalone', 'lite'], required=True)
    parser.add_argument('--uri', required=True)
    parser.add_argument('--scratch', type=Path, required=True)
    parser.add_argument('--phase', choices=['prepare', 'verify', 'missing-file'], required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(exercise(args.mode, args.uri, args.scratch, args.phase)), indent=2))
