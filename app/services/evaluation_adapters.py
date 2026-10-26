"""Built-in adapters that freeze real evaluation targets into immutable snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlmodel import Session

from app.services.evaluation_targets import EvaluationTargetService


class EvaluationAdapterError(ValueError):
    """A target cannot be evaluated through the selected adapter."""


@dataclass(frozen=True)
class EvaluationTargetSnapshot:
    target_type: str
    adapter_name: str
    adapter_version: str
    metric_schema_version: str
    targets: list[dict[str, object]]
    execution_principal: dict[str, object] | None = None


class EvaluationAdapter(Protocol):
    target_type: str
    name: str
    version: str
    metric_schema_version: str

    def snapshot(
        self,
        session: Session,
        *,
        tenant_id: str,
        target_ids: list[str],
        execution_user_id: str | None,
    ) -> EvaluationTargetSnapshot: ...


class EvaluationAdapterRegistry:
    """Explicit registry boundary; plugin loading can be added here under policy control."""

    def __init__(self, adapters: list[EvaluationAdapter]) -> None:
        self._adapters: dict[str, EvaluationAdapter] = {}
        for adapter in adapters:
            if adapter.target_type in self._adapters:
                raise EvaluationAdapterError(f"duplicate_adapter:{adapter.target_type}")
            self._adapters[adapter.target_type] = adapter

    def get(self, target_type: str) -> EvaluationAdapter:
        try:
            return self._adapters[target_type]
        except KeyError as exc:
            raise EvaluationAdapterError("unsupported_target_type") from exc

    def descriptors(self) -> list[dict[str, str]]:
        return [
            {
                "target_type": adapter.target_type,
                "name": adapter.name,
                "version": adapter.version,
                "metric_schema_version": adapter.metric_schema_version,
            }
            for adapter in self._adapters.values()
        ]


class _CatalogAdapter:
    target_type = ""
    name = ""
    version = "1.0.0"
    metric_schema_version = "evaluation-metrics-v1"

    def _targets(
        self, session: Session, tenant_id: str, target_ids: list[str]
    ) -> list[dict[str, object]]:
        if not target_ids or len(target_ids) != len(set(target_ids)):
            raise EvaluationAdapterError("invalid_target_selection")
        available = {
            str(item["id"]): item
            for item in EvaluationTargetService(session).list_targets(tenant_id, self.target_type)
        }
        try:
            selected = [available[target_id] for target_id in target_ids]
        except KeyError as exc:
            raise EvaluationAdapterError("target_unavailable") from exc
        if any(item["status"] != "available" for item in selected):
            raise EvaluationAdapterError("target_unavailable")
        return selected


class RagEvaluationAdapter(_CatalogAdapter):
    target_type = "rag"
    name = "builtin.rag"

    def snapshot(
        self,
        session: Session,
        *,
        tenant_id: str,
        target_ids: list[str],
        execution_user_id: str | None,
    ) -> EvaluationTargetSnapshot:
        if execution_user_id is not None:
            raise EvaluationAdapterError("execution_user_not_allowed")
        return EvaluationTargetSnapshot(
            target_type=self.target_type,
            adapter_name=self.name,
            adapter_version=self.version,
            metric_schema_version=self.metric_schema_version,
            targets=self._targets(session, tenant_id, target_ids),
        )


class AgentEvaluationAdapter(_CatalogAdapter):
    target_type = "agent"
    name = "builtin.agent"

    def snapshot(
        self,
        session: Session,
        *,
        tenant_id: str,
        target_ids: list[str],
        execution_user_id: str | None,
    ) -> EvaluationTargetSnapshot:
        targets = self._targets(session, tenant_id, target_ids)
        if target_ids != ["system-default"]:
            raise EvaluationAdapterError("invalid_agent_target")
        users = {
            str(item["id"]): item
            for item in EvaluationTargetService(session).execution_users(tenant_id)
        }
        principal = users.get(execution_user_id or "")
        if principal is None:
            raise EvaluationAdapterError("execution_user_unavailable")
        return EvaluationTargetSnapshot(
            target_type=self.target_type,
            adapter_name=self.name,
            adapter_version=self.version,
            metric_schema_version=self.metric_schema_version,
            targets=targets,
            execution_principal=principal,
        )


class WorkflowEvaluationAdapter(_CatalogAdapter):
    target_type = "workflow"
    name = "builtin.workflow"

    def snapshot(
        self,
        session: Session,
        *,
        tenant_id: str,
        target_ids: list[str],
        execution_user_id: str | None,
    ) -> EvaluationTargetSnapshot:
        if execution_user_id is not None:
            raise EvaluationAdapterError("execution_user_not_allowed")
        return EvaluationTargetSnapshot(
            target_type=self.target_type,
            adapter_name=self.name,
            adapter_version=self.version,
            metric_schema_version=self.metric_schema_version,
            targets=self._targets(session, tenant_id, target_ids),
        )


builtin_evaluation_adapters = EvaluationAdapterRegistry(
    [RagEvaluationAdapter(), AgentEvaluationAdapter(), WorkflowEvaluationAdapter()]
)
