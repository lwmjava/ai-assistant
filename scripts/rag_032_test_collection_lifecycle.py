"""Verify rebuild CLI against brand-new synthetic Milvus collections; never delete them.

Default is a dry plan. --apply uses a new UUID SQLite and collection prefix, offline
synthetic embeddings, and the actual prepare/rebuild/activate/rollback CLI.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import io
import json
import os
import sys
import uuid
from pathlib import Path


def run(args: argparse.Namespace) -> dict:
    root = Path(__file__).resolve().parents[1]
    run_id = uuid.uuid4().hex
    database = root / "data" / "pytest-tmp" / f"rag032-real-{run_id}.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    assert not database.exists()
    prefix = f"rag032_synthetic_{run_id}"
    os.environ.update({
        "ENV": "development", "DATABASE_URL": "sqlite:///" + database.as_posix(),
        "RAG_VECTOR_STORE": "milvus", "RAG_BACKEND": "native",
        "MILVUS_COLLECTION": prefix, "MILVUS_URI": args.milvus_uri, "MILVUS_TOKEN": "",
        "EMBEDDING_PROVIDER": "mock", "EMBEDDING_API_KEY": "",
        "INITIAL_ADMIN_USERNAME": "", "INITIAL_ADMIN_PASSWORD": "",
    })
    sys.path.insert(0, str(root))
    from collections.abc import Sequence

    from pymilvus import Collection, connections, utility
    from sqlmodel import Session, col, select

    from app.core.database import engine, init_db
    from app.models.rag import DocumentChunk, EmbeddingIndex
    from app.rag.embeddings import factory
    from app.rag.embeddings.base import EmbeddingInputPolicy, EmbeddingProvider
    from app.rag.index_identity import identity_from_provider
    from app.rag.index_registry import active_index
    from app.rag.service import RAGService
    from app.rag.vectorstore.milvus import MilvusVectorStore
    from scripts import rebuild_embedding_index as cli

    class SyntheticProvider(EmbeddingProvider):
        def __init__(self, model: str):
            self.model = model
            self.dim = 8
            self.input_policy = EmbeddingInputPolicy(
                max_input_tokens=None, counting_method="offline-unlimited",
                source="synthetic-lifecycle-v1",
            )

        async def embed(self, texts: Sequence[str]) -> list[list[float]]:
            return [[0.1] * self.dim for _ in texts]

    old_provider = SyntheticProvider("synthetic-model-a")
    new_provider = SyntheticProvider("synthetic-model-b")
    failed_provider = SyntheticProvider("synthetic-model-c-failure")
    providers = [old_provider, new_provider, failed_provider]
    box: dict[str, EmbeddingProvider] = {"provider": old_provider}
    factory.get_embedding_provider = lambda: box["provider"]
    connections.connect(alias="default", uri=args.milvus_uri, timeout=10)
    names = [identity_from_provider(provider).collection_name() for provider in providers]
    for name in names:
        assert name.startswith(prefix) and not utility.has_collection(name), "test collection must be new"
    init_db()
    report: dict = {
        "schema_version": "rag032-real-lifecycle-v1", "run_id": run_id,
        "database": str(database), "collection_prefix": prefix,
        "embedding": {"provider": "mock/synthetic", "dim": 8, "fee": 0,
                      "purpose": "deterministic lifecycle, not retrieval quality"},
        "commands": [], "snapshots": {}, "collections_retained": names,
        "service_version": utility.get_server_version(),
    }

    def command(argv: list[str], expected: int = 0) -> None:
        output, errors = io.StringIO(), io.StringIO()
        previous = sys.argv
        try:
            sys.argv = ["rebuild_embedding_index.py", *argv]
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                code = cli.main()
        finally:
            sys.argv = previous
        report["commands"].append({"argv": argv, "exit_code": code,
                                   "stdout": output.getvalue(), "stderr": errors.getvalue()})
        assert code == expected, report["commands"][-1]

    def index_for(provider):
        with Session(engine) as session:
            row = session.exec(select(EmbeddingIndex).where(
                col(EmbeddingIndex.identity_key) == identity_from_provider(provider).key())).one()
            return row.id, row.name

    def snapshot(label, provider):
        index_id, name = index_for(provider)
        collection = Collection(name)
        collection.flush()
        collection.load()
        remote = collection.query(expr=f'index_id == "{index_id}"',
                                  output_fields=["id", "index_id", "embedding"])
        with Session(engine) as session:
            sql = session.exec(select(DocumentChunk).where(col(DocumentChunk.index_id) == index_id)).all()
            result = {"index_id": index_id, "collection": name,
                      "sql_ids": sorted(chunk.id for chunk in sql if chunk.embedding),
                      "remote_ids": sorted(row["id"] for row in remote),
                      "dimensions": sorted({len(row["embedding"]) for row in remote}),
                      "active_index": active_index(session, "milvus").id}
        report["snapshots"][label] = result
        assert result["sql_ids"] == result["remote_ids"] and result["dimensions"] == [8], result
        return result

    def hits(provider):
        with Session(engine) as session:
            service = RAGService(session, "synthetic-tenant", embedding_provider=provider)
            result = asyncio.run(service.search("synthetic evidence", top_k=5))
            return sorted(hit.id for hit in result)

    with Session(engine) as session:
        service = RAGService(session, "synthetic-tenant", embedding_provider=old_provider)
        for number in range(3):
            asyncio.run(service.ingest_text(
                f"Synthetic evidence document {number} with unique reference {number}.",
                f"synthetic-{number}", f"synthetic-{number}.txt", "synthetic-user"))
    old = snapshot("initial_a", old_provider)
    assert len(old["sql_ids"]) == 3 and hits(old_provider)
    box["provider"] = new_provider
    command(["prepare", "--json"])
    command(["rebuild", "--apply"])
    snapshot("a_during_b_preparation", old_provider)
    new = snapshot("b_prepared", new_provider)
    assert new["active_index"] == old["index_id"] and len(new["sql_ids"]) == 3
    assert hits(old_provider) == old["sql_ids"]
    # Repeat the full CLI rebuild: no SQL or remote duplication, old remains intact.
    command(["rebuild", "--apply"])
    new = snapshot("b_prepared_second_run", new_provider)
    assert len(new["sql_ids"]) == 3
    assert snapshot("a_after_repeat", old_provider)["remote_ids"] == old["remote_ids"]
    command(["activate", "--index-id", new["index_id"]])
    assert hits(new_provider) == new["sql_ids"]
    snapshot("b_active", new_provider)
    box["provider"] = old_provider
    command(["rollback", "--index-id", old["index_id"]])
    assert hits(old_provider) == old["sql_ids"]
    snapshot("a_after_rollback", old_provider)
    snapshot("b_retained_after_rollback", new_provider)
    # Actual remote writes followed by a synthetic failure; only fresh target C is touched.
    box["provider"] = failed_provider
    command(["prepare", "--json"])
    original_add = MilvusVectorStore.add

    async def partial_add(self, chunks, identity=None, **kwargs):
        await original_add(self, chunks[:1], identity, **kwargs)
        raise RuntimeError("synthetic failure after remote upsert")

    MilvusVectorStore.add = partial_add
    try:
        command(["rebuild", "--apply"], expected=1)
    finally:
        MilvusVectorStore.add = original_add
    failed_id, failed_name = index_for(failed_provider)
    with Session(engine) as session:
        failed = session.get(EmbeddingIndex, failed_id)
        detail = json.loads(failed.notes)
        assert failed.status == "failed" and detail["compensation_required"] is True
        assert detail["new_chunk_ids"] and active_index(session, "milvus").id == old["index_id"]
        report["failure_evidence"] = {"index_id": failed_id, "status": failed.status, "notes": detail}
    assert hits(old_provider) == old["sql_ids"]
    report["snapshots"]["a_after_c_failure"] = snapshot("a_after_c_failure", old_provider)
    command(["activate", "--index-id", failed_id], expected=3)
    # Explicit prepare retries C, carrying the compensation IDs until target-only cleanup.
    command(["prepare", "--json"])
    command(["rebuild", "--apply"])
    recovered = snapshot("c_retry_completed", failed_provider)
    assert len(recovered["sql_ids"]) == 3 and recovered["active_index"] == old["index_id"]
    assert hits(old_provider) == old["sql_ids"]
    report["fingerprints"] = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in [root / "app/rag/service.py", root / "app/rag/vectorstore/milvus.py",
                     root / "scripts/rebuild_embedding_index.py"]}
    report["result"] = "pass"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--milvus-uri", default="http://127.0.0.1:19530")
    parser.add_argument("--report", type=Path, default=Path("evals/reports/rag-032-real-lifecycle-20261008.json"))
    args = parser.parse_args()
    if not args.apply:
        print("Plan: new UUID SQLite and 3 synthetic test collections only; no fees, no deletion.")
        return 0
    if args.milvus_uri != "http://127.0.0.1:19530":
        parser.error("This acceptance script only permits the local test Milvus endpoint")
    report = run(args)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"result={report['result']} report={args.report} collections retained={report['collections_retained']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
