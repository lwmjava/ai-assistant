"""健康检查路由。"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlmodel import Session, select

from app.core.config import settings
from app.core.database import engine
from app.llm.factory import llm_availability
from app.models.rag import DocumentChunk
from app.rag.index_identity import (
    ACTUAL_METRIC,
    ACTUAL_NORMALIZATION,
    SUPPORTED_METRIC,
    SUPPORTED_NORMALIZATION,
    fingerprint_of_key,
)
from app.rag.index_registry import active_index, has_any_chunk, legacy_chunk_count

logger = logging.getLogger(__name__)

# 索引状态里需要运维立即处理的情形；这些都会让向量库检查项不再是 ok。
_ACTIONABLE_INDEX_STATUS = ("unavailable", "legacy_quarantined", "unsupported_config")

router = APIRouter(tags=["system"])

# 本进程加载路由时记下，供管理页展示启动时间。
STARTED_AT = datetime.now(UTC)


def _configured_vector_backend() -> str:
    """返回当前生效的向量库名称：仅 milvus 走外部服务，其余为本地库。"""
    name = settings.RAG_VECTOR_STORE.strip().lower()
    return "milvus" if name == "milvus" else "local"


def _database_status() -> str:
    """确认应用数据库能执行一次查询。"""
    with Session(engine) as session:
        session.execute(text("SELECT 1")).one()
    return "ok"


def _probe_milvus() -> None:
    """连接当前配置的 Milvus，并读取服务版本以确认链路可用。"""
    from pymilvus import connections, utility

    alias = "health"
    connections.connect(
        alias=alias,
        uri=settings.MILVUS_URI,
        token=settings.MILVUS_TOKEN or None,
        timeout=2,
    )
    try:
        utility.get_server_version(using=alias)
    finally:
        connections.disconnect(alias)


def _vector_store_status() -> tuple[str, str]:
    """确认当前配置的向量库可连通。本地库与应用库同库，只做一次只读探测。"""
    backend = _configured_vector_backend()
    if backend == "milvus":
        _probe_milvus()
        return "ok", backend
    with Session(engine) as session:
        session.exec(select(DocumentChunk.id).limit(1)).first()
    return "ok", backend


def _check(probe) -> str:
    try:
        return probe()
    except Exception as exc:
        logger.warning("connectivity check failed: %s", type(exc).__name__)
        return "error"


def _embedding_index_view() -> dict:
    """当前生效索引的身份与存量状态。

    展示只是可观测手段，**不是校验**：阻断混用发生在写入与检索的核对点，
    不在这里。但「没有生效索引 / 有历史数据被隔离 / 登记了不支持的归一化或度量」
    必须在这里看得见——否则知识库查不到东西时，业务侧只会看到一个正常空结果。

    ``normalization`` / ``metric`` 报的是**登记值**，``*_actual`` 报的是**当前实现
    真正执行的约定**；两者不一致时 ``supported`` 为 False，避免把未生效的配置
    当成已生效的控制。
    """
    view: dict = {
        "status": "unknown",
        "index_version": settings.EMBEDDING_INDEX_VERSION,
        "model": settings.EMBEDDING_MODEL,
        "dim": settings.EMBEDDING_DIM,
        "normalization": settings.EMBEDDING_NORMALIZATION,
        "metric": settings.EMBEDDING_METRIC,
        "normalization_actual": ACTUAL_NORMALIZATION,
        "metric_actual": ACTUAL_METRIC,
        "supported": True,
        "legacy_chunks": None,
        "fingerprint": None,
        "identity_key": None,
        "reason": None,
    }
    try:
        with Session(engine) as session:
            index = active_index(session)
            legacy = legacy_chunk_count(session)
            if index is not None:
                unsupported = (
                    (index.normalization or "").strip().lower() != SUPPORTED_NORMALIZATION
                    or (index.metric or "").strip().lower() != SUPPORTED_METRIC
                )
                view.update(
                    {
                        "status": "unsupported_config" if unsupported else index.status,
                        "name": index.name,
                        "provider": index.provider,
                        "model": index.model,
                        "deployment": index.deployment,
                        "dim": index.dim,
                        "index_version": index.index_version,
                        "normalization": index.normalization,
                        "metric": index.metric,
                        "supported": not unsupported,
                        # fingerprint 是短指纹，identity_key 是完整身份；两个字段
                        # 各归其位，不再让 fingerprint 字段存完整身份。
                        "fingerprint": fingerprint_of_key(index.identity_key),
                        "identity_key": index.identity_key,
                    }
                )
                if unsupported:
                    view["reason"] = (
                        f"索引 {index.name} 登记了 normalization={index.normalization} "
                        f"metric={index.metric}，但当前实现只按 "
                        f"{SUPPORTED_NORMALIZATION}/{SUPPORTED_METRIC} 执行；"
                        "请修正配置或重建索引。"
                    )
                elif legacy:
                    view["status"] = "legacy_quarantined"
                    view["reason"] = (
                        f"存在 {legacy} 条身份未知的历史分块，已被隔离、不参与检索。"
                        "确认它们与当前模型一致后执行 adopt，否则执行 rebuild。"
                    )
            elif has_any_chunk(session) or legacy:
                # 有数据却不知道它们的身份：检索会直接失败，不能只报 unknown。
                view["status"] = "unavailable"
                view["reason"] = (
                    "没有生效的 embedding 索引，但库中存在身份未知的历史分块；"
                    "检索会被拒绝。请先 adopt（有证据）或 rebuild。"
                )
            view["legacy_chunks"] = legacy
    except Exception as exc:  # noqa: BLE001 — 展示层故障不应把 health 判死
        logger.warning("embedding index view failed: %s", type(exc).__name__)
    return view


@router.get("/health")
def health_check() -> JSONResponse:
    """报告进程、数据库与当前向量库是否连通。任一依赖失败则整体不是 ok。"""
    database = _check(_database_status)
    try:
        vector_status, backend = _vector_store_status()
    except Exception as exc:
        logger.warning("vector store connectivity check failed: %s", type(exc).__name__)
        vector_status = "error"
        backend = _configured_vector_backend()
    # 整体只在**连通性**失败时判错（沿用既有契约）；索引身份问题是可处理的状态，
    # 表现为 degraded + reason，而不是把整个进程打成 503 掩盖真正原因。
    overall = "ok" if database == "ok" and vector_status in ("ok", "degraded") else "error"
    index_view = _embedding_index_view()
    if vector_status == "ok" and index_view["status"] in _ACTIONABLE_INDEX_STATUS:
        vector_status = "degraded"
    body = {
        "status": overall,
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "env": settings.ENV,
        "checks": {
            "database": {"status": database},
            "vector_store": {
                "status": vector_status,
                "backend": backend,
                "reason": index_view["reason"],
                "embedding_index": index_view,
            },
            "llm": {"mode": llm_availability()},
        },
    }
    return JSONResponse(status_code=200 if overall == "ok" else 503, content=body)
