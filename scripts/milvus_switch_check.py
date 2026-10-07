"""RAG-015 local/Milvus 切换验收。

默认只打印计划；显式 ``--apply`` 才会创建临时 SQLite、上传测试语料并访问
Milvus。脚本不会修改 ``.env``，也不会把旧的五门脚本结果当成本次证据。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import socket
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = REPO_ROOT / "evals" / "reports" / "rag-015-local-milvus-switch-20261007.json"
FROZEN_BASELINE = REPO_ROOT / "evals" / "reports" / "rag-v0.1-baseline-20260919.json"
TENANT_ID = "__auth_disabled__"
CORPUS = (
    {
        "logical_id": "cedar-warranty",
        "filename": "cedar-warranty.txt",
        "text": "Cedar orchard warranty requires an amber receipt and lunar serial number.",
    },
    {
        "logical_id": "harbor-maintenance",
        "filename": "harbor-maintenance.txt",
        "text": "Harbor turbine maintenance uses a cobalt wrench and a weekly pressure log.",
    },
    {
        "logical_id": "alpine-calibration",
        "filename": "alpine-calibration.txt",
        "text": "Alpine sensor calibration uses a violet beacon and a quartz reference.",
    },
)
QUERIES = (
    "amber receipt warranty",
    "cobalt wrench maintenance",
    "quartz beacon calibration",
    "unseen neutral phrase",
)


class AcceptanceCheckError(AssertionError):
    """验收判定失败，和基础设施阻塞分开记录。"""


def dry_run_plan(milvus_uri: str, report: Path) -> dict[str, Any]:
    """返回不产生写操作的执行计划。"""
    return {
        "mode": "dry-run",
        "writes_performed": False,
        "milvus_uri": milvus_uri,
        "report": report.as_posix(),
        "steps": [
            "在全新临时 SQLite 中以默认 local 上传固定语料，并断言检索命中",
            "将进程内 RAG_VECTOR_STORE 切为 milvus，上传同一语料并直接查询 Milvus 集合",
            "记录自动写入判定后，显式补种 Milvus 向量，仅用于跨库差异采样",
            "切回 local，联合核对 SQL 分块存在且检索命中，禁止把丢数据当 no_hit",
            "比较两库命中集合、排序、RRF 分数与 similarity 范围并写入新报告",
        ],
        "apply_command": (
            "python scripts/milvus_switch_check.py --apply "
            f"--milvus-uri {milvus_uri}"
        ),
    }


def assert_milvus_contains(expected_chunk_ids: list[str], milvus_rows: list[dict[str, Any]]) -> None:
    """必须以 Milvus 查询结果证明上传向量存在，SQL 行不能替代该证据。"""
    expected = set(expected_chunk_ids)
    observed = {str(row.get("id")) for row in milvus_rows if row.get("id")}
    missing = sorted(expected - observed)
    if not expected:
        raise AcceptanceCheckError("Milvus 路径上传后 SQL 中没有可向量化分块")
    if missing:
        raise AcceptanceCheckError(
            "Milvus 集合缺少上传产生的向量："
            f"expected={len(expected)} observed={len(observed)} missing={missing[:5]}；"
            "仅有 DocumentChunk 不能算 Milvus 写入通过"
        )


def assert_local_preserved(
    expected_document_ids: list[str],
    local_chunk_ids: list[str],
    hit_document_ids: list[str],
    retrieval_status: str,
) -> None:
    """切回 local 后同时证明数据存在且可检索，显式区分 no_hit 与数据丢失。"""
    if not local_chunk_ids:
        raise AcceptanceCheckError("切回 local 后 SQL 中没有原 local 分块：数据已丢失，不得记为普通空知识库")
    if retrieval_status == "no_hit":
        raise AcceptanceCheckError("切回 local 后返回 no_hit，但 SQL 中仍有 local 分块：不得静默当成空知识库")
    missing = sorted(set(expected_document_ids) - set(hit_document_ids))
    if missing:
        raise AcceptanceCheckError(f"切回 local 后未检索到原 local 文档：missing={missing}")


def assert_l2_normalized(embeddings: list[list[float]]) -> dict[str, float | None]:
    """验证所有非零写入向量均为 L2 单位向量，并返回范数范围。"""
    if not embeddings:
        raise AcceptanceCheckError("没有可验证归一化的写入向量")
    norms = [math.sqrt(sum(float(value) ** 2 for value in vector)) for vector in embeddings]
    invalid = [norm for norm in norms if not math.isclose(norm, 1.0, rel_tol=1e-6, abs_tol=1e-6)]
    if invalid:
        raise AcceptanceCheckError(f"写入向量未按 L2 归一化：norms={invalid[:5]}")
    return summarize_range(norms)


def summarize_range(values: list[float]) -> dict[str, float | None]:
    """以稳定 JSON 结构记录分值范围。"""
    if not values:
        return {"min": None, "max": None}
    return {"min": min(values), "max": max(values)}


def compare_results(
    local_hits: list[dict[str, Any]], milvus_hits: list[dict[str, Any]]
) -> dict[str, Any]:
    """比较逻辑命中集合、排序以及两类分数范围。"""
    local_order = [str(hit["logical_id"]) for hit in local_hits]
    milvus_order = [str(hit["logical_id"]) for hit in milvus_hits]
    local_set = set(local_order)
    milvus_set = set(milvus_order)
    shared = sorted(local_set & milvus_set)
    return {
        "local_order": local_order,
        "milvus_order": milvus_order,
        "only_local": sorted(local_set - milvus_set),
        "only_milvus": sorted(milvus_set - local_set),
        "rank_differences": {
            item: {"local": local_order.index(item) + 1, "milvus": milvus_order.index(item) + 1}
            for item in shared
            if local_order.index(item) != milvus_order.index(item)
        },
        "score_range": {
            "local": summarize_range([float(hit["score"]) for hit in local_hits]),
            "milvus": summarize_range([float(hit["score"]) for hit in milvus_hits]),
        },
        "similarity_range": {
            "local": summarize_range([float(hit["similarity"]) for hit in local_hits]),
            "milvus": summarize_range([float(hit["similarity"]) for hit in milvus_hits]),
        },
    }


def _tcp_target(uri: str) -> tuple[str, int]:
    parsed = urlsplit(uri if "://" in uri else f"http://{uri}")
    return parsed.hostname or "127.0.0.1", parsed.port or 19530


def _tcp_error(uri: str) -> str | None:
    host, port = _tcp_target(uri)
    try:
        with socket.create_connection((host, port), timeout=3):
            return None
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"


def _ids_expr(ids: list[str]) -> str:
    quoted = ", ".join(json.dumps(item) for item in ids)
    return f"id in [{quoted}]"


def _logical_id(content: str) -> str:
    digest = hashlib.sha256(content.strip().encode("utf-8")).hexdigest()
    for item in CORPUS:
        if hashlib.sha256(item["text"].encode("utf-8")).hexdigest() == digest:
            return str(item["logical_id"])
    return f"unknown:{digest[:12]}"


def _configure_environment(database_path: Path, milvus_uri: str, collection_prefix: str) -> None:
    os.environ["ENV"] = "development"
    os.environ["AUTH_ENABLED"] = "false"
    os.environ["DATABASE_URL"] = f"sqlite:///{database_path.resolve().as_posix()}"
    os.environ["EMBEDDING_PROVIDER"] = "mock"
    os.environ["EMBEDDING_DIM"] = "64"
    os.environ["EMBEDDING_NORMALIZATION"] = "l2"
    os.environ["EMBEDDING_METRIC"] = "cosine"
    os.environ["RAG_VECTOR_STORE"] = "local"
    os.environ["RAG_BACKEND"] = "native"
    os.environ["RAG_CHUNK_STRATEGY"] = "structured"
    os.environ["RAG_CHUNK_SIZE"] = "500"
    os.environ["RAG_CHUNK_OVERLAP"] = "64"
    os.environ["RAG_HYBRID_RRF_K"] = "60"
    os.environ["RAG_EFFECTIVE_DATE_FILTER"] = "false"
    os.environ["MILVUS_URI"] = milvus_uri
    os.environ["MILVUS_COLLECTION"] = collection_prefix
    os.environ["INITIAL_ADMIN_USERNAME"] = ""
    os.environ["INITIAL_ADMIN_PASSWORD"] = ""


def _upload_corpus(client: Any, backend_label: str) -> list[dict[str, str]]:
    uploaded: list[dict[str, str]] = []
    for item in CORPUS:
        response = client.post(
            "/api/rag/documents/upload",
            files={
                "file": (
                    f"{backend_label}-{item['filename']}",
                    item["text"].encode("utf-8"),
                    "text/plain",
                )
            },
        )
        if response.status_code != 200:
            raise AcceptanceCheckError(
                f"{backend_label} 上传失败：status={response.status_code} body={response.text[:300]}"
            )
        body = response.json()
        uploaded.append({"logical_id": str(item["logical_id"]), "document_id": str(body["id"])})
    return uploaded


def _http_search(client: Any, query: str, top_k: int = 5) -> tuple[list[dict[str, Any]], str]:
    response = client.post("/api/rag/search", json={"query": query, "top_k": top_k})
    if response.status_code != 200:
        raise AcceptanceCheckError(
            f"检索失败：query={query!r} status={response.status_code} body={response.text[:300]}"
        )
    return list(response.json()), response.headers.get("X-Retrieval-Status", "missing")


def _load_chunks(session: Any, document_ids: list[str]) -> list[Any]:
    from sqlmodel import col, select

    from app.models.rag import DocumentChunk

    return list(
        session.exec(select(DocumentChunk).where(col(DocumentChunk.document_id).in_(document_ids))).all()
    )


async def _search_backend(backend: str, queries: tuple[str, ...]) -> dict[str, list[dict[str, Any]]]:
    from sqlmodel import Session

    from app.core.config import settings
    from app.core.database import engine
    from app.rag.embeddings.mock import MockEmbeddingProvider, tokenize
    from app.rag.vectorstore.factory import get_vector_store

    settings.RAG_VECTOR_STORE = backend
    provider = MockEmbeddingProvider(dim=64)
    output: dict[str, list[dict[str, Any]]] = {}
    with Session(engine) as session:
        store = get_vector_store(session)
        for query in queries:
            vector = (await provider.embed([query]))[0]
            hits = await store.hybrid_search(
                vector,
                tokenize(query),
                TENANT_ID,
                top_k=len(CORPUS),
                rrf_k=60,
            )
            output[query] = [
                {
                    "logical_id": _logical_id(hit.content),
                    "chunk_id": hit.id,
                    "score": float(hit.score),
                    "similarity": float(hit.similarity),
                }
                for hit in hits
            ]
    return output


async def _run_apply(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    run_id = uuid.uuid4().hex[:10]
    report: dict[str, Any] = {
        "schema_version": "rag-015-switch-v1",
        "run_at": datetime.now(UTC).isoformat(),
        "run_id": run_id,
        "milvus_uri": args.milvus_uri,
        "collection_prefix": f"{args.collection_prefix}_{run_id}",
        "corpus": list(CORPUS),
        "queries": list(QUERIES),
        "paths": {},
        "cross_backend": {},
        "limitations": [
            "MockEmbeddingProvider 仅用于确定性链路对照，不代表生产语义质量。",
            "范数检查证明本次 Mock 样本为单位向量，不等同于生产写入路径已主动执行 L2 归一化。",
            "Milvus 跨库样本在记录自动写入失败后显式补种；补种不计作上传写链路通过证据。",
            "当前 app/rag/vectorstore/milvus.py 返回 similarity=1.0 占位，本报告会如实暴露该范围。",
        ],
    }
    tcp_error = _tcp_error(args.milvus_uri)
    report["milvus_preflight"] = {"status": "pass" if tcp_error is None else "blocked", "error": tcp_error}

    with tempfile.TemporaryDirectory(prefix="rag015-switch-") as temp_dir:
        temp_root = Path(temp_dir)
        database_path = temp_root / "rag015-switch.db"
        _configure_environment(database_path, args.milvus_uri, report["collection_prefix"])
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))

        from fastapi.testclient import TestClient
        from sqlmodel import Session

        from app.core.config import settings
        from app.core.database import engine, init_db
        from app.main import app
        from app.rag import document_storage

        document_storage._PROJECT_ROOT = temp_root
        init_db(auto_migrate=False)
        client = TestClient(app)

        settings.RAG_VECTOR_STORE = "local"
        try:
            local_docs = _upload_corpus(client, "local")
            query_results, retrieval_status = _http_search(client, "amber receipt warranty")
            local_doc_ids = [item["document_id"] for item in local_docs]
            hit_ids = [str(item["document_id"]) for item in query_results]
            if local_doc_ids[0] not in hit_ids:
                raise AcceptanceCheckError(
                    f"默认 local 上传后未命中 cedar-warranty：hits={hit_ids}"
                )
            with Session(engine) as session:
                local_chunks = _load_chunks(session, local_doc_ids)
            local_norm_range = assert_l2_normalized(
                [json.loads(chunk.embedding) for chunk in local_chunks if chunk.embedding]
            )
            report["paths"]["default_local"] = {
                "status": "pass",
                "configured_backend": settings.RAG_VECTOR_STORE,
                "uploaded_document_ids": local_doc_ids,
                "sql_chunk_count": len(local_chunks),
                "embedding_normalization": "l2",
                "embedding_norm_range": local_norm_range,
                "retrieval_status": retrieval_status,
                "hit_document_ids": hit_ids,
            }
        except Exception as exc:  # noqa: BLE001 - 报告要保留其它路径证据
            local_docs = []
            local_chunks = []
            report["paths"]["default_local"] = {
                "status": "fail",
                "error": f"{type(exc).__name__}: {exc}",
            }

        milvus_docs: list[dict[str, str]] = []
        milvus_chunks: list[Any] = []
        collection = None
        if tcp_error is not None:
            report["paths"]["milvus_upload_visible"] = {
                "status": "blocked",
                "error": tcp_error,
            }
        else:
            settings.RAG_VECTOR_STORE = "milvus"
            try:
                import pymilvus

                from app.rag.vectorstore.milvus import MilvusVectorStore

                report["pymilvus_version"] = pymilvus.__version__
                milvus_docs = _upload_corpus(client, "milvus")
                milvus_doc_ids = [item["document_id"] for item in milvus_docs]
                with Session(engine) as session:
                    milvus_chunks = _load_chunks(session, milvus_doc_ids)
                    expected_ids = [chunk.id for chunk in milvus_chunks if chunk.embedding]
                    norm_range = assert_l2_normalized(
                        [json.loads(chunk.embedding) for chunk in milvus_chunks if chunk.embedding]
                    )
                    store = MilvusVectorStore(session)
                    collection = store._connect()
                    collection.flush()
                    rows = list(
                        collection.query(
                            expr=_ids_expr(expected_ids),
                            output_fields=["id", "document_id"],
                        )
                        or []
                    )
                    try:
                        assert_milvus_contains(expected_ids, rows)
                    except AcceptanceCheckError as exc:
                        report["paths"]["milvus_upload_visible"] = {
                            "status": "fail",
                            "uploaded_document_ids": milvus_doc_ids,
                            "sql_vectorized_chunk_count": len(expected_ids),
                            "embedding_normalization": "l2",
                            "embedding_norm_range": norm_range,
                            "milvus_matching_vector_count": len(rows),
                            "error": str(exc),
                        }
                    else:
                        report["paths"]["milvus_upload_visible"] = {
                            "status": "pass",
                            "uploaded_document_ids": milvus_doc_ids,
                            "sql_vectorized_chunk_count": len(expected_ids),
                            "embedding_normalization": "l2",
                            "embedding_norm_range": norm_range,
                            "milvus_matching_vector_count": len(rows),
                        }
            except Exception as exc:  # noqa: BLE001 - 连接/版本失败单独报告
                report["paths"]["milvus_upload_visible"] = {
                    "status": "blocked",
                    "error": f"{type(exc).__name__}: {exc}",
                }

        settings.RAG_VECTOR_STORE = "local"
        try:
            local_doc_ids = [item["document_id"] for item in local_docs]
            with Session(engine) as session:
                current_local_chunks = _load_chunks(session, local_doc_ids)
            hits, retrieval_status = _http_search(client, "amber receipt warranty")
            hit_ids = [str(item["document_id"]) for item in hits]
            assert_local_preserved(
                [local_doc_ids[0]] if local_doc_ids else [],
                [chunk.id for chunk in current_local_chunks],
                hit_ids,
                retrieval_status,
            )
            report["paths"]["switch_back_local"] = {
                "status": "pass",
                "sql_chunk_count": len(current_local_chunks),
                "retrieval_status": retrieval_status,
                "hit_document_ids": hit_ids,
                "data_vs_empty_distinguished": True,
            }
        except Exception as exc:  # noqa: BLE001 - 报告要保留 Milvus 结果
            report["paths"]["switch_back_local"] = {
                "status": "fail",
                "data_vs_empty_distinguished": True,
                "error": f"{type(exc).__name__}: {exc}",
            }

        if tcp_error is None and milvus_chunks:
            try:
                from app.rag.vectorstore.milvus import MilvusVectorStore

                settings.RAG_VECTOR_STORE = "milvus"
                with Session(engine) as session:
                    store = MilvusVectorStore(session)
                    await store.add(_load_chunks(session, [item["document_id"] for item in milvus_docs]))
                    collection = store._connect()
                    collection.flush()
                local_results = await _search_backend("local", QUERIES)
                milvus_results = await _search_backend("milvus", QUERIES)
                comparisons = {
                    query: compare_results(local_results[query], milvus_results[query])
                    for query in QUERIES
                }
                report["cross_backend"] = {
                    "status": "completed",
                    "milvus_seed_method": "explicit_store_add_after_acceptance_observation",
                    "counts_as_upload_write_proof": False,
                    "queries": comparisons,
                    "queries_with_hit_set_difference": sum(
                        bool(item["only_local"] or item["only_milvus"])
                        for item in comparisons.values()
                    ),
                    "queries_with_rank_difference": sum(
                        bool(item["rank_differences"]) for item in comparisons.values()
                    ),
                }
            except Exception as exc:  # noqa: BLE001 - 差异采样失败不覆盖三路径证据
                report["cross_backend"] = {
                    "status": "blocked",
                    "error": f"{type(exc).__name__}: {exc}",
                }
        else:
            report["cross_backend"] = {
                "status": "blocked",
                "error": tcp_error or "Milvus 上传阶段没有生成可补种的 SQL 分块",
            }

        if collection is not None:
            try:
                from pymilvus import utility

                name = collection.name
                if name.startswith(f"{args.collection_prefix}_{run_id}"):
                    collection.release()
                    utility.drop_collection(name)
                    report["milvus_cleanup"] = {"status": "pass", "collection": name}
                else:
                    report["milvus_cleanup"] = {
                        "status": "refused",
                        "collection": name,
                        "reason": "集合名不属于本次唯一前缀",
                    }
            except Exception as exc:  # noqa: BLE001 - 清理失败必须留痕
                report["milvus_cleanup"] = {
                    "status": "fail",
                    "error": f"{type(exc).__name__}: {exc}",
                }
        engine.dispose()

    settings.RAG_VECTOR_STORE = "local"
    report["final_backend"] = settings.RAG_VECTOR_STORE
    path_statuses = [item.get("status") for item in report["paths"].values()]
    report["all_acceptance_paths_passed"] = path_statuses == ["pass", "pass", "pass"]
    report["result"] = "pass" if report["all_acceptance_paths_passed"] else "fail_or_blocked"
    return (0 if report["all_acceptance_paths_passed"] else 1), report


def _write_report(path: Path, report: dict[str, Any]) -> None:
    if path.resolve() == FROZEN_BASELINE.resolve():
        raise SystemExit(f"拒绝覆盖冻结基线：{FROZEN_BASELINE}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG-015 local/Milvus 切换验收")
    parser.add_argument("--apply", action="store_true", help="执行真实上传、Milvus 查询与切回检查")
    parser.add_argument("--milvus-uri", default="http://127.0.0.1:19530")
    parser.add_argument("--collection-prefix", default="rag015_switch_check")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    if not args.apply:
        print(json.dumps(dry_run_plan(args.milvus_uri, args.report), ensure_ascii=False, indent=2))
        return 0

    exit_code, report = asyncio.run(_run_apply(args))
    _write_report(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"报告已写入：{args.report}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
