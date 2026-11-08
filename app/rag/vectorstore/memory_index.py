"""Cross-session vectors use the configured vector store, not the document index.

Local mode ranks the vectors stored beside the metadata in the application
database. Milvus mode uses a separate collection on the same server. A Milvus
failure stays a failure and is not replaced with the local ranking.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.core.config import settings
from app.rag.vectorstore.base import l2_normalize
from app.rag.vectorstore.milvus import MilvusUnavailableError

_COLLECTION = "cross_session_memory"


def configured_backend() -> str:
    return (settings.RAG_VECTOR_STORE or "").strip().lower()


def normalize(vector: list[float]) -> list[float]:
    return l2_normalize(vector)


def rank_local(rows: list[Any], query: list[float], top_k: int) -> list[Any]:
    scored: list[tuple[float, Any]] = []
    for row in rows:
        vector = json.loads(row.embedding_json)
        if not isinstance(vector, list) or len(vector) != len(query):
            continue
        score = sum(float(left) * right for left, right in zip(vector, query, strict=True))
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [row for _score, row in scored[:top_k]]


async def upsert(memory_id: str, tenant_id: str, user_id: str, vector: list[float]) -> None:
    if configured_backend() == "local":
        return
    if configured_backend() != "milvus":
        raise RuntimeError("不支持的向量库")
    await asyncio.to_thread(_milvus_upsert, memory_id, tenant_id, user_id, vector)


async def search_ids(
    tenant_id: str, user_id: str | None, vector: list[float], top_k: int
) -> list[str] | None:
    """Return Milvus ids, or None when the local vector store ranks the rows."""
    if configured_backend() == "local":
        return None
    if configured_backend() != "milvus":
        raise RuntimeError("不支持的向量库")
    return await asyncio.to_thread(_milvus_search, tenant_id, user_id, vector, top_k)


def _connect():
    try:
        from pymilvus import connections
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise MilvusUnavailableError("未安装 pymilvus，无法写入跨会话记忆向量") from exc
    from app.rag.vectorstore.runtime import resolved_uri

    connections.connect(
        alias="cross-session-memory",
        uri=resolved_uri() or settings.MILVUS_URI,
        token=settings.MILVUS_TOKEN or None,
    )
    return connections


def _collection(dim: int):
    from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, utility

    _connect()
    name = f"{_COLLECTION}_{dim}"
    if not utility.has_collection(name, using="cross-session-memory"):
        schema = CollectionSchema(
            [
                FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
                FieldSchema(name="tenant_id", dtype=DataType.VARCHAR, max_length=64),
                FieldSchema(name="user_id", dtype=DataType.VARCHAR, max_length=64),
                FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=dim),
            ],
            description="cross-session memory vectors",
        )
        collection = Collection(name, schema, using="cross-session-memory")
        collection.create_index(
            "embedding",
            {"index_type": "AUTOINDEX", "metric_type": "IP", "params": {}},
        )
    else:
        collection = Collection(name, using="cross-session-memory")
    collection.load()
    return collection


def _milvus_upsert(memory_id: str, tenant_id: str, user_id: str, vector: list[float]) -> None:
    try:
        collection = _collection(len(vector))
        collection.upsert([[memory_id], [tenant_id], [user_id], [vector]])
        collection.flush()
    except MilvusUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001 — 连接失败不能改走本地库
        raise MilvusUnavailableError("跨会话记忆向量写入失败") from exc


def _expr_literal(value: str) -> str:
    if any(char in value for char in '"\\'):
        raise MilvusUnavailableError("记忆标识不能包含引号")
    return value


def _milvus_search(
    tenant_id: str, user_id: str | None, vector: list[float], top_k: int
) -> list[str]:
    try:
        collection = _collection(len(vector))
        expr = f'tenant_id == "{_expr_literal(tenant_id)}"'
        if user_id is not None:
            expr += f' && user_id == "{_expr_literal(user_id)}"'
        hits = collection.search(
            data=[vector],
            anns_field="embedding",
            param={"metric_type": "IP", "params": {}},
            limit=top_k,
            expr=expr,
            output_fields=["id"],
        )
    except MilvusUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001 — 检索失败不能改走本地库
        raise MilvusUnavailableError("跨会话记忆向量检索失败") from exc
    found: list[str] = []
    for group in hits or []:
        for hit in group:
            found.append(str(hit.id))
    return found
