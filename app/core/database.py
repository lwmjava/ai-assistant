"""数据库持久化层（SQLModel + SQLAlchemy）。

提供：
- ``engine``：全局数据库引擎（SQLite / PostgreSQL 由 DATABASE_URL 决定）；
- ``init_db``：创建所有表（应在应用启动时调用一次）；
- ``get_session``：FastAPI 依赖，按请求提供数据库会话。
"""

from collections.abc import Generator
import os

from sqlmodel import Session, SQLModel, create_engine

from app.core.config import settings

# SQLite 需要关闭同线程检查以配合多线程 ASGI 服务器。
_connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}

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


def init_db() -> None:
    """创建所有数据表。

    必须在使用模型之前导入模型模块，以确保 SQLModel.metadata 已注册对应表。
    """
    # 导入模型以注册表结构（Side-effect import）。
    from app.models import user  # noqa: F401

    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    """FastAPI 依赖：为每个请求提供数据库会话，并在结束时自动关闭。"""
    with Session(engine) as session:
        yield session
