"""Authorized evaluation dataset versions and feedback regression candidates."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, col, select

from app.core.security import Role
from app.models.evaluation import (
    EvaluationDataset,
    EvaluationDatasetVersion,
    EvaluationFeedbackCandidate,
)
from app.models.feedback import ChatFeedback, MessageRating
from app.models.rag import Document, DocumentChunk, DocumentVersionState
from app.models.user import User

_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,118}[a-z0-9]$|^[a-z0-9]$")
_SPLITS = frozenset({"development", "validation", "holdout"})
_TIERS = frozenset({"gold", "silver", "adversarial"})
_FAMILIES = frozenset({"retrieval", "generation", "agent", "workflow"})
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



DOC_QUESTION_SCHEMA = "doc-assisted-question-candidate-v1"
DOC_QUESTION_PROMPT_VERSION = "doc-questions-v1"
DOC_QUESTION_GENERATOR = "fake-local-v1"
_CANDIDATE_STATUSES = frozenset({"pending", "edited", "accepted", "rejected"})
_REDACT_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[REDACTED_EMAIL]"),
    (re.compile(r"(?<!\d)(?:1\d{10}|\d{3}[-.\s]?\d{4}[-.\s]?\d{4})(?!\d)"), "[REDACTED_PHONE]"),
    (re.compile(r"(?i)\b(?:sk|pk|api)[_-][A-Za-z0-9_]{16,}\b"), "[REDACTED_SECRET]"),
)


def redact_doc_fragment(text: str) -> str:
    """Strip common secrets/PII markers from fragments before persistence or egress."""
    out = text
    for pattern, replacement in _REDACT_RULES:
        out = pattern.sub(replacement, out)
    return out


def fake_doc_question_cases(
    *,
    fragments: list[dict[str, Any]],
    max_cases: int,
    generation_config: dict[str, Any],
) -> tuple[list[dict[str, Any]], int]:
    """Deterministic local generator: never performs paid/network model calls."""
    cases: list[dict[str, Any]] = []
    for fragment in fragments:
        if len(cases) >= max_cases:
            break
        evidence = redact_doc_fragment(str(fragment["evidence"]))
        compact = " ".join(evidence.split())
        if len(compact) < 12:
            continue
        case_id = f"docq-{fragment['chunk_id']}"
        cases.append(
            {
                "case_id": case_id,
                "split": "development",
                "tier": "silver",
                "query": f"根据授权资料，说明以下内容的要点：{compact[:100]}",
                "expected_document_ids": [fragment["document_id"]],
                "expected_evidence": compact[:240],
                "expected_answer_points": [compact[:120]],
                "candidate_status": "pending",
                "unavailable_reason": None,
                "provenance": {
                    "source_document_id": fragment["document_id"],
                    "source_version_number": fragment["version_number"],
                    "source_content_hash": fragment["content_hash"],
                    "source_chunk_id": fragment["chunk_id"],
                    "fragment": compact[:240],
                    "generator": DOC_QUESTION_GENERATOR,
                    "prompt_version": DOC_QUESTION_PROMPT_VERSION,
                    "generation_config": generation_config,
                    "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
                },
            }
        )
    return cases, 0


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

    def _load_index(self, version: EvaluationDatasetVersion) -> dict[str, Any]:
        if not version.index_json:
            return {}
        raw = json.loads(version.index_json)
        return raw if isinstance(raw, dict) else {}

    def _authorized_doc_fragments(
        self,
        user: User,
        *,
        document_ids: list[str],
        max_cases: int,
    ) -> list[dict[str, Any]]:
        if not document_ids:
            raise DatasetValidationError("document_ids required")
        if len(document_ids) > 50:
            raise DatasetValidationError("too many documents")
        scope = self._tenant_scope(user, None)
        fragments: list[dict[str, Any]] = []
        for document_id in document_ids:
            document = self.session.get(Document, document_id)
            if (
                document is None
                or document.tenant_id != scope
                or document.deleted_at is not None
                or not document.is_current
                or document.version_state != DocumentVersionState.PUBLISHED.value
            ):
                raise DatasetAccessError
            chunks = list(
                self.session.exec(
                    select(DocumentChunk)
                    .where(
                        col(DocumentChunk.tenant_id) == scope,
                        col(DocumentChunk.document_id) == document.id,
                        col(DocumentChunk.parent_id).is_(None),
                    )
                    .order_by(col(DocumentChunk.chunk_index))
                    .limit(max(1, max_cases))
                ).all()
            )
            if not chunks:
                raise DatasetValidationError(f"no_chunks:{document.id}")
            for chunk in chunks:
                evidence = " ".join(chunk.content.split())
                if len(evidence) < 12:
                    continue
                fragments.append(
                    {
                        "document_id": document.id,
                        "version_number": document.version_number,
                        "content_hash": document.content_hash,
                        "chunk_id": chunk.id,
                        "evidence": evidence,
                    }
                )
                if len(fragments) >= max_cases * 3:
                    break
            if len(fragments) >= max_cases * 3:
                break
        if not fragments:
            raise DatasetValidationError("no_grounded_fragments")
        return fragments

    def _refresh_case_availability(
        self, user: User, cases: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        scope = self._tenant_scope(user, None)
        refreshed: list[dict[str, Any]] = []
        for row in cases:
            item = dict(row)
            provenance = item.get("provenance")
            if not isinstance(provenance, dict):
                item["unavailable_reason"] = "source_invalid"
                refreshed.append(item)
                continue
            document_id = provenance.get("source_document_id")
            document = self.session.get(Document, document_id) if isinstance(document_id, str) else None
            reason = None
            if (
                document is None
                or document.tenant_id != scope
                or document.deleted_at is not None
            ):
                reason = "source_revoked"
            elif (
                not document.is_current
                or document.version_state != DocumentVersionState.PUBLISHED.value
                or document.version_number != provenance.get("source_version_number")
                or document.content_hash != provenance.get("source_content_hash")
            ):
                reason = "source_invalid"
            item["unavailable_reason"] = reason
            if reason and item.get("candidate_status") == "accepted":
                # Accepted but source lost: keep audit status, block promote via availability.
                pass
            refreshed.append(item)
        return refreshed

    def generate_doc_questions(
        self,
        user: User,
        dataset_id: str,
        *,
        document_ids: list[str],
        idempotency_key: str,
        max_cases: int = 20,
        max_external_calls: int = 0,
        data_egress_confirmed: bool = False,
        provider: str = "fake",
        generator: Callable[..., tuple[list[dict[str, Any]], int]] | None = None,
    ) -> EvaluationDatasetVersion:
        """Create a draft AI-generated candidate version bound to authorized documents."""
        if not self._can_write(user):
            raise DatasetAccessError
        if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 128:
            raise DatasetValidationError("invalid idempotency_key")
        if max_cases < 1 or max_cases > 100:
            raise DatasetValidationError("invalid max_cases")
        if max_external_calls < 0 or max_external_calls > 1000:
            raise DatasetValidationError("invalid max_external_calls")
        if provider not in {"fake", "live"}:
            raise DatasetValidationError("invalid provider")
        dataset = self.get_dataset(user, dataset_id)

        # Idempotent replay for the same dataset + key.
        existing_rows = list(
            self.session.exec(
                select(EvaluationDatasetVersion).where(
                    col(EvaluationDatasetVersion.dataset_id) == dataset.id,
                    col(EvaluationDatasetVersion.source) == "ai_generated",
                )
            ).all()
        )
        for row in existing_rows:
            index = self._load_index(row)
            if index.get("idempotency_key") == idempotency_key and index.get("kind") == "doc_questions":
                return row

        if provider == "live" and (not data_egress_confirmed or max_external_calls < 1):
            raise DatasetValidationError("live_provider_requires_egress_and_budget")
        if provider == "live" and generator is None:
            # Paid egress is out of scope for this delivery; refuse rather than silently charge.
            raise DatasetValidationError("live_provider_not_authorized")

        fragments = self._authorized_doc_fragments(
            user, document_ids=document_ids, max_cases=max_cases
        )
        generation_config = {
            "provider": provider,
            "max_cases": max_cases,
            "max_external_calls": max_external_calls,
            "data_egress_confirmed": bool(data_egress_confirmed),
            "prompt_version": DOC_QUESTION_PROMPT_VERSION,
        }
        active_generator = generator or fake_doc_question_cases
        cases, calls_used = active_generator(
            fragments=fragments,
            max_cases=max_cases,
            generation_config=generation_config,
        )
        if calls_used > max_external_calls:
            raise DatasetValidationError("budget_exceeded")
        if not cases:
            raise DatasetValidationError("no_cases_generated")
        for case in cases:
            if case.get("tier") == "gold":
                raise DatasetValidationError("ai_generated cannot be gold")
            case["tier"] = "silver"
            if case.get("split") == "holdout":
                raise DatasetValidationError("holdout generation forbidden")
            case["expected_evidence"] = redact_doc_fragment(str(case.get("expected_evidence") or ""))
            if isinstance(case.get("provenance"), dict) and "fragment" in case["provenance"]:
                case["provenance"]["fragment"] = redact_doc_fragment(str(case["provenance"]["fragment"]))

        parsed = _parse_cases(cases)
        digest = canonical_cases_hash(parsed)
        version_name = f"docq-{idempotency_key[:24]}"
        clash = self.session.exec(
            select(EvaluationDatasetVersion).where(
                col(EvaluationDatasetVersion.dataset_id) == dataset.id,
                col(EvaluationDatasetVersion.version) == version_name,
            )
        ).first()
        if clash:
            raise DatasetConflictError("version exists")
        index = {
            "kind": "doc_questions",
            "schema": DOC_QUESTION_SCHEMA,
            "idempotency_key": idempotency_key,
            "document_ids": list(document_ids),
            "generation_config": generation_config,
            "external_calls_used": int(calls_used),
            "max_external_calls": int(max_external_calls),
            "generator": DOC_QUESTION_GENERATOR if generator is None else "injected",
        }
        version_row = EvaluationDatasetVersion(
            dataset_id=dataset.id,
            tenant_id=dataset.tenant_id,
            version=version_name,
            status="draft",
            source="ai_generated",
            content_hash=digest,
            schema_name=DOC_QUESTION_SCHEMA,
            cases_json=json.dumps(parsed, ensure_ascii=False, sort_keys=True),
            index_json=json.dumps(index, ensure_ascii=False, sort_keys=True),
            case_count=len(parsed),
            is_frozen=False,
            created_by=user.id,
        )
        self.session.add(version_row)
        self.session.commit()
        self.session.refresh(version_row)
        return version_row

    def get_doc_questions(
        self, user: User, dataset_id: str, version_id: str
    ) -> tuple[EvaluationDataset, EvaluationDatasetVersion, list[dict[str, Any]], dict[str, Any]]:
        dataset, version, cases = self.get_version(
            user, dataset_id, version_id, redact_holdout=False
        )
        index = self._load_index(version)
        if index.get("kind") != "doc_questions":
            raise DatasetValidationError("not_doc_question_version")
        refreshed = self._refresh_case_availability(user, cases)
        return dataset, version, refreshed, index

    def edit_doc_question(
        self,
        user: User,
        dataset_id: str,
        version_id: str,
        *,
        case_id: str,
        query: str,
        expected_answer_points: list[str] | None = None,
    ) -> EvaluationDatasetVersion:
        if not self._can_write(user):
            raise DatasetAccessError
        _, version, cases, _index = self.get_doc_questions(user, dataset_id, version_id)
        if version.status != "draft":
            raise DatasetValidationError("only draft candidates editable")
        query = query.strip()
        if len(query) < 4:
            raise DatasetValidationError("query too short")
        updated: list[dict[str, Any]] = []
        found = False
        for row in cases:
            item = dict(row)
            if item.get("case_id") != case_id:
                updated.append(item)
                continue
            found = True
            if item.get("unavailable_reason"):
                raise DatasetValidationError("source_unavailable")
            if item.get("candidate_status") == "rejected":
                raise DatasetValidationError("rejected_case_not_editable")
            item["query"] = query
            if expected_answer_points is not None:
                item["expected_answer_points"] = [
                    redact_doc_fragment(str(point)) for point in expected_answer_points
                ]
            item["candidate_status"] = "edited"
            updated.append(item)
        if not found:
            raise DatasetValidationError("case_not_found")
        version.cases_json = json.dumps(updated, ensure_ascii=False, sort_keys=True)
        version.content_hash = canonical_cases_hash(updated)
        version.case_count = len(updated)
        self.session.add(version)
        self.session.commit()
        self.session.refresh(version)
        return version

    def review_doc_question(
        self,
        user: User,
        dataset_id: str,
        version_id: str,
        *,
        case_id: str,
        decision: str,
        notes: str | None = None,
    ) -> EvaluationDatasetVersion:
        if not self._can_write(user):
            raise DatasetAccessError
        if decision not in {"accept", "reject", "reopen"}:
            raise DatasetValidationError("invalid decision")
        _, version, cases, _index = self.get_doc_questions(user, dataset_id, version_id)
        if version.status != "draft":
            raise DatasetValidationError("only draft candidates reviewable")
        updated: list[dict[str, Any]] = []
        found = False
        for row in cases:
            item = dict(row)
            if item.get("case_id") != case_id:
                updated.append(item)
                continue
            found = True
            status_now = item.get("candidate_status") or "pending"
            if decision == "accept":
                if item.get("unavailable_reason"):
                    raise DatasetValidationError("source_unavailable")
                if status_now not in {"pending", "edited", "rejected"}:
                    raise DatasetValidationError("invalid_status_transition")
                item["candidate_status"] = "accepted"
            elif decision == "reject":
                if status_now not in {"pending", "edited", "accepted"}:
                    raise DatasetValidationError("invalid_status_transition")
                item["candidate_status"] = "rejected"
            else:  # reopen = 拒绝可撤销
                if status_now != "rejected":
                    raise DatasetValidationError("only_rejected_can_reopen")
                item["candidate_status"] = "pending"
            if notes is not None:
                item["review_notes"] = notes
            item["reviewed_by"] = user.id
            updated.append(item)
        if not found:
            raise DatasetValidationError("case_not_found")
        version.cases_json = json.dumps(updated, ensure_ascii=False, sort_keys=True)
        version.content_hash = canonical_cases_hash(updated)
        self.session.add(version)
        self.session.commit()
        self.session.refresh(version)
        return version

    def promote_doc_questions_to_silver(
        self,
        user: User,
        dataset_id: str,
        version_id: str,
        *,
        silver_version: str,
    ) -> EvaluationDatasetVersion:
        """Materialize human-accepted, still-authorized candidates as Silver draft."""
        if not self._can_write(user):
            raise DatasetAccessError
        if not _VERSION_RE.fullmatch(silver_version):
            raise DatasetValidationError("invalid version")
        dataset, _version, cases, index = self.get_doc_questions(user, dataset_id, version_id)
        accepted: list[dict[str, Any]] = []
        for row in cases:
            if row.get("candidate_status") != "accepted":
                continue
            if row.get("unavailable_reason"):
                raise DatasetValidationError("accepted_source_unavailable")
            case = {
                "case_id": row["case_id"],
                "split": "validation",
                "tier": "silver",
                "query": row["query"],
                "expected_document_ids": row.get("expected_document_ids") or [],
                "expected_evidence": row.get("expected_evidence"),
                "expected_answer_points": row.get("expected_answer_points") or [],
                "provenance": row.get("provenance") or {},
                "generator": "doc-assisted-human-accepted-v1",
            }
            if case.get("tier") == "gold":
                raise DatasetValidationError("ai_generated cannot be gold")
            accepted.append(case)
        if not accepted:
            raise DatasetValidationError("no_accepted_candidates")
        parsed = _parse_cases(accepted)
        digest = canonical_cases_hash(parsed)
        clash = self.session.exec(
            select(EvaluationDatasetVersion).where(
                col(EvaluationDatasetVersion.dataset_id) == dataset.id,
                col(EvaluationDatasetVersion.version) == silver_version,
            )
        ).first()
        if clash:
            raise DatasetConflictError("version exists")
        silver_index = {
            "kind": "doc_questions_silver",
            "from_candidate_version_id": version_id,
            "source_idempotency_key": index.get("idempotency_key"),
            "generation_config": index.get("generation_config"),
            "human_accepted": True,
            "auto_gold": False,
        }
        silver = EvaluationDatasetVersion(
            dataset_id=dataset.id,
            tenant_id=dataset.tenant_id,
            version=silver_version,
            status="draft",
            source="ai_generated",
            content_hash=digest,
            schema_name="managed-eval-case-v1",
            cases_json=json.dumps(parsed, ensure_ascii=False, sort_keys=True),
            index_json=json.dumps(silver_index, ensure_ascii=False, sort_keys=True),
            case_count=len(parsed),
            is_frozen=False,
            created_by=user.id,
            review_notes="Human-accepted doc-assisted Silver candidates; not Gold.",
        )
        self.session.add(silver)
        self.session.commit()
        self.session.refresh(silver)
        return silver

