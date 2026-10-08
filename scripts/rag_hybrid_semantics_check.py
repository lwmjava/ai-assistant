"""Compare hybrid semantics with explicit synthetic vectors and retained test data."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def fixtures() -> list[dict]:
    def row(name, cosine, tokens, **kwargs):
        return {"name": name, "vector": [cosine, math.sqrt(1 - cosine**2)],
                "tokens": tokens, **kwargs}

    return [
        {"name": "empty_query", "tokens": [],
         "rows": [row("a", .9, ["apple"]), row("b", .4, ["pear"])]},
        {"name": "empty_docs", "tokens": ["apple"],
         "rows": [row("a", .9, []), row("b", .4, [])]},
        {"name": "no_overlap", "tokens": ["absent"],
         "rows": [row("a", .9, ["apple"]), row("b", .4, ["pear"])]},
        {"name": "positive_overlap", "tokens": ["pear"],
         "rows": [row("a", .9, ["apple"]), row("b", .4, ["pear"])]},
        {"name": "keyword_outside_window", "tokens": ["keyword"], "top_k": 1,
         "rows": [row(f"dense-{i:02}", .99 - i * .005, ["neutral"]) for i in range(20)]
         + [row(f"keyword-{i:02}", .7 - i * .005, ["keyword"]) for i in range(21)]},
        {"name": "visibility", "tokens": [], "uploader": "owner", "rows": [
            row("allowed", .4, ["neutral"]), row("foreign_tenant", .99, [], tenant="foreign"),
            row("other_uploader", .98, [], uploader="other"),
            row("deleted", .97, [], deleted=True), row("historical", .96, [], current=False),
            row("old_index", .95, [], old_index=True)]},
        {"name": "filtered_window_exhausted", "tokens": [], "top_k": 1, "uploader": "owner",
         "rows": [row(f"other-{i:02}", .99 - i * .005, [], uploader="other") for i in range(20)]
         + [row("allowed", .4, ["neutral"])]},
    ]


def seed(session, backend: str, cases: list[dict], run_id: str, embedding=None):
    from app.models.rag import Document, DocumentChunk
    from app.rag.index_identity import EmbeddingIndexIdentity
    from app.rag.index_registry import ensure_index

    metadata = embedding or {"provider": "synthetic-fixed", "model": "explicit-unit-vectors-v1",
                             "dim": 2}
    identity = EmbeddingIndexIdentity(backend=backend, provider=metadata["provider"],
                                     model=metadata["model"], dim=metadata["dim"],
                                     deployment=metadata.get("deployment", ""), index_version=run_id)
    index = ensure_index(session, identity)
    chunks = []
    names = {}
    for case in cases:
        for spec in case["rows"]:
            doc = Document(tenant_id=spec.get("tenant", case["name"]),
                           user_id=spec.get("uploader", "owner"), title=spec["name"],
                           is_current=spec.get("current", True),
                           deleted_at=datetime.now(UTC) if spec.get("deleted") else None)
            session.add(doc)
            session.flush()
            chunk = DocumentChunk(tenant_id=doc.tenant_id, document_id=doc.id,
                                  content=spec["name"], embedding=json.dumps(spec["vector"]),
                                  tokens=json.dumps(spec["tokens"]),
                                  index_id="retired-synthetic" if spec.get("old_index") else index.id)
            session.add(chunk)
            chunks.append(chunk)
            names[chunk.id] = spec["name"]
    session.flush()
    # Direct synthetic fixture registration isolates retrieval from index-switch workflows.
    index.status = "active"
    index.chunk_count = sum(c.index_id == index.id for c in chunks)
    session.add(index)
    session.commit()
    return identity, index, chunks, names


async def compare(session, *, fake_remote=False, cases=None, embedding=None) -> dict:
    from types import SimpleNamespace

    from app.rag.access import ReadScope
    from app.rag.vectorstore.local import LocalVectorStore
    from app.rag.vectorstore.milvus import MilvusVectorStore

    cases = cases or fixtures()
    run_id = uuid4().hex
    local_identity, _, _, local_names = seed(session, "local", cases, run_id, embedding)
    identity, index, chunks, names = seed(session, "milvus", cases, run_id, embedding)
    local = LocalVectorStore(session)
    milvus = MilvusVectorStore(session)
    if fake_remote:
        def remote(_identity, target, expr, query, expand):
            tenant = expr.split('"')[1]
            selected = [c for c in chunks if c.tenant_id == tenant and c.index_id == target.id]
            selected.sort(key=lambda c: json.loads(c.embedding)[0], reverse=True)
            return [SimpleNamespace(entity={"id": c.id}, distance=json.loads(c.embedding)[0])
                    for c in selected[:expand]]
        milvus._remote_search = remote
    else:
        await milvus.add([c for c in chunks if c.index_id == index.id], identity)
        collection = milvus._connect(identity)
        collection.flush()
        collection.load()
        # Keep an actual old-index vector as a remote-filter witness.
        stale = [c for c in chunks if c.index_id != index.id]
        if stale:
            collection.upsert([{"id": c.id, "tenant_id": c.tenant_id,
                                "document_id": c.document_id, "index_id": c.index_id,
                                "embedding": json.loads(c.embedding)} for c in stale])
            collection.flush()

    report = {"dataset_version": "hybrid-semantics-synthetic-v1", "rrf_k": 60,
              "embedding": embedding or {"provider": "synthetic-fixed",
                                         "model": "explicit-unit-vectors-v1", "dim": 2},
              "real_milvus": not fake_remote, "collection": index.name, "slices": []}
    report["dataset_sha256"] = hashlib.sha256(
        json.dumps(cases, sort_keys=True).encode("utf-8")).hexdigest()
    report["code_sha256"] = {
        path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        for path in ("app/rag/vectorstore/local.py", "app/rag/vectorstore/milvus.py",
                     "scripts/rag_hybrid_semantics_check.py")}
    for case in cases:
        top_k = case.get("top_k", 2)
        scope = ReadScope(case["name"], uploader_id=case.get("uploader"))
        kwargs = dict(query_embedding=case.get("query_vector", [1., 0.]),
                      query_tokens=case["tokens"], tenant_id=case["name"],
                      top_k=top_k, rrf_k=60, read_scope=scope)
        lhs = await local.hybrid_search(**kwargs, identity=local_identity)
        rhs = await milvus.hybrid_search(**kwargs, identity=identity)

        def encode(results, mapping):
            return [{"name": mapping[r.id], "score": r.score,
                     "similarity": r.similarity} for r in results]

        report["slices"].append({"name": case["name"], "top_k": top_k,
                                  "dense_window": max(top_k * 4, 20),
                                  "local": encode(lhs, local_names), "milvus": encode(rhs, names)})
    if not fake_remote:
        from pymilvus import utility
        report["server_version"] = utility.get_server_version()
        report["remote_count"] = milvus._connect(identity).num_entities
    report["limitations"] = ["Fixed vectors prove semantics, not embedding quality.",
                              "BM25 statistics use different candidate universes.",
                              "Post-selection SQL filtering can exhaust the dense window."]
    return report


def verify(report: dict) -> None:
    slices = {s["name"]: s for s in report["slices"]}
    for name in ("empty_query", "empty_docs", "no_overlap"):
        for backend in ("local", "milvus"):
            rows = slices[name][backend]
            assert [r["name"] for r in rows] == ["a", "b"]
            assert all(math.isclose(r["score"], 1 / (61 + i), abs_tol=1e-12)
                       for i, r in enumerate(rows))
    for backend in ("local", "milvus"):
        assert slices["positive_overlap"][backend][0]["score"] > 1 / 61
        assert [r["name"] for r in slices["visibility"][backend]] == ["allowed"]
    assert slices["keyword_outside_window"]["local"][0]["name"].startswith("keyword-")
    assert slices["keyword_outside_window"]["milvus"][0]["name"].startswith("dense-")
    assert [r["name"] for r in slices["filtered_window_exhausted"]["local"]] == ["allowed"]
    assert slices["filtered_window_exhausted"]["milvus"] == []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--milvus-uri", default="http://127.0.0.1:19530")
    args = parser.parse_args()
    if not args.apply:
        print(json.dumps({"mode": "dry-run", "new_collection_only": True,
                          "retains_artifacts": True}))
        return 0
    run_dir = ROOT / "evals" / "artifacts" / f"hybrid-{uuid4().hex}"
    run_dir.mkdir(parents=True)
    os.environ["DATABASE_URL"] = f"sqlite:///{(run_dir / 'evaluation.db').as_posix()}"
    os.environ["ENV"] = "development"
    from sqlmodel import Session

    from app.core.config import settings
    from app.core.database import engine, init_db

    settings.MILVUS_URI = args.milvus_uri
    settings.MILVUS_INDEX_TYPE = "FLAT"
    settings.RAG_EFFECTIVE_DATE_FILTER = False
    init_db()
    report = {"result": "unverified", "database": str(run_dir / "evaluation.db")}
    exit_code = 1
    try:
        with Session(engine) as session:
            report.update(asyncio.run(compare(session)))
        verify(report)
        report["result"] = "pass"
        exit_code = 0
    except Exception as exc:
        report["error_type"] = type(exc).__name__
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        engine.dispose()
    print(json.dumps({"result": report["result"], "report": str(args.report)}))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
