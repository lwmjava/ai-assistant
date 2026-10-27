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

from sqlalchemy import inspect

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from app.core.config import settings

logger = logging.getLogger(__name__)

# Alembic 配置文件路径（相对于项目根目录）
_ALEMBIC_INI = Path(__file__).resolve().parent.parent.parent / "alembic.ini"


def _get_alembic_config() -> Config:
    """获取 Alembic 配置对象。

    每次调用创建新实例，确保线程安全。
    脚本目录按 alembic.ini 所在位置解析，不随当前工作目录变化。
    """
    if not _ALEMBIC_INI.exists():
        raise FileNotFoundError(f"alembic.ini 未找到：{_ALEMBIC_INI}。")
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", (_ALEMBIC_INI.parent / "alembic").as_posix())
    # 从应用配置注入数据库 URL（覆盖 alembic.ini 中的占位符）
    cfg.set_main_option("sqlalchemy.url", settings.DATABASE_URL.replace("%", "%%"))
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
    head = script.get_current_head()
    if head is None:
        raise RuntimeError("迁移链没有唯一 head")
    return head


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
    """Compatibility entry point delegating schema changes to Alembic."""
    auto_migrate()


def _ensure_evaluation_job_columns() -> None:
    """Compatibility entry point delegating schema changes to Alembic."""
    auto_migrate()


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


def _quota_columns_present() -> bool:
    """配额相关列都在时，才可以把版本直接标到当前 head。"""
    from app.core.database import engine

    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())
        if "tenants" not in tables or "rag_documents" not in tables:
            return False
        tenant_columns = {column["name"] for column in inspect(conn).get_columns("tenants")}
        document_columns = {column["name"] for column in inspect(conn).get_columns("rag_documents")}
    required = {
        "message_limit",
        "storage_limit_bytes",
        "token_limit",
        "tokens_used_period",
        "quota_period_ym",
    }
    return required.issubset(tenant_columns) and "source_bytes" in document_columns


def _stamp_if_schema_already_at_head() -> bool:
    """若库结构已达到 head 对应对象，则只标记版本、不重跑 DDL。

    导入平台表已由 create_all 建出、但配额列还不在时，不能把版本标过加列迁移。
    这种库先标到加列迁移的父修订，后面的 upgrade 再补列。
    """
    current = get_current_revision()
    head = get_head_revision()
    from app.core.database import engine

    with engine.connect() as conn:
        inspector = inspect(conn)
        user_columns = (
            {column["name"] for column in inspector.get_columns("users")}
            if "users" in inspector.get_table_names() else set()
        )
        message_columns = (
            {column["name"] for column in inspector.get_columns("messages")}
            if "messages" in inspector.get_table_names() else set()
        )
        workflow_columns = (
            {column["name"] for column in inspector.get_columns("workflows")}
            if "workflows" in inspector.get_table_names() else set()
        )
        evaluation_tables = {
            "evaluation_datasets": {"family"},
            "evaluation_dataset_versions": {"cases_json"},
            "evaluation_feedback_candidates": {"status"},
            "evaluation_jobs": {"job_kind", "target_snapshot_json", "case_completed", "execution_user_id"},
            "evaluation_case_results": {"metrics_json", "evidence_json"},
            "run_history": {"events_json"},
            "conversation_memory_snapshots": {"prefix_hash", "policy_version"},
        }
        tables = set(inspector.get_table_names())
        evaluation_schema_ready = all(
            table in tables and required.issubset(
                {column["name"] for column in inspector.get_columns(table)}
            )
            for table, required in evaluation_tables.items()
        )
        if evaluation_schema_ready:
            checks = [item["sqltext"].lower() for item in inspector.get_check_constraints("evaluation_jobs")]
            dataset_checks = [item["sqltext"].lower()
                              for item in inspector.get_check_constraints("evaluation_datasets")]
            evaluation_schema_ready = (
                any("runner" in check and "'workflow'" in check for check in checks)
                and any("mode" in check and "'real'" in check for check in checks)
                and any("family" in check and "'workflow'" in check for check in dataset_checks)
            )
    user_flags_present = {"is_test_user", "must_change_password"}.issubset(user_columns)
    if current == head:
        if not user_flags_present:
            raise RuntimeError("数据库修订已到最新版本，但用户安全标记列缺失；请核对并恢复正确 schema。")
        if "attachments" not in message_columns:
            raise RuntimeError("数据库修订已到最新版本，但消息附件列缺失；请核对并恢复正确 schema。")
        if "timeout_seconds" not in workflow_columns:
            raise RuntimeError("数据库修订已到最新版本，但工作流超时列缺失；请核对并恢复正确 schema。")
        if evaluation_schema_ready:
            return False
        logger.warning("数据库修订与评测结构不一致，将重新执行幂等向前迁移")
        stamp("a3b4c5d6e7f8")
        return False
    if not _later_rag_tables_exist():
        return False
    if not _quota_columns_present():
        from app.core.database import engine as _engine

        with _engine.connect() as conn:
            tenant_cols = (
                {column["name"] for column in inspect(conn).get_columns("tenants")}
                if "tenants" in inspect(conn).get_table_names()
                else set()
            )
        has_basic = {"message_limit", "storage_limit_bytes"}.issubset(tenant_cols)
        if has_basic:
            # timeout_seconds 在 a4b7c2d9e160 之后才加入；不能越过该修订直接标到反馈表修订。
            if "timeout_seconds" not in workflow_columns:
                if current != "a4b7c2d9e160":
                    logger.warning(
                        "配额基础列已在但工作流超时列缺失，版本先标到 a4b7c2d9e160: current=%s",
                        current,
                    )
                    stamp("a4b7c2d9e160")
            elif current != "d7e0f3a21b44":
                logger.warning(
                    "消息/存储配额列已在，但 Token 周期列还没有。版本先标到 d7e0f3a21b44: current=%s",
                    current,
                )
                stamp("d7e0f3a21b44")
        elif current != "d7c2a91e4b18":
            logger.warning(
                "导入平台表已在，但配额列还没有。版本先标到 d7c2a91e4b18，再执行加列: current=%s",
                current,
            )
            stamp("d7c2a91e4b18")
        return False
    if not user_flags_present:
        if current != "c1d2e3f4a5b6":
            stamp("c1d2e3f4a5b6")
        return False
    if "attachments" not in message_columns:
        if current != "f1a6c8d2b903":
            stamp("f1a6c8d2b903")
        return False
    if "timeout_seconds" not in workflow_columns:
        if current != "a4b7c2d9e160":
            stamp("a4b7c2d9e160")
        return False
    # The legacy checks above establish only this concrete revision. Newer
    # migrations must run even when create_all has already created their tables.
    verified_revision = "a3b4c5d6e7f8"
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_get_alembic_config())
    later = {revision.revision for revision in script.walk_revisions(base=verified_revision, head=head)}
    if current not in later:
        stamp(verified_revision)
    return False


def validate_current_schema() -> None:
    """Verify critical persisted columns without changing schema or revision."""
    from app.core.database import engine

    required = {
        "users": {"is_test_user", "must_change_password"},
        "messages": {"attachments"},
        "workflows": {"timeout_seconds"},
    }
    with engine.connect() as connection:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        for table, columns in required.items():
            if table not in tables or not columns.issubset(
                {column["name"] for column in inspector.get_columns(table)}
            ):
                raise RuntimeError("数据库修订与实际结构不一致，请核对迁移记录和 schema。")


def auto_migrate() -> bool:
    """Run versioned migrations; production errors reject startup."""
    try:
        head = get_head_revision()
        if not settings.is_production:
            _stamp_legacy_unversioned_schema()
            _stamp_if_schema_already_at_head()
        pending = get_pending_migrations()
        if pending:
            from app.core.database import engine

            engine.dispose()
            upgrade("head")
        if get_current_revision() != head:
            raise RuntimeError("数据库迁移后版本与唯一 head 不一致")
        validate_current_schema()
        logger.info("数据库迁移完成，schema revision=%s", head)
        return True
    except Exception as exc:
        logger.error("数据库迁移失败：%s", type(exc).__name__)
        if settings.is_production:
            raise RuntimeError(
                "生产环境数据库迁移失败，应用已拒绝启动。"
                "请检查数据库连接与迁移脚本，或手动执行 alembic upgrade head。"
            ) from exc
        logger.warning("开发环境：迁移失败不阻塞启动，请手动运行 alembic upgrade head。")
        return False


# 迁移命令有两个入口，行为相同：
# - python -m app.core.migration migrate
# - ai-assistant migrate（app.cli，内部调用这里的 _cli_migrate）
# 创建系统管理员不在本模块。


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
    # python -m app.core.migration [check|migrate|history]
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
