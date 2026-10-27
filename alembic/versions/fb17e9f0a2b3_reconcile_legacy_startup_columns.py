"""Reconcile historical startup columns through the versioned migration chain."""

import logging
from contextlib import nullcontext

from sqlalchemy import inspect, text

from alembic import op

revision = "fb17e9f0a2b3"
down_revision = "fa06d8e9f1a2"
branch_labels = None
depends_on = None
logger = logging.getLogger(__name__)


def _ensure_message_columns() -> None:
    """历史库被直接标到 head 时，补上消息来源列、停止状态列和技能名列。"""

    with nullcontext(op.get_bind()) as conn:
        tables = set(inspect(conn).get_table_names())
        if "messages" not in tables:
            return
        columns = {col["name"] for col in inspect(conn).get_columns("messages")}
        if "sources" not in columns:
            logger.warning("补齐缺失的消息来源列")
            conn.execute(text("ALTER TABLE messages ADD COLUMN sources VARCHAR"))
        if "status" not in columns:
            logger.warning("补齐缺失的消息状态列")
            conn.execute(
                text(
                    "ALTER TABLE messages ADD COLUMN status VARCHAR NOT NULL DEFAULT 'complete'"
                )
            )
        if "code_results" not in columns:
            logger.warning("补齐缺失的代码结果列")
            conn.execute(text("ALTER TABLE messages ADD COLUMN code_results VARCHAR"))
        if "skill_names" not in columns:
            logger.warning("补齐缺失的消息技能名列")
            conn.execute(text("ALTER TABLE messages ADD COLUMN skill_names TEXT"))


def _ensure_conversation_lifecycle_columns() -> None:
    """补齐会话软删除与归档列。"""

    with nullcontext(op.get_bind()) as conn:
        tables = set(inspect(conn).get_table_names())
        if "conversations" not in tables:
            return
        columns = {col["name"] for col in inspect(conn).get_columns("conversations")}
        if "deleted_at" not in columns:
            logger.warning("补齐缺失的会话软删除列")
            conn.execute(text("ALTER TABLE conversations ADD COLUMN deleted_at TIMESTAMP"))
        if "archived_at" not in columns:
            logger.warning("补齐缺失的会话归档列")
            conn.execute(text("ALTER TABLE conversations ADD COLUMN archived_at TIMESTAMP"))


def _ensure_quota_columns() -> None:
    """版本已被标到 head、但配额列还没写上时，把可空整数列补上。"""

    with nullcontext(op.get_bind()) as conn:
        tables = set(inspect(conn).get_table_names())
        if "tenants" in tables:
            columns = {column["name"] for column in inspect(conn).get_columns("tenants")}
            if "message_limit" not in columns:
                logger.warning("补齐缺失的租户消息上限列")
                conn.execute(text("ALTER TABLE tenants ADD COLUMN message_limit BIGINT"))
            if "storage_limit_bytes" not in columns:
                logger.warning("补齐缺失的租户源文件上限列")
                conn.execute(text("ALTER TABLE tenants ADD COLUMN storage_limit_bytes BIGINT"))
            if "token_limit" not in columns:
                logger.warning("补齐缺失的租户 Token 上限列")
                conn.execute(text("ALTER TABLE tenants ADD COLUMN token_limit BIGINT"))
            if "tokens_used_period" not in columns:
                logger.warning("补齐缺失的租户 Token 周期用量列")
                conn.execute(
                    text("ALTER TABLE tenants ADD COLUMN tokens_used_period BIGINT NOT NULL DEFAULT 0")
                )
            if "quota_period_ym" not in columns:
                logger.warning("补齐缺失的租户配额周期列")
                conn.execute(text("ALTER TABLE tenants ADD COLUMN quota_period_ym VARCHAR"))
        if "rag_documents" in tables:
            columns = {column["name"] for column in inspect(conn).get_columns("rag_documents")}
            if "source_bytes" not in columns:
                logger.warning("补齐缺失的文档源文件字节列")
                conn.execute(text("ALTER TABLE rag_documents ADD COLUMN source_bytes BIGINT"))


def _ensure_evaluation_job_columns() -> None:
    """修订号已到最新时，给已有评测任务表补上真实资源评测列。"""

    expected_columns = {
        "job_kind": (
            "ALTER TABLE evaluation_jobs ADD COLUMN job_kind VARCHAR(32) "
            "NOT NULL DEFAULT 'internal_smoke'"
        ),
        "target_type": "ALTER TABLE evaluation_jobs ADD COLUMN target_type VARCHAR(32)",
        "target_ids_json": (
            "ALTER TABLE evaluation_jobs ADD COLUMN target_ids_json TEXT NOT NULL DEFAULT '[]'"
        ),
        "target_snapshot_json": (
            "ALTER TABLE evaluation_jobs ADD COLUMN target_snapshot_json TEXT NOT NULL DEFAULT '{}'"
        ),
        "adapter_name": "ALTER TABLE evaluation_jobs ADD COLUMN adapter_name VARCHAR(128)",
        "adapter_version": "ALTER TABLE evaluation_jobs ADD COLUMN adapter_version VARCHAR(64)",
        "metric_schema_version": (
            "ALTER TABLE evaluation_jobs ADD COLUMN metric_schema_version VARCHAR(64)"
        ),
        "execution_user_id": "ALTER TABLE evaluation_jobs ADD COLUMN execution_user_id VARCHAR",
        "execution_principal_json": (
            "ALTER TABLE evaluation_jobs ADD COLUMN execution_principal_json TEXT"
        ),
        "options_json": (
            "ALTER TABLE evaluation_jobs ADD COLUMN options_json TEXT NOT NULL DEFAULT '{}'"
        ),
        "budget_json": (
            "ALTER TABLE evaluation_jobs ADD COLUMN budget_json TEXT NOT NULL DEFAULT '{}'"
        ),
        "external_calls_used": (
            "ALTER TABLE evaluation_jobs ADD COLUMN external_calls_used INTEGER NOT NULL DEFAULT 0"
        ),
        "case_total": (
            "ALTER TABLE evaluation_jobs ADD COLUMN case_total INTEGER NOT NULL DEFAULT 0"
        ),
        "case_completed": (
            "ALTER TABLE evaluation_jobs ADD COLUMN case_completed INTEGER NOT NULL DEFAULT 0"
        ),
    }
    with nullcontext(op.get_bind()) as conn:
        tables = set(inspect(conn).get_table_names())
        if "evaluation_jobs" not in tables:
            return
        columns = {column["name"] for column in inspect(conn).get_columns("evaluation_jobs")}
        for name, ddl in expected_columns.items():
            if name not in columns:
                logger.warning("补齐缺失的评测任务列: %s", name)
                conn.execute(text(ddl))
        for index_sql in (
            "CREATE INDEX IF NOT EXISTS ix_evaluation_jobs_job_kind ON evaluation_jobs (job_kind)",
            "CREATE INDEX IF NOT EXISTS ix_evaluation_jobs_target_type ON evaluation_jobs (target_type)",
            "CREATE INDEX IF NOT EXISTS ix_evaluation_jobs_execution_user_id "
            "ON evaluation_jobs (execution_user_id)",
        ):
            conn.execute(text(index_sql))


def _ensure_rag_schema_columns() -> None:
    """为历史数据库补齐 RAG 版本化与导入平台所需列。

    某些 SQLite 开发库可能出现“修订号已更新，但表结构仍停留在旧版本”的情况。
    这里做一次幂等补齐，避免服务层在访问新增列时直接失败。
    """

    expected_columns = {
        "storage_path": "ALTER TABLE rag_documents ADD COLUMN storage_path VARCHAR",
        "source_kind": "ALTER TABLE rag_documents ADD COLUMN source_kind VARCHAR NOT NULL DEFAULT 'file'",
        "source_uri": "ALTER TABLE rag_documents ADD COLUMN source_uri VARCHAR",
        "content_hash": "ALTER TABLE rag_documents ADD COLUMN content_hash VARCHAR",
        "version_group_id": "ALTER TABLE rag_documents ADD COLUMN version_group_id VARCHAR",
        "version_number": "ALTER TABLE rag_documents ADD COLUMN version_number INTEGER NOT NULL DEFAULT 1",
        "previous_document_id": "ALTER TABLE rag_documents ADD COLUMN previous_document_id VARCHAR",
        "is_current": "ALTER TABLE rag_documents ADD COLUMN is_current BOOLEAN NOT NULL DEFAULT TRUE",
        "effective_at": "ALTER TABLE rag_documents ADD COLUMN effective_at TIMESTAMP",
        "expires_at": "ALTER TABLE rag_documents ADD COLUMN expires_at TIMESTAMP",
        "import_job_id": "ALTER TABLE rag_documents ADD COLUMN import_job_id VARCHAR",
        "deleted_at": "ALTER TABLE rag_documents ADD COLUMN deleted_at TIMESTAMP",
        "version_state": "ALTER TABLE rag_documents ADD COLUMN version_state VARCHAR NOT NULL DEFAULT 'published'",
    }
    with nullcontext(op.get_bind()) as conn:
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
                    "WHERE NOT is_current AND ("
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

        # 为历史库补齐分块元数据列（父子文档 / 策略 / 溯源 / 索引身份）。
        chunk_columns = {
            "index_id": "ALTER TABLE rag_document_chunks ADD COLUMN index_id VARCHAR",
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
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_rag_document_chunks_index_id "
                    "ON rag_document_chunks (index_id)"
                )
            )


def upgrade() -> None:
    _ensure_message_columns()
    _ensure_conversation_lifecycle_columns()
    _ensure_quota_columns()
    _ensure_evaluation_job_columns()
    _ensure_rag_schema_columns()


def downgrade() -> None:
    # Reconciled columns and stored values are retained on downgrade.
    pass

