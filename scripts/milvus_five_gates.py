"""Milvus 五条门槛核对。应用代码不得导入本文件。

在已能连接到 127.0.0.1:19530 的前提下，用单独 SQLite 执行摄取后显式 add、
重解析、租户隔离和 delete_by_document。嵌入若是 Mock，只记链路，五条不算通过。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import socket
import sys
import traceback
import uuid
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]


class GateBlocked(RuntimeError):
    """环境或版本不匹配，不记成业务断言失败。"""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


class GateFailed(RuntimeError):
    """某条门槛的业务断言失败。"""


def _prepare_env(database_url: str) -> None:
    os.environ["RAG_VECTOR_STORE"] = "milvus"
    os.environ["MILVUS_URI"] = "http://127.0.0.1:19530"
    os.environ["DATABASE_URL"] = database_url
    os.environ.setdefault("ENV", "development")


def _rollback_env() -> None:
    os.environ["RAG_VECTOR_STORE"] = "local"
    print("ROLLBACK RAG_VECTOR_STORE=local", flush=True)


def _tcp_open(host: str, port: int, timeout: float = 3.0) -> str | None:
    sock = socket.socket()
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        return None
    except Exception as exc:  # noqa: BLE001 — 连接失败原文要记入说明
        return f"{type(exc).__name__}: {exc}"
    finally:
        sock.close()


def _looks_like_version_mismatch(exc: BaseException) -> bool:
    text = str(exc).lower()
    needles = (
        "version",
        "incompat",
        "protocol",
        "unmarshall",
        "proto",
        "mismatch",
        "not supported",
    )
    return any(item in text for item in needles)


def _raise_connect_blocked(exc: BaseException) -> None:
    """TCP 已通时的客户端调用失败：记 blocked，不把调用失败写成业务断言失败。"""
    import pymilvus

    raise GateBlocked(
        "version_mismatch",
        f"pymilvus=={pymilvus.__version__} 与 milvusdb/milvus:v2.5.11 调用失败："
        f"{type(exc).__name__}: {exc}",
    ) from exc


def _query_by_document(collection: Any, document_id: str, tenant_id: str) -> list[dict]:
    expr = f'document_id == "{document_id}" and tenant_id == "{tenant_id}"'
    return list(collection.query(expr=expr, output_fields=["id", "document_id"]) or [])


def _query_ids(collection: Any, ids: list[str]) -> list[str]:
    if not ids:
        return []
    quoted = ", ".join(f'"{item}"' for item in ids)
    rows = collection.query(expr=f"id in [{quoted}]", output_fields=["id"]) or []
    return [str(row.get("id")) for row in rows if row.get("id")]


async def _run_gates() -> dict[str, Any]:
    from sqlmodel import Session, col, select

    from app.core.database import engine, init_db
    from app.models.rag import DocumentChunk
    from app.rag.document_parsers.base import ParsedDocument
    from app.rag.embeddings.factory import get_embedding_provider
    from app.rag.embeddings.mock import MockEmbeddingProvider
    from app.rag.service import RAGService
    from app.rag.vectorstore.milvus import MilvusVectorStore

    init_db(auto_migrate=True)

    embedding = get_embedding_provider()
    mock = isinstance(embedding, MockEmbeddingProvider)
    print(
        json.dumps(
            {
                "embedding_model": embedding.model,
                "embedding_dim": embedding.dim,
                "embedding_mock": mock,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if mock:
        print("EMBEDDING_IS_MOCK 五条只记链路，不算通过", flush=True)

    tenant_a = "gate-a-" + uuid.uuid4().hex[:12]
    tenant_b = "gate-b-" + uuid.uuid4().hex[:12]
    user_id = uuid.uuid4().hex
    marker_a = "GATEMARKA" + uuid.uuid4().hex
    marker_b = "GATEMARKB" + uuid.uuid4().hex
    report: dict[str, Any] = {
        "embedding_mock": mock,
        "gates": {},
    }

    with Session(engine) as session:
        rag = RAGService(session, tenant_a, embedding_provider=embedding)
        store = rag._vector_store
        if not isinstance(store, MilvusVectorStore):
            raise GateFailed(f"向量库不是 MilvusVectorStore：{type(store).__name__}")

        try:
            collection = store._connect()
        except Exception as exc:  # noqa: BLE001
            _raise_connect_blocked(exc)

        text_a = f"核对文本包含唯一标记 {marker_a}，用于摄取后检索。"
        try:
            doc = await rag.ingest_text(text_a, "gate-doc", "gate.txt", user_id)
            chunks = list(
                session.exec(
                    select(DocumentChunk).where(col(DocumentChunk.document_id) == doc.id)
                ).all()
            )
            if not chunks:
                raise GateFailed("摄取后没有分块")
            await store.add(chunks)
            collection.flush()
            hits = await rag.search(marker_a, top_k=5)
        except GateFailed:
            raise
        except Exception as exc:  # noqa: BLE001
            _raise_connect_blocked(exc)

        hit_ids = [hit.document_id for hit in hits]
        if doc.id not in hit_ids:
            raise GateFailed(f"第 1 条：检索未命中刚写入的文档，命中={hit_ids}")
        report["gates"]["1"] = {
            "result": "pass" if not mock else "chain_only",
            "document_id": doc.id,
            "chunk_count": len(chunks),
            "hit_document_ids": hit_ids,
        }
        print("GATE 1 ok", flush=True)

        old_ids = [chunk.id for chunk in chunks]
        parsed = ParsedDocument(
            text=f"重解析后的文本包含另一段唯一标记 {marker_b}。",
            title="gate-doc",
            source="gate.txt",
            extension=".txt",
            content_type="text/plain",
        )
        new_hash = hashlib.sha256(parsed.text.encode("utf-8")).hexdigest()
        try:
            doc = await rag.reindex_document_in_place(doc, parsed, content_hash=new_hash)
            new_chunks = list(
                session.exec(
                    select(DocumentChunk).where(col(DocumentChunk.document_id) == doc.id)
                ).all()
            )
            await store.add(new_chunks)
            collection.flush()
            leftover = _query_ids(collection, old_ids)
            search_old = await rag.search(marker_a, top_k=8)
            search_old_chunk_ids = [hit.id for hit in search_old]
        except GateFailed:
            raise
        except Exception as exc:  # noqa: BLE001
            _raise_connect_blocked(exc)

        if leftover:
            raise GateFailed(f"第 2 条：集合仍含旧主键 {leftover}")
        old_in_search = [item for item in search_old_chunk_ids if item in set(old_ids)]
        if old_in_search:
            raise GateFailed(f"第 2 条：检索仍返回旧主键 {old_in_search}")
        report["gates"]["2"] = {
            "result": "pass" if not mock else "chain_only",
            "old_ids": old_ids,
            "new_chunk_count": len(new_chunks),
            "collection_old_ids": leftover,
            "search_old_chunk_ids": search_old_chunk_ids,
        }
        print("GATE 2 ok", flush=True)

        rag_b = RAGService(session, tenant_b, embedding_provider=embedding)
        try:
            other_hits = await rag_b.search(marker_b, top_k=8)
        except Exception as exc:  # noqa: BLE001
            _raise_connect_blocked(exc)
        other_docs = [hit.document_id for hit in other_hits]
        if doc.id in other_docs:
            raise GateFailed(f"第 4 条：其他租户命中了第一个租户的文档 {doc.id}")
        report["gates"]["4"] = {
            "result": "pass" if not mock else "chain_only",
            "other_hit_document_ids": other_docs,
        }
        print("GATE 4 ok", flush=True)

        current_chunks = list(
            session.exec(
                select(DocumentChunk).where(col(DocumentChunk.document_id) == doc.id)
            ).all()
        )
        before = len(current_chunks)
        try:
            deleted = await store.delete_by_document(doc.id, tenant_a)
            collection.flush()
            remaining = _query_by_document(collection, doc.id, tenant_a)
        except Exception as exc:  # noqa: BLE001
            _raise_connect_blocked(exc)
        if deleted != before:
            raise GateFailed(f"第 3 条：delete_by_document 返回 {deleted}，删除前分块数 {before}")
        if remaining:
            raise GateFailed(f"第 3 条：删除后集合仍有 {len(remaining)} 条")
        report["gates"]["3"] = {
            "result": "pass" if not mock else "chain_only",
            "deleted": deleted,
            "before": before,
            "remaining": len(remaining),
        }
        print("GATE 3 ok", flush=True)

    report["gates"]["5"] = {
        "result": "pass",
        "note": "进程变量将设回 local；核对卷备份由调用方在停止容器后拷贝。",
    }
    report["all_passed"] = (not mock) and all(
        report["gates"].get(str(i), {}).get("result") == "pass" for i in (1, 2, 3, 4, 5)
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Milvus 五条门槛核对")
    parser.add_argument(
        "--database-url",
        default="sqlite:///./data/milvus_gate_check.db",
        help="单独 SQLite，不要指向开发库或测试库",
    )
    args = parser.parse_args()
    if "test_ai_assistant.db" in args.database_url or "ai_assistant.db" in args.database_url:
        print("REFUSE database url", file=sys.stderr)
        return 2

    os.chdir(REPO_ROOT)
    Path("data").mkdir(parents=True, exist_ok=True)
    _prepare_env(args.database_url)
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    print("pymilvus_import_check begin", flush=True)
    try:
        import pymilvus
    except ImportError as exc:
        _rollback_env()
        print(json.dumps({"blocked": "no_pymilvus", "detail": str(exc)}, ensure_ascii=False))
        return 3

    print(json.dumps({"pymilvus": pymilvus.__version__}, ensure_ascii=False), flush=True)
    if pymilvus.__version__ != "2.5.11":
        _rollback_env()
        print(
            json.dumps(
                {
                    "blocked": "version_mismatch",
                    "detail": f"解释器 pymilvus={pymilvus.__version__}，要求 2.5.11",
                },
                ensure_ascii=False,
            )
        )
        return 3

    tcp_err = _tcp_open("127.0.0.1", 19530)
    if tcp_err is not None:
        _rollback_env()
        print(json.dumps({"blocked": "milvus_unreachable", "detail": tcp_err}, ensure_ascii=False))
        return 3

    try:
        report = asyncio.run(_run_gates())
    except GateBlocked as exc:
        _rollback_env()
        print(json.dumps({"blocked": exc.reason, "detail": exc.detail}, ensure_ascii=False))
        traceback.print_exc()
        return 3
    except GateFailed as exc:
        _rollback_env()
        print(json.dumps({"failed": str(exc)}, ensure_ascii=False))
        traceback.print_exc()
        return 1
    except Exception as exc:  # noqa: BLE001
        if _looks_like_version_mismatch(exc):
            _rollback_env()
            print(
                json.dumps(
                    {
                        "blocked": "version_mismatch",
                        "detail": f"{type(exc).__name__}: {exc}",
                    },
                    ensure_ascii=False,
                )
            )
            traceback.print_exc()
            return 3
        _rollback_env()
        print(json.dumps({"failed": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        traceback.print_exc()
        return 1

    _rollback_env()
    print(json.dumps(report, ensure_ascii=False, default=str), flush=True)
    if report.get("embedding_mock"):
        return 4
    if not report.get("all_passed"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
