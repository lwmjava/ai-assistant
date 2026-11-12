"""租户级彻底清除：业务数据、向量分块与源文件。

仅用于已确认的测试租户，且必须指向隔离数据库。默认只演练，不删除。
执行前写入人工确认记录并做租户快照；清除后写出验证报告。
历史审计先归档再删除，清除本身会留下新的审计记录。
本模块不是 Agent 工具，也不提供 HTTP 接口。
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func
from sqlmodel import Session, SQLModel, col, select

from app.audit.models import (
    AuditAction,
    AuditLog,
    WorkflowExecutionAlert,
    WorkflowExecutionLog,
    WorkflowOwnerAlert,
)
from app.models.agent_definition import AgentDefinition, AgentDefinitionVersion
from app.models.agent_state import AgentRunState
from app.models.approval import ActionApproval
from app.models.conversation import Conversation, Message
from app.models.cross_session_memory import CrossSessionMemory
from app.models.evaluation import (
    EvaluationCaseResult,
    EvaluationDataset,
    EvaluationDatasetVersion,
    EvaluationFeedbackCandidate,
    EvaluationJob,
)
from app.models.feedback import ChatAttempt, ChatFeedback, MessageRating
from app.models.membership import Invitation, Membership
from app.models.memory_snapshot import ConversationMemorySnapshot
from app.models.prompt_variant import PromptCompareRun
from app.models.rag import (
    Document,
    DocumentACL,
    DocumentACLGrant,
    DocumentACLProposal,
    DocumentChunk,
    DocumentIngestionSnapshot,
    ImportBatch,
    ImportJob,
    ImportJobTrace,
    OperationConfirmation,
    SectionSummary,
    SectionSummaryJob,
    VectorCleanupJob,
)
from app.models.run_history import RunHistory
from app.models.skill import Skill
from app.models.user import Tenant, User
from app.models.user_memory_preference import UserMemoryPreference
from app.models.workflow import Workflow, WorkflowExecution
from app.rag.document_storage import (
    delete_tenant_source_files,
    list_tenant_source_files,
    tenant_source_directory,
)

SHANGHAI = timezone(timedelta(hours=8))
TEST_TENANT_PREFIX = "test-"
_DEFAULT_DB_MARKERS = ("data/ai_assistant.db", "data\\ai_assistant.db")
_ALLOWED_AUDIT_ACTIONS = frozenset(
    {
        AuditAction.TENANT_PURGE.value,
        AuditAction.TENANT_PURGE_CONFIRMED.value,
    }
)
_SAFE_TENANT_ID = re.compile(r"^[0-9A-Za-z_-]+$")


class TenantPurgeError(ValueError):
    """拒绝清除：条件不满足或目标不安全。"""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


@dataclass
class TenantPurgeResult:
    """一次租户清除或演练的结果摘要，不含记录正文与连接信息。"""

    dry_run: bool
    tenant_id: str
    tenant_name: str | None
    verified: bool
    confirmation_path: Path | None = None
    backup_dir: Path | None = None
    report_path: Path | None = None
    audit_confirmation_id: str | None = None
    audit_purge_id: str | None = None
    planned_counts: dict[str, int] = field(default_factory=dict)
    deleted_counts: dict[str, int] = field(default_factory=dict)
    remaining_counts: dict[str, int] = field(default_factory=dict)
    files_planned: int = 0
    files_deleted: int = 0
    files_remaining: int = 0
    vector_backend: str = "local"


def shanghai_now() -> datetime:
    return datetime.now(SHANGHAI)


def iso_now() -> str:
    return shanghai_now().isoformat()


def _safe_tenant_id(tenant_id: str) -> str:
    if not _SAFE_TENANT_ID.fullmatch(tenant_id or ""):
        raise TenantPurgeError("invalid_tenant_id", "租户标识不合法")
    return tenant_id


def _redact_url(value: str) -> str:
    lowered = value.replace("\\", "/").lower()
    for marker in _DEFAULT_DB_MARKERS:
        if marker.replace("\\", "/") in lowered:
            return "<default-app-database>"
    if "://" in value:
        scheme, _, rest = value.partition("://")
        return f"{scheme}://<redacted>"
    return "<redacted>"


def assert_isolated_database(
    database_url: str,
    *,
    allow_isolated_database: bool,
    environment: str | None = None,
) -> None:
    """拒绝默认应用库与生产环境；须显式声明使用隔离数据库。"""
    if not allow_isolated_database:
        raise TenantPurgeError("isolated_database_required", "必须显式声明隔离数据库")
    env = (environment if environment is not None else os.environ.get("ENV", "")).strip().lower()
    if env in {"production", "prod"}:
        raise TenantPurgeError("production_environment_refused", "拒绝在生产环境执行清除")
    normalized = database_url.replace("\\", "/").lower()
    if any(marker.replace("\\", "/") in normalized for marker in _DEFAULT_DB_MARKERS):
        raise TenantPurgeError("default_database_refused", "拒绝默认应用数据库")


def import_persistence_models() -> None:
    """确保元数据注册全部持久化表。"""
    import app.audit.models  # noqa: F401
    import app.models.agent_definition  # noqa: F401
    import app.models.agent_state  # noqa: F401
    import app.models.approval  # noqa: F401
    import app.models.conversation  # noqa: F401
    import app.models.cross_session_memory  # noqa: F401
    import app.models.evaluation  # noqa: F401
    import app.models.feature_flag  # noqa: F401
    import app.models.feedback  # noqa: F401
    import app.models.membership  # noqa: F401
    import app.models.memory_snapshot  # noqa: F401
    import app.models.prompt_variant  # noqa: F401
    import app.models.rag  # noqa: F401
    import app.models.run_history  # noqa: F401
    import app.models.skill  # noqa: F401
    import app.models.user  # noqa: F401
    import app.models.user_memory_preference  # noqa: F401
    import app.models.workflow  # noqa: F401


def _tenant_models() -> list[tuple[str, type[SQLModel]]]:
    return [
        ("rag_section_summaries", SectionSummary),
        ("rag_section_summary_jobs", SectionSummaryJob),
        ("rag_document_chunks", DocumentChunk),
        ("rag_ingestion_snapshots", DocumentIngestionSnapshot),
        ("rag_document_acl_grants", DocumentACLGrant),
        ("rag_document_acl_proposals", DocumentACLProposal),
        ("rag_document_acl", DocumentACL),
        ("rag_vector_cleanup_jobs", VectorCleanupJob),
        ("rag_import_job_traces", ImportJobTrace),
        ("rag_import_jobs", ImportJob),
        ("rag_import_batches", ImportBatch),
        ("rag_operation_confirmations", OperationConfirmation),
        ("rag_documents", Document),
        ("workflow_executions", WorkflowExecution),
        ("workflow_execution_logs", WorkflowExecutionLog),
        ("workflow_execution_alerts", WorkflowExecutionAlert),
        ("workflow_owner_alerts", WorkflowOwnerAlert),
        ("workflows", Workflow),
        ("agent_run_states", AgentRunState),
        ("agent_definitions", AgentDefinition),
        ("action_approvals", ActionApproval),
        ("chat_feedbacks", ChatFeedback),
        ("message_ratings", MessageRating),
        ("chat_attempts", ChatAttempt),
        ("conversation_memory_snapshots", ConversationMemorySnapshot),
        ("cross_session_memories", CrossSessionMemory),
        ("user_memory_preferences", UserMemoryPreference),
        ("run_history", RunHistory),
        ("evaluation_case_results", EvaluationCaseResult),
        ("evaluation_jobs", EvaluationJob),
        ("evaluation_feedback_candidates", EvaluationFeedbackCandidate),
        ("evaluation_dataset_versions", EvaluationDatasetVersion),
        ("evaluation_datasets", EvaluationDataset),
        ("prompt_compare_runs", PromptCompareRun),
        ("skills", Skill),
        ("invitations", Invitation),
        ("memberships", Membership),
        ("audit_logs", AuditLog),
        ("conversations", Conversation),
        ("users", User),
    ]


def _count_query(session: Session, model: type[SQLModel], clause: Any) -> int:
    value = session.exec(select(func.count()).select_from(model).where(clause)).one()
    return int(value or 0)


def collect_tenant_counts(session: Session, tenant_id: str) -> dict[str, int]:
    """统计某租户在各表中的残留行数。"""
    tenant_id = _safe_tenant_id(tenant_id)
    counts: dict[str, int] = {}
    for name, model in _tenant_models():
        counts[name] = _count_query(session, model, model.tenant_id == tenant_id)  # type: ignore[attr-defined]
    conversation_ids = list(session.exec(select(Conversation.id).where(Conversation.tenant_id == tenant_id)))
    if conversation_ids:
        counts["messages"] = _count_query(session, Message, col(Message.conversation_id).in_(conversation_ids))
    else:
        counts["messages"] = 0
    definition_ids = list(session.exec(select(AgentDefinition.id).where(AgentDefinition.tenant_id == tenant_id)))
    if definition_ids:
        counts["agent_definition_versions"] = _count_query(
            session,
            AgentDefinitionVersion,
            col(AgentDefinitionVersion.definition_id).in_(definition_ids),
        )
    else:
        counts["agent_definition_versions"] = 0
    counts["tenants"] = _count_query(session, Tenant, Tenant.id == tenant_id)
    return counts


def remaining_business_counts(counts: dict[str, int], *, allowed_audit: int) -> dict[str, int]:
    leftover = dict(counts)
    leftover["audit_logs"] = max(0, leftover.get("audit_logs", 0) - allowed_audit)
    return leftover


def _assert_test_tenant(session: Session, tenant: Tenant) -> None:
    if not tenant.name.lower().startswith(TEST_TENANT_PREFIX):
        raise TenantPurgeError("non_test_tenant_refused", "只允许清除名称以 test- 开头的测试租户")
    users = list(session.exec(select(User).where(User.tenant_id == tenant.id)))
    if any(not user.is_test_user for user in users):
        raise TenantPurgeError("non_test_user_refused", "租户内存在非测试用户，已拒绝清除")
    user_ids = [user.id for user in users]
    if user_ids:
        other = session.exec(
            select(Membership).where(
                col(Membership.user_id).in_(user_ids),
                Membership.tenant_id != tenant.id,
            )
        ).first()
        if other is not None:
            raise TenantPurgeError("cross_tenant_membership_refused", "租户用户还属于其他租户，已拒绝清除")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _serialize_audit(record: AuditLog) -> dict[str, Any]:
    details = record.details
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except json.JSONDecodeError:
            pass
    created = record.created_at
    created_text = None
    if created is not None:
        if created.tzinfo is None:
            created_text = created.replace(tzinfo=UTC).astimezone(SHANGHAI).isoformat()
        else:
            created_text = created.astimezone(SHANGHAI).isoformat()
    return {
        "id": record.id,
        "action": record.action,
        "user_id": record.user_id,
        "tenant_id": record.tenant_id,
        "resource_type": record.resource_type,
        "resource_id": record.resource_id,
        "details": details,
        "created_at": created_text,
    }


def _backup_tenant(
    session: Session,
    tenant: Tenant,
    *,
    backup_dir: Path,
    knowledge_root: Path | None,
    counts: dict[str, int],
) -> None:
    backup_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        backup_dir / "inventory.json",
        {
            "tenant_id": tenant.id,
            "tenant_name": tenant.name,
            "created_at": iso_now(),
            "timezone": "Asia/Shanghai",
            "counts": counts,
        },
    )
    logs = list(session.exec(select(AuditLog).where(AuditLog.tenant_id == tenant.id)))
    archive = backup_dir / "audit-logs.jsonl"
    with archive.open("w", encoding="utf-8") as handle:
        for row in logs:
            handle.write(json.dumps(_serialize_audit(row), ensure_ascii=False, default=str))
            handle.write("\n")
    if knowledge_root is not None:
        source_dir = tenant_source_directory(tenant.id, root=knowledge_root)
        if source_dir.exists():
            destination = backup_dir / "knowledge"
            shutil.copytree(source_dir, destination, dirs_exist_ok=True)


def _delete_eval_report_files(session: Session, tenant_id: str, *, project_root: Path) -> int:
    removed = 0
    jobs = list(session.exec(select(EvaluationJob).where(EvaluationJob.tenant_id == tenant_id)))
    data_root = (project_root / "data").resolve()
    for job in jobs:
        if not job.report_path:
            continue
        candidate = Path(job.report_path)
        if not candidate.is_absolute():
            candidate = project_root / candidate
        try:
            resolved = candidate.resolve()
            resolved.relative_to(data_root)
        except (OSError, ValueError):
            continue
        if resolved.is_file():
            resolved.unlink()
            removed += 1
    return removed


def _delete_tenant_rows(session: Session, tenant_id: str) -> dict[str, int]:
    deleted: dict[str, int] = {}
    conversation_ids = list(session.exec(select(Conversation.id).where(Conversation.tenant_id == tenant_id)))
    if conversation_ids:
        deleted["messages"] = _delete_model(session, Message, col(Message.conversation_id).in_(conversation_ids))
    else:
        deleted["messages"] = 0
    definition_ids = list(session.exec(select(AgentDefinition.id).where(AgentDefinition.tenant_id == tenant_id)))
    if definition_ids:
        deleted["agent_definition_versions"] = _delete_model(
            session,
            AgentDefinitionVersion,
            col(AgentDefinitionVersion.definition_id).in_(definition_ids),
        )
    else:
        deleted["agent_definition_versions"] = 0
    for name, model in _tenant_models():
        if name in {"users", "conversations", "audit_logs"}:
            continue
        deleted[name] = _delete_model(session, model, model.tenant_id == tenant_id)  # type: ignore[attr-defined]
    deleted["conversations"] = _delete_model(session, Conversation, Conversation.tenant_id == tenant_id)
    deleted["audit_logs"] = _delete_model(session, AuditLog, AuditLog.tenant_id == tenant_id)
    deleted["users"] = _delete_model(session, User, User.tenant_id == tenant_id)
    deleted["tenants"] = _delete_model(session, Tenant, Tenant.id == tenant_id)
    return deleted


def _delete_model(session: Session, model: type[SQLModel], clause: Any) -> int:
    rows = list(session.exec(select(model).where(clause)))
    for row in rows:
        session.delete(row)
    return len(rows)


def _add_audit(
    session: Session,
    *,
    action: AuditAction,
    tenant_id: str,
    details: dict[str, Any],
    user_id: str | None = None,
) -> AuditLog:
    record = AuditLog(
        action=action.value,
        tenant_id=tenant_id,
        user_id=user_id,
        resource_type="tenant",
        resource_id=tenant_id,
        details=json.dumps(details, ensure_ascii=False),
    )
    session.add(record)
    session.flush()
    session.refresh(record)
    return record


def verify_tenant_purged(
    session: Session,
    tenant_id: str,
    *,
    knowledge_root: Path | None = None,
) -> dict[str, Any]:
    """检查租户业务数据是否已清除；允许保留本轮清除审计记录。"""
    tenant_id = _safe_tenant_id(tenant_id)
    counts = collect_tenant_counts(session, tenant_id)
    allowed_audit = 0
    if counts.get("audit_logs"):
        rows = list(session.exec(select(AuditLog).where(AuditLog.tenant_id == tenant_id)))
        unexpected = [row.action for row in rows if row.action not in _ALLOWED_AUDIT_ACTIONS]
        if unexpected:
            allowed_audit = 0
        else:
            allowed_audit = len(rows)
    leftover = remaining_business_counts(counts, allowed_audit=allowed_audit)
    files_remaining = 0
    if knowledge_root is not None:
        files_remaining = len(list_tenant_source_files(tenant_id, root=knowledge_root))
        directory = tenant_source_directory(tenant_id, root=knowledge_root)
        if directory.exists() and any(directory.iterdir()):
            files_remaining = max(files_remaining, 1)
    verified = files_remaining == 0 and all(value == 0 for value in leftover.values())
    return {
        "verified": verified,
        "remaining_counts": counts,
        "files_remaining": files_remaining,
        "allowed_audit": allowed_audit,
    }


def purge_tenant(
    session: Session,
    tenant_id: str,
    *,
    dry_run: bool = True,
    confirm_tenant_id: str | None = None,
    i_understand_irreversible: bool = False,
    confirmed_by: str | None = None,
    allow_isolated_database: bool = False,
    database_url: str,
    environment: str | None = None,
    report_dir: str | Path | None = None,
    backup_dir: str | Path | None = None,
    knowledge_root: str | Path | None = None,
    project_root: str | Path | None = None,
    vector_backend: str = "local",
) -> TenantPurgeResult:
    """对测试租户执行演练或彻底清除。"""
    tenant_id = _safe_tenant_id(tenant_id)
    assert_isolated_database(
        database_url,
        allow_isolated_database=allow_isolated_database,
        environment=environment,
    )
    import_persistence_models()
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise TenantPurgeError("tenant_not_found", "租户不存在")
    _assert_test_tenant(session, tenant)

    knowledge = Path(knowledge_root) if knowledge_root is not None else None
    files_planned = 0
    if knowledge is not None:
        files_planned = len(list_tenant_source_files(tenant.id, root=knowledge))

    planned = collect_tenant_counts(session, tenant.id)
    result = TenantPurgeResult(
        dry_run=dry_run,
        tenant_id=tenant.id,
        tenant_name=tenant.name,
        verified=False,
        planned_counts=planned,
        files_planned=files_planned,
        vector_backend=vector_backend,
    )
    if dry_run:
        if report_dir is not None:
            report_path = Path(report_dir) / f"tenant-purge-dry-run-{tenant.id}.json"
            _write_json(
                report_path,
                {
                    "dry_run": True,
                    "tenant_id": tenant.id,
                    "tenant_name": tenant.name,
                    "planned_counts": planned,
                    "files_planned": files_planned,
                    "vector_backend": vector_backend,
                    "created_at": iso_now(),
                    "timezone": "Asia/Shanghai",
                },
            )
            result.report_path = report_path
        return result

    if confirm_tenant_id != tenant.id:
        raise TenantPurgeError("confirmation_mismatch", "确认标识与目标租户不一致")
    if not i_understand_irreversible:
        raise TenantPurgeError("irreversible_not_acknowledged", "必须确认该操作不可逆")
    if report_dir is None:
        raise TenantPurgeError("report_dir_required", "执行清除必须指定报告目录")
    if knowledge is None:
        raise TenantPurgeError("knowledge_root_required", "执行清除必须指定知识库根目录")

    report_root = Path(report_dir)
    report_root.mkdir(parents=True, exist_ok=True)
    stamp = shanghai_now().strftime("%Y%m%dT%H%M%S")
    confirmation_path = report_root / f"confirmation-{tenant.id}-{stamp}.json"
    backup_root = Path(backup_dir) if backup_dir is not None else report_root / f"backup-{tenant.id}-{stamp}"
    _write_json(
        confirmation_path,
        {
            "confirmed_at": iso_now(),
            "timezone": "Asia/Shanghai",
            "tenant_id": tenant.id,
            "tenant_name": tenant.name,
            "confirmed_by": confirmed_by,
            "phrase": "irreversible tenant purge",
            "dry_run": False,
        },
    )
    result.confirmation_path = confirmation_path
    _backup_tenant(session, tenant, backup_dir=backup_root, knowledge_root=knowledge, counts=planned)
    result.backup_dir = backup_root

    project = Path(project_root) if project_root is not None else Path(__file__).resolve().parents[3]
    _delete_eval_report_files(session, tenant.id, project_root=project)
    deleted = _delete_tenant_rows(session, tenant.id)
    confirmation_record = _add_audit(
        session,
        action=AuditAction.TENANT_PURGE_CONFIRMED,
        tenant_id=tenant.id,
        user_id=None,
        details={
            "tenant_name": tenant.name,
            "confirmed_by": confirmed_by,
            "confirmation_file": confirmation_path.as_posix(),
            "backup_dir": backup_root.as_posix(),
        },
    )
    purge_record = _add_audit(
        session,
        action=AuditAction.TENANT_PURGE,
        tenant_id=tenant.id,
        user_id=None,
        details={
            "tenant_name": tenant.name,
            "deleted_counts": deleted,
            "files_planned": files_planned,
            "vector_backend": vector_backend,
            "confirmation_file": confirmation_path.as_posix(),
        },
    )
    session.commit()
    result.deleted_counts = deleted
    result.audit_confirmation_id = confirmation_record.id
    result.audit_purge_id = purge_record.id

    files_deleted = delete_tenant_source_files(tenant.id, root=knowledge)
    result.files_deleted = files_deleted
    check = verify_tenant_purged(session, tenant.id, knowledge_root=knowledge)
    result.remaining_counts = check["remaining_counts"]
    result.files_remaining = check["files_remaining"]
    result.verified = bool(check["verified"]) and files_deleted == files_planned
    report_path = report_root / f"tenant-purge-report-{tenant.id}-{stamp}.json"
    _write_json(
        report_path,
        {
            "dry_run": False,
            "verified": result.verified,
            "tenant_id": tenant.id,
            "tenant_name": tenant.name,
            "deleted_counts": deleted,
            "remaining_counts": result.remaining_counts,
            "files_planned": files_planned,
            "files_deleted": files_deleted,
            "files_remaining": result.files_remaining,
            "vector_backend": vector_backend,
            "audit_confirmation_id": result.audit_confirmation_id,
            "audit_purge_id": result.audit_purge_id,
            "confirmation_path": confirmation_path.as_posix(),
            "backup_dir": backup_root.as_posix(),
            "created_at": iso_now(),
            "timezone": "Asia/Shanghai",
        },
    )
    result.report_path = report_path
    return result


# 避免未使用告警：隔离检查的对外描述可被脚本引用。
def describe_database_target(database_url: str) -> str:
    return _redact_url(database_url)
