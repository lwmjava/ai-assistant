"""Authorized evaluation dataset versions and feedback regression candidates."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from sqlmodel import Session, col, select

from app.core.security import Role
from app.models.evaluation import (
    EvaluationDataset,
    EvaluationDatasetVersion,
    EvaluationFeedbackCandidate,
)
from app.models.feedback import ChatFeedback, MessageRating
from app.models.user import User

_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,118}[a-z0-9]$|^[a-z0-9]$")
_SPLITS = frozenset({"development", "validation", "holdout"})
_TIERS = frozenset({"gold", "silver", "adversarial"})
_FAMILIES = frozenset({"retrieval", "generation", "agent"})
_HOLDOUT_REDACT_KEYS = frozenset(
    {
        "expected_answer_points",
        "forbidden_answer_points",
        "expected_evidence",
        "expected_document_ids",
    }
)


class DatasetError(Exception):
    """Base domain error."""


class DatasetAccessError(DatasetError):
    """Missing and unauthorized resources share one public result."""


class DatasetValidationError(DatasetError):
    """Schema, status, or policy violation."""


class DatasetConflictError(DatasetError):
    """Duplicate or immutable conflict."""


def canonical_cases_hash(cases: list[dict[str, Any]]) -> str:
    payload = json.dumps(cases, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_cases(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise DatasetValidationError("cases must be a non-empty list")
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in raw:
        if not isinstance(row, dict):
            raise DatasetValidationError("each case must be an object")
        case_id = row.get("case_id")
        split = row.get("split")
        tier = row.get("tier")
        query = row.get("query")
        if not isinstance(case_id, str) or not case_id.strip():
            raise DatasetValidationError("case_id required")
        if case_id in seen:
            raise DatasetValidationError(f"duplicate case_id: {case_id}")
        seen.add(case_id)
        if split not in _SPLITS:
            raise DatasetValidationError(f"invalid split for {case_id}")
        if tier not in _TIERS:
            raise DatasetValidationError(f"invalid tier for {case_id}")
        if not isinstance(query, str) or len(query.strip()) < 4:
            raise DatasetValidationError(f"query too short for {case_id}")
        cases.append(dict(row))
    return cases


def _redact_holdout(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in cases:
        item = dict(row)
        if item.get("split") == "holdout":
            for key in _HOLDOUT_REDACT_KEYS:
                item.pop(key, None)
            item["holdout_redacted"] = True
        out.append(item)
    return out


class EvaluationDatasetService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _tenant_scope(self, user: User, tenant_id: str | None) -> str:
        if user.role == Role.SYSTEM_ADMIN.value:
            return tenant_id or user.tenant_id
        if tenant_id and tenant_id != user.tenant_id:
            raise DatasetAccessError
        return user.tenant_id

    def _can_write(self, user: User) -> bool:
        return user.role in {Role.SYSTEM_ADMIN.value, Role.TENANT_ADMIN.value}

    def get_dataset(self, user: User, dataset_id: str) -> EvaluationDataset:
        row = self.session.get(EvaluationDataset, dataset_id)
        if row is None:
            raise DatasetAccessError
        if user.role != Role.SYSTEM_ADMIN.value and row.tenant_id != user.tenant_id:
            raise DatasetAccessError
        return row

    def create_dataset(
        self,
        user: User,
        *,
        name: str,
        slug: str,
        family: str,
        description: str | None = None,
        tenant_id: str | None = None,
    ) -> EvaluationDataset:
        if not self._can_write(user):
            raise DatasetAccessError
        if family not in _FAMILIES:
            raise DatasetValidationError("invalid family")
        if not _SLUG_RE.fullmatch(slug):
            raise DatasetValidationError("invalid slug")
        name = name.strip()
        if not name or len(name) > 200:
            raise DatasetValidationError("invalid name")
        scope = self._tenant_scope(user, tenant_id)
        existing = self.session.exec(
            select(EvaluationDataset).where(
                col(EvaluationDataset.tenant_id) == scope,
                col(EvaluationDataset.slug) == slug,
            )
        ).first()
        if existing:
            raise DatasetConflictError("slug exists")
        row = EvaluationDataset(
            tenant_id=scope,
            name=name,
            slug=slug,
            family=family,
            created_by=user.id,
            description=description,
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def list_datasets(self, user: User, *, page: int, page_size: int) -> tuple[list[EvaluationDataset], int]:
        stmt = select(EvaluationDataset)
        if user.role != Role.SYSTEM_ADMIN.value:
            stmt = stmt.where(col(EvaluationDataset.tenant_id) == user.tenant_id)
        rows = list(self.session.exec(stmt.order_by(col(EvaluationDataset.created_at).desc())).all())
        total = len(rows)
        start = (page - 1) * page_size
        return rows[start : start + page_size], total

    def import_version(
        self,
        user: User,
        dataset_id: str,
        *,
        version: str,
        cases: list[dict[str, Any]],
        source: str = "upload",
        index: dict[str, Any] | None = None,
        is_frozen: bool = False,
    ) -> EvaluationDatasetVersion:
        if not self._can_write(user):
            raise DatasetAccessError
        dataset = self.get_dataset(user, dataset_id)
        if not _VERSION_RE.fullmatch(version):
            raise DatasetValidationError("invalid version")
        if source not in {"upload", "feedback", "ai_generated"}:
            raise DatasetValidationError("invalid source")
        if is_frozen and user.role != Role.SYSTEM_ADMIN.value:
            raise DatasetValidationError("only system_admin may mark frozen")
        parsed = _parse_cases(cases)
        if source == "ai_generated":
            for case in parsed:
                if case.get("tier") == "gold":
                    raise DatasetValidationError("ai_generated cannot be gold")
                case["tier"] = (
                    case.get("tier") if case.get("tier") in {"silver", "adversarial"} else "silver"
                )
        digest = canonical_cases_hash(parsed)
        clash = self.session.exec(
            select(EvaluationDatasetVersion).where(
                col(EvaluationDatasetVersion.dataset_id) == dataset.id,
                col(EvaluationDatasetVersion.version) == version,
            )
        ).first()
        if clash:
            raise DatasetConflictError("version exists")
        version_row = EvaluationDatasetVersion(
            dataset_id=dataset.id,
            tenant_id=dataset.tenant_id,
            version=version,
            status="draft",
            source=source,
            content_hash=digest,
            cases_json=json.dumps(parsed, ensure_ascii=False, sort_keys=True),
            index_json=json.dumps(index, ensure_ascii=False, sort_keys=True) if index else None,
            case_count=len(parsed),
            is_frozen=is_frozen,
            created_by=user.id,
        )
        self.session.add(version_row)
        self.session.commit()
        self.session.refresh(version_row)
        return version_row

    def list_versions(
        self, user: User, dataset_id: str, *, page: int, page_size: int
    ) -> tuple[list[EvaluationDatasetVersion], int]:
        dataset = self.get_dataset(user, dataset_id)
        rows = list(
            self.session.exec(
                select(EvaluationDatasetVersion)
                .where(col(EvaluationDatasetVersion.dataset_id) == dataset.id)
                .order_by(col(EvaluationDatasetVersion.created_at).desc())
            ).all()
        )
        total = len(rows)
        start = (page - 1) * page_size
        return rows[start : start + page_size], total

    def get_version(
        self, user: User, dataset_id: str, version_id: str, *, redact_holdout: bool | None = None
    ) -> tuple[EvaluationDataset, EvaluationDatasetVersion, list[dict[str, Any]]]:
        dataset = self.get_dataset(user, dataset_id)
        version = self.session.get(EvaluationDatasetVersion, version_id)
        if version is None or version.dataset_id != dataset.id:
            raise DatasetAccessError
        cases = json.loads(version.cases_json)
        if not isinstance(cases, list):
            raise DatasetValidationError("corrupt cases")
        typed = [dict(row) for row in cases if isinstance(row, dict)]
        hide = user.role != Role.SYSTEM_ADMIN.value if redact_holdout is None else redact_holdout
        if hide:
            typed = _redact_holdout(typed)
        return dataset, version, typed

    def submit_version(self, user: User, dataset_id: str, version_id: str) -> EvaluationDatasetVersion:
        if not self._can_write(user):
            raise DatasetAccessError
        _, version, _ = self.get_version(user, dataset_id, version_id, redact_holdout=False)
        if version.is_frozen:
            raise DatasetValidationError("frozen versions cannot be submitted")
        if version.status != "draft":
            raise DatasetValidationError("only draft can be submitted")
        version.status = "pending_review"
        self.session.add(version)
        self.session.commit()
        self.session.refresh(version)
        return version

    def review_version(
        self,
        user: User,
        dataset_id: str,
        version_id: str,
        *,
        decision: str,
        notes: str | None = None,
    ) -> EvaluationDatasetVersion:
        if not self._can_write(user):
            raise DatasetAccessError
        _, version, cases = self.get_version(user, dataset_id, version_id, redact_holdout=False)
        if version.is_frozen:
            raise DatasetValidationError("frozen versions are immutable")
        if version.status != "pending_review":
            raise DatasetValidationError("version not pending review")
        if decision == "reject":
            version.status = "rejected"
            version.review_decision = "REJECT"
        elif decision == "approve":
            if any(row.get("tier") == "gold" for row in cases):
                if user.role != Role.SYSTEM_ADMIN.value or notes != "ACCEPT_GOLD":
                    raise DatasetValidationError("gold requires system_admin ACCEPT_GOLD")
                version.review_decision = "ACCEPT_GOLD"
            else:
                version.review_decision = "APPROVE"
            if version.source == "ai_generated" and any(row.get("tier") == "gold" for row in cases):
                raise DatasetValidationError("ai_generated cannot become gold")
            version.status = "approved"
        else:
            raise DatasetValidationError("invalid decision")
        # Holdout may not be rewritten into tuning splits via review side-channel.
        for row in cases:
            if row.get("split") not in _SPLITS:
                raise DatasetValidationError("invalid split after review")
        version.reviewed_by = user.id
        version.review_notes = notes
        self.session.add(version)
        self.session.commit()
        self.session.refresh(version)
        return version

    def delete_version(self, user: User, dataset_id: str, version_id: str) -> None:
        if user.role != Role.SYSTEM_ADMIN.value:
            raise DatasetAccessError
        _, version, _ = self.get_version(user, dataset_id, version_id, redact_holdout=False)
        if version.is_frozen or version.status == "approved":
            raise DatasetValidationError("approved or frozen versions cannot be deleted")
        if version.status not in {"draft", "rejected"}:
            raise DatasetValidationError("only draft or rejected may be deleted")
        self.session.delete(version)
        self.session.commit()

    def nominate_candidate(
        self,
        user: User,
        *,
        source_type: str,
        source_id: str,
        target_dataset_id: str | None = None,
    ) -> EvaluationFeedbackCandidate:
        if not self._can_write(user):
            raise DatasetAccessError
        if source_type not in {"feedback", "rating"}:
            raise DatasetValidationError("invalid source_type")
        if source_type == "feedback":
            src = self.session.get(ChatFeedback, source_id)
            if src is None:
                raise DatasetAccessError
            if user.role != Role.SYSTEM_ADMIN.value and src.tenant_id != user.tenant_id:
                raise DatasetAccessError
            snapshot = {
                "kind": src.kind,
                "comment": src.comment,
                "attempt_id": src.attempt_id,
                "conversation_id": src.conversation_id,
                "source_user_id": src.user_id,
            }
            tenant_id = src.tenant_id
            attempt_id = src.attempt_id
            conversation_id = src.conversation_id
            source_user_id = src.user_id
        else:
            src_r = self.session.get(MessageRating, source_id)
            if src_r is None:
                raise DatasetAccessError
            if user.role != Role.SYSTEM_ADMIN.value and src_r.tenant_id != user.tenant_id:
                raise DatasetAccessError
            snapshot = {
                "rating": src_r.rating,
                "reason_code": src_r.reason_code,
                "comment": src_r.comment,
                "attempt_id": src_r.attempt_id,
                "conversation_id": src_r.conversation_id,
                "assistant_message_id": src_r.assistant_message_id,
                "source_user_id": src_r.user_id,
            }
            tenant_id = src_r.tenant_id
            attempt_id = src_r.attempt_id
            conversation_id = src_r.conversation_id
            source_user_id = src_r.user_id
        if target_dataset_id:
            ds = self.get_dataset(user, target_dataset_id)
            if ds.tenant_id != tenant_id and user.role != Role.SYSTEM_ADMIN.value:
                raise DatasetAccessError
        dup = self.session.exec(
            select(EvaluationFeedbackCandidate).where(
                col(EvaluationFeedbackCandidate.tenant_id) == tenant_id,
                col(EvaluationFeedbackCandidate.source_type) == source_type,
                col(EvaluationFeedbackCandidate.source_id) == source_id,
            )
        ).first()
        if dup:
            raise DatasetConflictError("candidate exists")
        row = EvaluationFeedbackCandidate(
            tenant_id=tenant_id,
            source_type=source_type,
            source_id=source_id,
            attempt_id=attempt_id,
            conversation_id=conversation_id,
            snapshot_json=json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
            nominated_by=user.id,
            source_user_id=source_user_id,
            target_dataset_id=target_dataset_id,
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def list_candidates(
        self, user: User, *, page: int, page_size: int, status: str | None = None
    ) -> tuple[list[EvaluationFeedbackCandidate], int]:
        stmt = select(EvaluationFeedbackCandidate)
        if user.role != Role.SYSTEM_ADMIN.value:
            stmt = stmt.where(col(EvaluationFeedbackCandidate.tenant_id) == user.tenant_id)
        if status:
            stmt = stmt.where(col(EvaluationFeedbackCandidate.status) == status)
        rows = list(
            self.session.exec(stmt.order_by(col(EvaluationFeedbackCandidate.created_at).desc())).all()
        )
        total = len(rows)
        start = (page - 1) * page_size
        return rows[start : start + page_size], total

    def review_candidate(
        self,
        user: User,
        candidate_id: str,
        *,
        decision: str,
        notes: str | None = None,
    ) -> EvaluationFeedbackCandidate:
        if not self._can_write(user):
            raise DatasetAccessError
        row = self.session.get(EvaluationFeedbackCandidate, candidate_id)
        if row is None:
            raise DatasetAccessError
        if user.role != Role.SYSTEM_ADMIN.value and row.tenant_id != user.tenant_id:
            raise DatasetAccessError
        if row.status != "pending":
            raise DatasetValidationError("candidate not pending")
        if decision == "accept":
            row.status = "accepted"
        elif decision == "reject":
            row.status = "rejected"
        elif decision == "withdraw":
            row.status = "withdrawn"
        else:
            raise DatasetValidationError("invalid decision")
        row.reviewed_by = user.id
        row.review_notes = notes
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row
