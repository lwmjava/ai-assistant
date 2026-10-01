"""数据库迁移运行器。

封装 Alembic 命令，提供：
- 应用启动时自动迁移（auto_migrate）
- CLI 迁移（migrate / check / history）
- 迁移前状态检查

设计原则：
- 自动迁移：启动时默认执行，失败时记录错误但不阻塞启动（开发友好）
- 生产模式：迁移失败直接拒绝启动（安全优先）
- CLI 模式：展示迁移清单并确认，支持 --check / --yes
"""

import logging
import sys
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from alembic import command
from app.core.config import settings

logger = logging.getLogger(__name__)

# Alembic 配置文件路径（相对于项目根目录）
_ALEMBIC_INI = Path(__file__).resolve().parent.parent.parent / "alembic.ini"


def _get_alembic_config() -> Config:
    """获取 Alembic 配置对象。

    每次调用创建新实例，确保线程安全。
    """
    if not _ALEMBIC_INI.exists():
        raise FileNotFoundError(
            f"alembic.ini 未找到：{_ALEMBIC_INI}。"
            f"请确认从项目根目录运行，或设置 ALEMBIC_CONFIG 环境变量。"
        )
    cfg = Config(str(_ALEMBIC_INI))
    # 从应用配置注入数据库 URL（覆盖 alembic.ini 中的占位符）
    cfg.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
    return cfg


def get_current_revision() -> str | None:
    """获取当前数据库的迁移版本号。

    Returns:
        当前修订版本号；若数据库未初始化则返回 None。
    """
    from app.core.database import engine

    with engine.connect() as conn:
        # 检查 alembic_version 表是否存在
        insp = inspect(conn)
        if "alembic_version" not in insp.get_table_names():
            return None
        context = MigrationContext.configure(conn)
        return context.get_current_revision()


def get_head_revision() -> str:
    """获取迁移链的最新版本号。"""
    cfg = _get_alembic_config()
    script = ScriptDirectory.from_config(cfg)
    return script.get_current_head()


def get_pending_migrations() -> list[str]:
    """获取待执行的迁移版本列表。

    Returns:
        待迁移的修订版本号列表（从当前版本到 head）。
    """
    cfg = _get_alembic_config()
    script = ScriptDirectory.from_config(cfg)
    current = get_current_revision()

    if current is None:
        # 数据库未初始化，返回所有版本
        revisions = []
        for rev in script.iterate_revisions("head", "base"):
            revisions.append(rev.revision)
        return list(reversed(revisions))

    # 从当前版本到 head 的迁移列表
    pending = []
    for rev in script.iterate_revisions("head", current):
        pending.append(rev.revision)
    return list(reversed(pending))


def get_migration_history() -> list[dict]:
    """获取迁移历史记录。

    Returns:
        迁移历史列表，每项包含 revision / down_revision / doc。
    """
    cfg = _get_alembic_config()
    script = ScriptDirectory.from_config(cfg)
    history = []
    for rev in script.iterate_revisions("head", "base"):
        history.append({
            "revision": rev.revision,
            "down_revision": rev.down_revision,
            "doc": rev.doc,
        })
    return history


def _stamp_legacy_unversioned_schema() -> None:
    """为旧的未版本化开发库补写基线版本号。

    早期开发库可能由 ``SQLModel.metadata.create_all()`` 直接创建，已有业务表但没有
    ``alembic_version``。这类库如果直接跑初始迁移会因“表已存在”失败，导致后续增量
    迁移也无法执行。开发/测试环境下，若探测到这种状态，则先将数据库标记为最早的
    迁移版本，再继续执行增量迁移。
    """
    current = get_current_revision()
    if current is not None:
        return

    from app.core.database import engine

    with engine.connect() as conn:
        table_names = set(inspect(conn).get_table_names())
    business_tables = {
        "conversations",
        "messages",
        "rag_documents",
        "rag_document_chunks",
        "tenants",
        "users",
        "workflows",
        "workflow_executions",
    }
    if not (table_names & business_tables):
        return

    history = get_migration_history()
    if not history:
        return
    base_revision = history[-1]["revision"]
    logger.warning(
        "检测到未版本化的既有数据库，先标记到基线版本再执行增量迁移: revision=%s",
        base_revision,
    )
    stamp(base_revision)


def _ensure_rag_schema_columns() -> None:
    """为历史数据库补齐 RAG 版本化与导入平台所需列。

    某些 SQLite 开发库可能出现“修订号已更新，但表结构仍停留在旧版本”的情况。
    这里做一次幂等补齐，避免服务层在访问新增列时直接失败。
    """
    from app.core.database import engine

    expected_columns = {
        "storage_path": "ALTER TABLE rag_documents ADD COLUMN storage_path VARCHAR",
        "source_kind": "ALTER TABLE rag_documents ADD COLUMN source_kind VARCHAR NOT NULL DEFAULT 'file'",
        "source_uri": "ALTER TABLE rag_documents ADD COLUMN source_uri VARCHAR",
        "content_hash": "ALTER TABLE rag_documents ADD COLUMN content_hash VARCHAR",
        "version_group_id": "ALTER TABLE rag_documents ADD COLUMN version_group_id VARCHAR",
        "version_number": "ALTER TABLE rag_documents ADD COLUMN version_number INTEGER NOT NULL DEFAULT 1",
        "previous_document_id": "ALTER TABLE rag_documents ADD COLUMN previous_document_id VARCHAR",
        "is_current": "ALTER TABLE rag_documents ADD COLUMN is_current BOOLEAN NOT NULL DEFAULT 1",
        "effective_at": "ALTER TABLE rag_documents ADD COLUMN effective_at DATETIME",
        "expires_at": "ALTER TABLE rag_documents ADD COLUMN expires_at DATETIME",
        "import_job_id": "ALTER TABLE rag_documents ADD COLUMN import_job_id VARCHAR",
        "deleted_at": "ALTER TABLE rag_documents ADD COLUMN deleted_at DATETIME",
        "version_state": "ALTER TABLE rag_documents ADD COLUMN version_state VARCHAR NOT NULL DEFAULT 'published'",
    }
    with engine.begin() as conn:
        table_names = set(inspect(conn).get_table_names())
        if "rag_documents" not in table_names:
            return
        columns = {col["name"] for col in inspect(conn).get_columns("rag_documents")}
        for name, ddl in expected_columns.items():
            if name not in columns:
                logger.warning("补齐缺失的 RAG 文档列: %s", name)
                conn.execute(text(ddl))
        columns = {col["name"] for col in inspect(conn).get_columns("rag_documents")}
        if "version_group_id" in columns:
            conn.execute(
                text(
                    "UPDATE rag_documents "
                    "SET version_group_id = id "
                    "WHERE version_group_id IS NULL OR version_group_id = ''"
                )
            )
        if "version_state" in columns:
            conn.execute(
                text(
                    "UPDATE rag_documents SET version_state = 'replaced' "
                    "WHERE is_current = 0 AND ("
                    "version_state IS NULL OR version_state = '' OR version_state = 'published'"
                    ")"
                )
            )
        for index_sql in [
            "CREATE INDEX IF NOT EXISTS ix_rag_documents_source_kind ON rag_documents (source_kind)",
            "CREATE INDEX IF NOT EXISTS ix_rag_documents_source_uri ON rag_documents (source_uri)",
            "CREATE INDEX IF NOT EXISTS ix_rag_documents_content_hash ON rag_documents (content_hash)",
            "CREATE INDEX IF NOT EXISTS ix_rag_documents_version_group_id ON rag_documents (version_group_id)",
            "CREATE INDEX IF NOT EXISTS ix_rag_documents_previous_document_id ON rag_documents (previous_document_id)",
            "CREATE INDEX IF NOT EXISTS ix_rag_documents_is_current ON rag_documents (is_current)",
            "CREATE INDEX IF NOT EXISTS ix_rag_documents_effective_at ON rag_documents (effective_at)",
            "CREATE INDEX IF NOT EXISTS ix_rag_documents_expires_at ON rag_documents (expires_at)",
            "CREATE INDEX IF NOT EXISTS ix_rag_documents_import_job_id ON rag_documents (import_job_id)",
        ]:
            conn.execute(text(index_sql))

        # 为历史库补齐分块元数据列（父子文档 / 策略 / 溯源）。
        chunk_columns = {
            "parent_id": "ALTER TABLE rag_document_chunks ADD COLUMN parent_id VARCHAR",
            "strategy": "ALTER TABLE rag_document_chunks ADD COLUMN strategy VARCHAR",
            "chunk_metadata": "ALTER TABLE rag_document_chunks ADD COLUMN chunk_metadata VARCHAR",
        }
        if "rag_document_chunks" in table_names:
            chunk_existing = {
                col["name"] for col in inspect(conn).get_columns("rag_document_chunks")
            }
            for name, ddl in chunk_columns.items():
                if name not in chunk_existing:
                    logger.warning("补齐缺失的 RAG 分块列: %s", name)
                    conn.execute(text(ddl))
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_rag_document_chunks_parent_id "
                    "ON rag_document_chunks (parent_id)"
                )
            )


def upgrade(target: str = "head", sql: bool = False) -> None:
    """执行数据库升级。

    Args:
        target: 目标版本号（默认 "head" 表示最新版本）。
        sql: 若为 True，仅输出 SQL 不执行。
    """
    cfg = _get_alembic_config()
    if sql:
        command.upgrade(cfg, target, sql=True)
    else:
        command.upgrade(cfg, target)


def downgrade(target: str) -> None:
    """执行数据库降级。

    Args:
        target: 目标版本号（如 "-1" 表示回退一个版本）。
    """
    cfg = _get_alembic_config()
    command.downgrade(cfg, target)


def stamp(target: str = "head") -> None:
    """标记数据库版本（不执行迁移，仅更新版本号）。

    用于首次部署时直接标记当前 schema 为最新版本。
    """
    cfg = _get_alembic_config()
    command.stamp(cfg, target)


_HEAD_RAG_TABLES = {
    "rag_import_batches",
    "rag_import_jobs",
    "rag_import_job_traces",
}


def _later_rag_tables_exist() -> bool:
    """判断 create_all 是否已经建出导入平台相关表。

    开发库常见状态：``alembic_version`` 仍停在旧修订，但 ``SQLModel.metadata.create_all``
    已把后续迁移要创建的表建好。此时再执行 ``create_table`` 会在 SQLite 上锁等待或失败。
    """
    from app.core.database import engine

    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())
    return _HEAD_RAG_TABLES.issubset(tables)


def _stamp_if_schema_already_at_head() -> bool:
    """若库结构已达到 head 对应对象，则只标记版本、不重跑 DDL。"""
    current = get_current_revision()
    head = get_head_revision()
    if current == head:
        return False
    if not _later_rag_tables_exist():
        return False
    logger.warning(
        "检测到 RAG 导入平台表已存在但 Alembic 修订落后，改为标记到 head: current=%s head=%s",
        current,
        head,
    )
    stamp(head)
    return True


def auto_migrate() -> bool:
    """应用启动时自动执行迁移。

    - 开发环境：迁移失败仅记录警告，不阻塞启动
    - 生产环境：迁移失败直接拒绝启动

    Returns:
        True 表示迁移成功或无待迁移项。
    """
    if not settings.is_production:
        _stamp_legacy_unversioned_schema()
    _stamp_if_schema_already_at_head()
    pending = get_pending_migrations()
    if not pending:
        _ensure_rag_schema_columns()
        logger.info("数据库 schema 已是最新版本，无需迁移。")
        return True

    logger.info("检测到 %d 个待迁移版本：%s", len(pending), pending)
    from app.core.database import engine

    engine.dispose()
    try:
        upgrade("head")
        _ensure_rag_schema_columns()
        logger.info("数据库迁移完成（%d 个版本）。", len(pending))
        return True
    except Exception as exc:
        logger.error("数据库迁移失败：%s", exc)
        if settings.is_production:
            raise RuntimeError(
                "生产环境数据库迁移失败，应用已拒绝启动。"
                "请检查数据库连接与迁移脚本，或手动执行 alembic upgrade head。"
            ) from exc
        logger.warning("开发环境：迁移失败不阻塞启动，请手动运行 alembic upgrade head。")
        return False


# ── CLI 入口（当前通过 python -m 调用，后续可迁移到 click/typer CLI）──
# SKELETON：CLI 完整实现 — 当前为最简骨架，可按需补充：
#   - click/typer 命令行框架
#   - `ai-assistant migrate` 入口
#   - 彩色输出（rich 库）
#   - 交互式确认（--yes / --check）
#   - 审计日志写入


def _cli_check() -> None:
    """CLI subcommand: check pending migrations."""
    print("Database URL:", settings.DATABASE_URL)
    current = get_current_revision()
    head = get_head_revision()
    print(f"Current revision: {current or '(uninitialized)'}")
    print(f"Head revision:    {head}")
    pending = get_pending_migrations()
    if pending:
        print(f"\nPending migrations ({len(pending)}):")
        for rev in pending:
            print(f"  - {rev}")
    else:
        print("\nDatabase is up to date.")


def _cli_migrate(yes: bool = False) -> None:
    """CLI subcommand: execute migrations."""
    pending = get_pending_migrations()
    if not pending:
        print("Database is up to date. No migrations needed.")
        return

    print(f"Pending migrations ({len(pending)}):")
    for rev in pending:
        print(f"  - {rev}")

    if not yes:
        answer = input("\nExecute migrations? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Cancelled.")
            return

    print("Running migrations...")
    upgrade("head")
    print("Migrations complete.")


def _cli_history() -> None:
    """CLI subcommand: view migration history."""
    history = get_migration_history()
    if not history:
        print("No migration history.")
        return
    print(f"Migration history ({len(history)} revisions):")
    for item in history:
        print(f"  {item['revision']} -> {item['down_revision'] or 'base'}: {item['doc']}")


if __name__ == "__main__":
    # 最简 CLI：python -m app.core.migration [check|migrate|history]
    # 后续可迁移到成熟的命令行框架（click/typer）。
    args = sys.argv[1:]
    cmd = args[0] if args else "check"

    if cmd == "check":
        _cli_check()
    elif cmd == "migrate":
        _cli_migrate(yes="--yes" in args or "-y" in args)
    elif cmd == "history":
        _cli_history()
    else:
        print("Usage: python -m app.core.migration [check|migrate|history]")
        print("  check   - Check for pending migrations")
        print("  migrate - Execute migrations (--yes to skip confirmation)")
        print("  history - View migration history")
        sys.exit(1)
