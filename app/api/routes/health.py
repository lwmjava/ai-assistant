"""健康检查路由。"""

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlmodel import Session, select

from app.core.config import settings
from app.core.database import engine
from app.models.rag import DocumentChunk

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])


def _configured_vector_backend() -> str:
    """返回当前生效的向量库名称：仅 milvus 走外部服务，其余为本地库。"""
    name = settings.RAG_VECTOR_STORE.strip().lower()
    return "milvus" if name == "milvus" else "local"


def _database_status() -> str:
    """确认应用数据库能执行一次查询。"""
    with Session(engine) as session:
        session.exec(text("SELECT 1")).one()
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
    overall = "ok" if database == "ok" and vector_status == "ok" else "error"
    body = {
        "status": overall,
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "env": settings.ENV,
        "checks": {
            "database": {"status": database},
            "vector_store": {"status": vector_status, "backend": backend},
        },
    }
    return JSONResponse(status_code=200 if overall == "ok" else 503, content=body)
