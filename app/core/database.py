"""数据库持久化层（SQLModel + SQLAlchemy）。

提供：
- ``engine``：全局数据库引擎（SQLite / PostgreSQL 由 DATABASE_URL 决定）；
- ``init_db``：通过版本化迁移初始化结构（应在应用启动时调用一次）；
- ``get_session``：FastAPI 依赖，按请求提供数据库会话。
"""

import os
from collections.abc import Generator

from sqlmodel import Session, SQLModel, create_engine

from app.core.config import settings

# SQLite 需要关闭同线程检查以配合多线程 ASGI 服务器；timeout=30 设置
# busy timeout，让并发写事务等待锁释放而不是立即抛 database is locked。
_connect_args = (
    {"check_same_thread": False, "timeout": 30} if settings.DATABASE_URL.startswith("sqlite") else {}
)

# SQLite 文件路径的父目录若不存在则自动创建（避免首次运行因目录缺失而启动失败）。
if settings.DATABASE_URL.startswith("sqlite"):
    _db_path = settings.DATABASE_URL.replace("sqlite:///", "", 1).replace("sqlite://", "", 1)
    _parent_dir = os.path.dirname(_db_path)
    if _parent_dir:
        os.makedirs(_parent_dir, exist_ok=True)

engine = create_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO,
    connect_args=_connect_args,
    pool_pre_ping=True,
)


def init_db(*, auto_migrate: bool = True) -> None:
    """通过 Alembic 初始化结构，显式开发建表仅用于关闭自动迁移的场景。

    必须在使用模型之前导入模型模块，以确保 SQLModel.metadata 已注册对应表。

    Args:
        auto_migrate: 自动执行 Alembic；禁用时开发环境建表，生产环境只核验版本。
    """
    # 导入模型以注册表结构（Side-effect import）。
    from app.audit import models as _audit_models  # noqa: F401
    from app.models import (
        agent_state,  # noqa: F401
        conversation,  # noqa: F401
        evaluation,  # noqa: F401
        feature_flag,  # noqa: F401
        feedback,  # noqa: F401
        rag,  # noqa: F401
        skill,  # noqa: F401
        user,  # noqa: F401
        workflow,  # noqa: F401
    )

    if auto_migrate:
        from app.core.migration import auto_migrate as _auto_migrate

        _auto_migrate()
    elif settings.is_production:
        from app.core.migration import get_current_revision, get_head_revision, validate_current_schema

        if get_current_revision() != get_head_revision():
            raise RuntimeError("生产环境已禁用自动迁移，但数据库未到最新修订，请先执行 Alembic 升级。")
        validate_current_schema()
    else:
        SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    """FastAPI 依赖：为每个请求提供数据库会话，并在结束时自动关闭。"""
    with Session(engine) as session:
        yield session
