"""Alembic 迁移环境配置。

与 SQLModel 集成：从所有已注册的 SQLModel 表元数据生成迁移脚本。
数据库 URL 在运行时从应用配置读取，而非硬编码在 alembic.ini 中。
"""

import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

# Alembic Config 对象，提供 .ini 文件中的配置
config = context.config

# 设置日志（遵循 alembic.ini 中的 [loggers] 配置）
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 延迟导入应用配置以避免循环依赖
# 注意：此处仅导入配置获取 DATABASE_URL，不导入业务模块
try:
    from app.core.config import settings

    _db_url = settings.DATABASE_URL
except Exception:
    _db_url = "sqlite:///./data/ai_assistant.db"

# 将数据库 URL 注入 Alembic 配置（优先于 alembic.ini 中的硬编码值）
config.set_main_option("sqlalchemy.url", _db_url)

# ---- 导入所有 SQLModel 表模型 ----
# 必须在运行 autogenerate 之前导入，确保 SQLModel.metadata 已注册所有表
from app.models import user  # noqa: F401, E402
from app.models import conversation  # noqa: F401, E402
from app.models import rag  # noqa: F401, E402
from app.models import workflow  # noqa: F401, E402

# SQLModel.metadata 包含所有已注册的 SQLModel 表（table=True）
target_metadata = SQLModel.metadata

# 排除 Alembic 自身的版本表（避免 autogenerate 误检）
# SQLite 下可使用 render_as_batch 确保 ALTER 兼容
# SKELETON：生产 PostgreSQL 环境应移除 render_as_batch
_IS_SQLITE = _db_url.startswith("sqlite")


def run_migrations_offline() -> None:
    """离线模式：生成 SQL 脚本输出到文件（不连接数据库）。

    用法：alembic upgrade head --sql > migration.sql
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite 批量模式
        render_as_batch=_IS_SQLITE,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：直接连接数据库执行迁移。

    用法：alembic upgrade head（默认模式）
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite 批量模式：ALTER TABLE 等 DDL 通过 CREATE TABLE AS 实现
            render_as_batch=_IS_SQLITE,
            # 比较列类型（自动检测类型变更）
            compare_type=True,
            # 比较服务器默认值
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()