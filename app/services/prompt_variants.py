"""Offline prompt variant compare, manual publish, and rollback."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update
from sqlmodel import Session, col, select

from app.agents.prompts import (
    get_prompt_slot,
    list_prompt_slots,
    prompt_source_fingerprint,
)
from app.agents.version_registry import defaults as registry_defaults
from app.agents.version_registry import digest as registry_digest
from app.agents.version_registry import source_inventory
from app.models.evaluation import EvaluationDataset, EvaluationDatasetVersion
from app.models.prompt_variant import PromptCompareRun, PromptPublication, PromptVariant
from app.models.user import User
from app.schemas.prompt_variant import (
    PromptCompareOut,
    PromptPublicationOut,
    PromptSlotOut,
    PromptVariantOut,
)


class PromptVariantError(ValueError):
    """Validation or policy failure for prompt governance."""


class PromptVariantConflictError(RuntimeError):
    """Optimistic concurrency conflict on publish/rollback."""


class PromptVariantAuthError(PermissionError):
    """Caller is not allowed to publish or roll back."""


_METRIC_SCHEMA_VERSION = "prompt-compare-metrics-v1"
_PAID_PROVIDERS = {"live", "openai", "anthropic", "deepseek"}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _loads(raw: str) -> Any:
    return json.loads(raw)


def _variant_out(row: PromptVariant) -> PromptVariantOut:
    return PromptVariantOut(
        id=row.id,
        slot_key=row.slot_key,
        label=row.label,
        body=row.body,
        body_fingerprint=row.body_fingerprint,
        status=row.status,
        created_by=row.created_by,
        notes=row.notes,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _publication_out(row: PromptPublication) -> PromptPublicationOut:
    return PromptPublicationOut(
        slot_key=row.slot_key,
        active_variant_id=row.active_variant_id,
        previous_variant_id=row.previous_variant_id,
        revision=row.revision,
        published_by=row.published_by,
        published_at=row.published_at,
        note=row.note,
    )


def _require_publisher(user: User) -> None:
    if getattr(user, "role", None) != "system_admin":
        raise PromptVariantAuthError("only system_admin may publish or roll back prompts")


def _source_and_model_versions(root: Any | None = None) -> tuple[str, str]:
    from pathlib import Path

    base = Path(__file__).resolve().parents[2] if root is None else Path(root)
    sources = source_inventory(base)
    source_version = hashlib.sha256(
        _canonical(sources).encode("utf-8")
    ).hexdigest()
    model_values = registry_defaults(base)
    model_version = registry_digest(
        {key: model_values[key] for key in sorted(model_values) if not key.endswith("_URL") and "SOURCE" not in key}
    )
    return source_version, model_version


def _fake_case_score(variant_id: str, case_id: str) -> float:
    digest = hashlib.sha256(f"{variant_id}:{case_id}".encode()).hexdigest()
    return int(digest[:8], 16) % 1000 / 1000.0


def _summarize_variant(
    *,
    variant: PromptVariant,
    case_ids: list[str],
    provider: str,
) -> tuple[dict[str, Any], list[str], dict[str, Any], dict[str, Any]]:
    missing: list[str] = []
    metrics: dict[str, Any] = {
        "provider": provider,
        "case_count": len(case_ids),
        "scored_case_count": 0,
        "pass_rate": None,
        "mean_score": None,
        "claims_real_model_quality": False,
    }
    cost = {
        "currency": "USD",
        "amount": 0.0,
        "estimated_tokens": 0,
        "billing_mode": "fake_unmetered",
        "explain": "fake provider records zero billable spend; not a production cost proof",
    }
    calls = {
        "total": 0,
        "external": 0,
        "cache_hits": 0,
        "cache_misses": 0,
        "cancels": 0,
        "retries": 0,
        "explain": {
            "total": "synthetic stage invocations under fake provider",
            "external": "paid/network model calls; always 0 when provider=fake",
            "cache_hits": "responses reused from in-run cache",
            "cache_misses": "first-time synthetic evaluations",
            "cancels": "explicit cancel signals observed",
            "retries": "bounded retry attempts after transient fake errors",
        },
    }
    if not case_ids:
        missing.extend(["pass_rate", "mean_score"])
        metrics["notes"] = "no scored cases; missing metrics are omitted rather than recorded as 0"
        return metrics, missing, cost, calls

    scores = [_fake_case_score(variant.id, case_id) for case_id in case_ids]
    metrics["scored_case_count"] = len(scores)
    metrics["mean_score"] = round(sum(scores) / len(scores), 4)
    metrics["pass_rate"] = round(sum(1 for score in scores if score >= 0.5) / len(scores), 4)
    # Deterministic fake traffic accounting
    calls["total"] = len(scores)
    calls["cache_misses"] = max(1, len(scores) // 2)
    calls["cache_hits"] = len(scores) - calls["cache_misses"]
    calls["retries"] = 1 if len(scores) > 3 else 0
    calls["cancels"] = 0
    cost["estimated_tokens"] = len(variant.body) + sum(len(case_id) for case_id in case_ids)
    # Semantic metrics that fake runner cannot prove stay missing (not zero).
    for name in ("human_acceptance_rate", "holdout_score", "production_latency_p95_ms"):
        missing.append(name)
    return metrics, missing, cost, calls


class PromptVariantService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_slots(self) -> list[PromptSlotOut]:
        pubs = {
            row.slot_key: row
            for row in self.session.exec(select(PromptPublication)).all()
        }
        out: list[PromptSlotOut] = []
        for slot in list_prompt_slots():
            pub = pubs.get(slot["key"])
            out.append(
                PromptSlotOut(
                    key=slot["key"],
                    title=slot["title"],
                    source_fingerprint=prompt_source_fingerprint(
                        slot["key"], slot["source_body"]
                    ),
                    published_variant_id=None if pub is None else pub.active_variant_id,
                    publication_revision=None if pub is None else pub.revision,
                )
            )
        return out

    def create_variant(
        self,
        user: User,
        *,
        slot_key: str,
        label: str,
        body: str,
        notes: str | None = None,
    ) -> PromptVariantOut:
        if get_prompt_slot(slot_key) is None:
            raise PromptVariantError("unknown_slot")
        label = label.strip()
        body = body.strip()
        if not label or not body:
            raise PromptVariantError("label_and_body_required")
        existing = self.session.exec(
            select(PromptVariant).where(
                PromptVariant.slot_key == slot_key,
                PromptVariant.label == label,
            )
        ).first()
        if existing is not None:
            raise PromptVariantError("duplicate_label")
        row = PromptVariant(
            slot_key=slot_key,
            label=label,
            body=body,
            body_fingerprint=prompt_source_fingerprint(slot_key, body),
            status="draft",
            created_by=user.id,
            notes=notes,
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _variant_out(row)

    def list_variants(self, slot_key: str) -> list[PromptVariantOut]:
        if get_prompt_slot(slot_key) is None:
            raise PromptVariantError("unknown_slot")
        rows = self.session.exec(
            select(PromptVariant)
            .where(PromptVariant.slot_key == slot_key)
            .order_by(col(PromptVariant.created_at).desc())
        ).all()
        return [_variant_out(row) for row in rows]

    def get_variant(self, variant_id: str) -> PromptVariantOut:
        row = self.session.get(PromptVariant, variant_id)
        if row is None:
            raise PromptVariantError("variant_not_found")
        return _variant_out(row)

    def get_publication(self, slot_key: str) -> PromptPublicationOut | None:
        if get_prompt_slot(slot_key) is None:
            raise PromptVariantError("unknown_slot")
        row = self.session.exec(
            select(PromptPublication).where(PromptPublication.slot_key == slot_key)
        ).first()
        return None if row is None else _publication_out(row)

    def resolve_published_body(self, slot_key: str) -> str:
        slot = get_prompt_slot(slot_key)
        if slot is None:
            raise PromptVariantError("unknown_slot")
        pub = self.session.exec(
            select(PromptPublication).where(PromptPublication.slot_key == slot_key)
        ).first()
        if pub is None:
            return slot["source_body"]
        variant = self.session.get(PromptVariant, pub.active_variant_id)
        if variant is None or variant.status == "archived":
            return slot["source_body"]
        return variant.body

    def compare(
        self,
        user: User,
        *,
        slot_key: str,
        baseline_variant_id: str,
        candidate_variant_id: str,
        dataset_id: str,
        dataset_version_id: str,
        provider: str = "fake",
        max_external_calls: int = 0,
    ) -> PromptCompareOut:
        if get_prompt_slot(slot_key) is None:
            raise PromptVariantError("unknown_slot")
        if provider != "fake" or provider in _PAID_PROVIDERS:
            raise PromptVariantError("paid_compare_not_authorized")
        if max_external_calls != 0:
            raise PromptVariantError("external_calls_not_authorized")
        if baseline_variant_id == candidate_variant_id:
            raise PromptVariantError("variants_must_differ")

        baseline = self.session.get(PromptVariant, baseline_variant_id)
        candidate = self.session.get(PromptVariant, candidate_variant_id)
        if baseline is None or candidate is None:
            raise PromptVariantError("variant_not_found")
        if baseline.slot_key != slot_key or candidate.slot_key != slot_key:
            raise PromptVariantError("variant_slot_mismatch")

        dataset = self.session.get(EvaluationDataset, dataset_id)
        version = self.session.get(EvaluationDatasetVersion, dataset_version_id)
        if dataset is None or version is None:
            raise PromptVariantError("dataset_version_not_found")
        if dataset.tenant_id != user.tenant_id or version.tenant_id != user.tenant_id:
            raise PromptVariantError("dataset_tenant_mismatch")
        if version.dataset_id != dataset.id:
            raise PromptVariantError("dataset_version_mismatch")

        cases = _loads(version.cases_json)
        if not isinstance(cases, list):
            raise PromptVariantError("dataset_cases_invalid")
        case_ids = [
            str(case.get("id") or case.get("case_id") or f"case-{index}")
            for index, case in enumerate(cases)
            if isinstance(case, dict)
        ]

        source_version, model_version = _source_and_model_versions()
        baseline_metrics, missing_a, cost_a, calls_a = _summarize_variant(
            variant=baseline, case_ids=case_ids, provider=provider
        )
        candidate_metrics, missing_b, cost_b, calls_b = _summarize_variant(
            variant=candidate, case_ids=case_ids, provider=provider
        )
        missing = sorted(set(missing_a) | set(missing_b))

        diff: dict[str, Any] = {
            "body_changed": baseline.body_fingerprint != candidate.body_fingerprint,
            "baseline_label": baseline.label,
            "candidate_label": candidate.label,
            "mean_score_delta": None,
            "pass_rate_delta": None,
            "winner": None,
            "auto_publish": False,
            "notes": "comparison is advisory only; publish requires an explicit authorized action",
        }
        if (
            baseline_metrics.get("mean_score") is not None
            and candidate_metrics.get("mean_score") is not None
        ):
            diff["mean_score_delta"] = round(
                float(candidate_metrics["mean_score"]) - float(baseline_metrics["mean_score"]),
                4,
            )
            diff["pass_rate_delta"] = round(
                float(candidate_metrics["pass_rate"]) - float(baseline_metrics["pass_rate"]),
                4,
            )
            if diff["mean_score_delta"] > 0:
                diff["winner"] = "candidate"
            elif diff["mean_score_delta"] < 0:
                diff["winner"] = "baseline"
            else:
                diff["winner"] = "tie"

        cost = {
            "baseline": cost_a,
            "candidate": cost_b,
            "total_amount": 0.0,
            "currency": "USD",
            "explain": "sums fake unmetered amounts only; unpaid offline compare",
        }
        call_stats = {
            "baseline": calls_a,
            "candidate": calls_b,
            "external_calls_used": 0,
            "explain": "fake provider never performs paid external calls",
        }
        binding = {
            "dataset_id": dataset.id,
            "dataset_slug": dataset.slug,
            "dataset_version_id": version.id,
            "dataset_version": version.version,
            "dataset_content_hash": version.content_hash,
            "source_version": source_version,
            "model_version": model_version,
            "prompt_slot": slot_key,
            "baseline_variant_id": baseline.id,
            "baseline_fingerprint": baseline.body_fingerprint,
            "candidate_variant_id": candidate.id,
            "candidate_fingerprint": candidate.body_fingerprint,
            "evaluation_version": _METRIC_SCHEMA_VERSION,
            "provider": provider,
        }
        status = "succeeded" if case_ids else "incomplete"
        row = PromptCompareRun(
            tenant_id=user.tenant_id,
            created_by=user.id,
            slot_key=slot_key,
            baseline_variant_id=baseline.id,
            candidate_variant_id=candidate.id,
            dataset_id=dataset.id,
            dataset_version_id=version.id,
            dataset_version=version.version,
            source_version=source_version,
            model_version=model_version,
            evaluation_version=_METRIC_SCHEMA_VERSION,
            provider=provider,
            status=status,
            max_external_calls=max_external_calls,
            external_calls_used=0,
            binding_json=_canonical(binding),
            baseline_metrics_json=_canonical(baseline_metrics),
            candidate_metrics_json=_canonical(candidate_metrics),
            missing_metrics_json=_canonical(missing),
            diff_json=_canonical(diff),
            cost_json=_canonical(cost),
            call_stats_json=_canonical(call_stats),
            auto_publish_blocked=True,
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return self._compare_out(row)

    def get_compare(self, user: User, compare_id: str) -> PromptCompareOut:
        row = self.session.get(PromptCompareRun, compare_id)
        if row is None or row.tenant_id != user.tenant_id:
            raise PromptVariantError("compare_not_found")
        return self._compare_out(row)

    def publish(
        self,
        user: User,
        *,
        slot_key: str,
        variant_id: str,
        expected_revision: int,
        note: str | None = None,
        compare_run_id: str | None = None,
    ) -> PromptPublicationOut:
        _require_publisher(user)
        if get_prompt_slot(slot_key) is None:
            raise PromptVariantError("unknown_slot")
        variant = self.session.get(PromptVariant, variant_id)
        if variant is None or variant.slot_key != slot_key:
            raise PromptVariantError("variant_not_found")
        if variant.status != "draft":
            raise PromptVariantError("variant_not_publishable")
        if compare_run_id is not None:
            compare = self.session.get(PromptCompareRun, compare_run_id)
            if compare is None or compare.tenant_id != user.tenant_id:
                raise PromptVariantError("compare_not_found")
            if compare.slot_key != slot_key:
                raise PromptVariantError("compare_slot_mismatch")
            # Compare may inform humans; never auto-select. Explicit variant_id wins.
            if compare.auto_publish_blocked is not True:
                raise PromptVariantError("compare_auto_publish_invariant")

        pub = self.session.exec(
            select(PromptPublication).where(PromptPublication.slot_key == slot_key)
        ).first()
        now = datetime.now(UTC)
        if pub is None:
            if expected_revision != 0:
                raise PromptVariantConflictError("publication_revision_conflict")
            pub = PromptPublication(
                slot_key=slot_key,
                active_variant_id=variant.id,
                previous_variant_id=None,
                revision=1,
                published_by=user.id,
                published_at=now,
                note=note,
            )
            self.session.add(pub)
            self.session.commit()
            self.session.refresh(pub)
            return _publication_out(pub)

        with self.session.no_autoflush:
            result = self.session.execute(
                update(PromptPublication)
                .where(
                    col(PromptPublication.id) == pub.id,
                    col(PromptPublication.revision) == expected_revision,
                )
                .values(
                    previous_variant_id=pub.active_variant_id,
                    active_variant_id=variant.id,
                    revision=expected_revision + 1,
                    published_by=user.id,
                    published_at=now,
                    note=note,
                )
            )
        if getattr(result, "rowcount", 0) != 1:
            raise PromptVariantConflictError("publication_revision_conflict")
        self.session.commit()
        self.session.refresh(pub)
        return _publication_out(pub)

    def rollback(
        self,
        user: User,
        *,
        slot_key: str,
        expected_revision: int,
        to_variant_id: str | None = None,
        note: str | None = None,
    ) -> PromptPublicationOut:
        _require_publisher(user)
        if get_prompt_slot(slot_key) is None:
            raise PromptVariantError("unknown_slot")
        pub = self.session.exec(
            select(PromptPublication).where(PromptPublication.slot_key == slot_key)
        ).first()
        if pub is None:
            raise PromptVariantError("publication_not_found")
        target_id = to_variant_id or pub.previous_variant_id
        if not target_id:
            raise PromptVariantError("rollback_target_missing")
        target = self.session.get(PromptVariant, target_id)
        if target is None or target.slot_key != slot_key:
            raise PromptVariantError("rollback_target_invalid")
        if target.id == pub.active_variant_id:
            raise PromptVariantError("rollback_noop")

        now = datetime.now(UTC)
        with self.session.no_autoflush:
            result = self.session.execute(
                update(PromptPublication)
                .where(
                    col(PromptPublication.id) == pub.id,
                    col(PromptPublication.revision) == expected_revision,
                )
                .values(
                    previous_variant_id=pub.active_variant_id,
                    active_variant_id=target.id,
                    revision=expected_revision + 1,
                    published_by=user.id,
                    published_at=now,
                    note=note or f"rollback_to:{target.id}",
                )
            )
        if getattr(result, "rowcount", 0) != 1:
            raise PromptVariantConflictError("publication_revision_conflict")
        self.session.commit()
        self.session.refresh(pub)
        return _publication_out(pub)

    def _compare_out(self, row: PromptCompareRun) -> PromptCompareOut:
        return PromptCompareOut(
            id=row.id,
            slot_key=row.slot_key,
            baseline_variant_id=row.baseline_variant_id,
            candidate_variant_id=row.candidate_variant_id,
            status=row.status,
            provider=row.provider,
            binding=_loads(row.binding_json),
            baseline_metrics=_loads(row.baseline_metrics_json),
            candidate_metrics=_loads(row.candidate_metrics_json),
            missing_metrics=list(_loads(row.missing_metrics_json)),
            diff=_loads(row.diff_json),
            cost=_loads(row.cost_json),
            call_stats=_loads(row.call_stats_json),
            auto_publish_blocked=row.auto_publish_blocked,
            external_calls_used=row.external_calls_used,
            max_external_calls=row.max_external_calls,
            created_at=row.created_at,
        )
