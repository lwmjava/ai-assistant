"""Tenant-bound catalog of real resources available to evaluation adapters."""

from __future__ import annotations

import hashlib

from sqlmodel import Session, col, select

from app.models.rag import Document, DocumentChunk, DocumentVersionState
from app.models.user import Tenant, User
from app.models.workflow import Workflow
from app.rag.index_registry import active_index


class EvaluationTargetNotFoundError(LookupError):
    """The target tenant is missing or inactive."""


class EvaluationTargetService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _tenant(self, tenant_id: str) -> Tenant:
        tenant = self.session.get(Tenant, tenant_id)
        if tenant is None or not tenant.is_active:
            raise EvaluationTargetNotFoundError(tenant_id)
        return tenant

    def list_targets(self, tenant_id: str, target_type: str) -> list[dict[str, object]]:
        self._tenant(tenant_id)
        if target_type == "rag":
            return self._rag_targets(tenant_id)
        if target_type == "agent":
            return [
                {
                    "id": "system-default",
                    "name": "系统默认 Agent",
                    "target_type": "agent",
                    "status": "available",
                    "metadata": {
                        "runtime": "AgentPipeline",
                        "execution_user_required": True,
                    },
                }
            ]
        if target_type == "workflow":
            return self._workflow_targets(tenant_id)
        raise ValueError("unsupported_target_type")

    def execution_users(self, tenant_id: str) -> list[dict[str, object]]:
        self._tenant(tenant_id)
        users = self.session.exec(
            select(User)
            .where(
                col(User.tenant_id) == tenant_id,
                col(User.is_active).is_(True),
                col(User.must_change_password).is_(False),
                col(User.role).in_(["tenant_admin", "member"]),
            )
            .order_by(col(User.username))
        ).all()
        return [
            {
                "id": user.id,
                "username": user.username,
                "role": user.role,
                "token_version": user.token_version,
            }
            for user in users
        ]

    def _rag_targets(self, tenant_id: str) -> list[dict[str, object]]:
        index = active_index(self.session)
        if index is None:
            return []
        documents = self.session.exec(
            select(Document)
            .where(
                col(Document.tenant_id) == tenant_id,
                col(Document.deleted_at).is_(None),
                col(Document.is_current).is_(True),
                col(Document.version_state) == DocumentVersionState.PUBLISHED.value,
            )
            .order_by(col(Document.title), col(Document.id))
        ).all()
        result: list[dict[str, object]] = []
        for document in documents:
            chunk_count = len(
                self.session.exec(
                    select(DocumentChunk.id).where(
                        col(DocumentChunk.tenant_id) == tenant_id,
                        col(DocumentChunk.document_id) == document.id,
                        col(DocumentChunk.index_id) == index.id,
                    )
                ).all()
            )
            if chunk_count == 0:
                continue
            result.append(
                {
                    "id": document.id,
                    "name": document.title,
                    "target_type": "rag",
                    "status": "available",
                    "metadata": {
                        "version_group_id": document.version_group_id,
                        "version_number": document.version_number,
                        "content_hash": document.content_hash,
                        "chunk_count": chunk_count,
                        "index_id": index.id,
                        "embedding_provider": index.provider,
                        "embedding_model": index.model,
                        "embedding_dim": index.dim,
                        "vector_backend": index.backend,
                    },
                }
            )
        return result

    def _workflow_targets(self, tenant_id: str) -> list[dict[str, object]]:
        workflows = self.session.exec(
            select(Workflow).where(col(Workflow.tenant_id) == tenant_id).order_by(col(Workflow.name), col(Workflow.id))
        ).all()
        result: list[dict[str, object]] = []
        for workflow in workflows:
            owner = self.session.get(User, workflow.owner_id)
            runnable = bool(
                owner is not None
                and owner.is_active
                and owner.tenant_id == tenant_id
                and not owner.must_change_password
            )
            result.append(
                {
                    "id": workflow.id,
                    "name": workflow.name,
                    "target_type": "workflow",
                    "status": "available" if runnable else "owner_unavailable",
                    "metadata": {
                        "owner_id": workflow.owner_id,
                        "enabled": workflow.enabled,
                        "timeout_seconds": workflow.timeout_seconds,
                        "webhook_configured": bool(workflow.webhook_url),
                        "prompt_hash": hashlib.sha256(workflow.prompt_template.encode()).hexdigest(),
                        "owner_role": owner.role if owner else None,
                        "owner_token_version": owner.token_version if owner else None,
                        "timezone": workflow.timezone,
                        "cron_expr": workflow.cron_expr,
                    },
                }
            )
        return result
