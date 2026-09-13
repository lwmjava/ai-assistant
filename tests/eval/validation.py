"""确定性校验 RAG Evaluation Case 与合成语料。"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "evals/schemas/rag_case.schema.json"
CORPUS_MANIFEST = REPO_ROOT / "evals/fixtures/corpus/manifest.json"
CASES_PATH = REPO_ROOT / "evals/datasets/rag-v0.1/cases.json"
INDEX_PATH = REPO_ROOT / "evals/datasets/rag-v0.1/index.json"

SPLIT_VALUES = frozenset({"development", "validation", "holdout"})
REQUIRED_CATEGORIES = frozenset(
    {
        "fact",
        "multi_hop",
        "no_answer",
        "conflict",
        "stale_version",
        "effective_date",
        "cross_tenant",
        "private_resource",
        "prompt_injection",
        "ambiguous",
        "citation_mismatch",
    }
)
GOLD_DECISIONS = frozenset({"ACCEPT_GOLD", "REVISE_AND_ACCEPT"})
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    re.compile(r"(?i)\b(sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})\b"),
    re.compile(r"(?i)\b(password|passwd|api[_-]?key|secret_key)\s*[:=]\s*\S+"),
)
PUNCT_RE = re.compile(r"[\s，。？！、,.!?;:：；“”\"'（）()【】\[\]-]+")


class Identity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str
    user_id: str
    roles: list[str] = Field(min_length=1)
    resource_scopes: list[str]


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str
    chunk_id: str | None
    title: str
    source: str
    version: str
    effective_at: str | None
    page: int | None
    section: str | None
    exact_quote: str = Field(min_length=8)


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_snapshot: str
    generator: str
    prompt_version: str
    generated_at: str
    review_status: str


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewer: str | None
    reviewed_at: str | None
    decision: str | None
    notes: str | None


class RagEvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(pattern=r"^rag-[0-9]{3}$")
    dataset_version: str
    split: str
    tier: str
    category: str
    query: str = Field(min_length=4, max_length=500)
    identity: Identity
    expected_document_ids: list[str]
    forbidden_document_ids: list[str]
    expected_evidence: list[Evidence]
    expected_answer_points: list[str]
    forbidden_answer_points: list[str]
    should_answer: bool
    should_clarify: bool
    should_escalate: bool
    synthetic: bool
    notes: str | None = None
    attack_goal: str | None = None
    provenance: Provenance
    review: Review
    tags: list[str] = Field(min_length=1)


class Finding(BaseModel):
    code: str
    case_id: str | None
    message: str


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_cases() -> list[dict[str, Any]]:
    payload = load_json(CASES_PATH)
    if not isinstance(payload, list):
        raise ValueError("cases.json 必须是数组")
    return payload


def load_manifest() -> dict[str, dict[str, Any]]:
    payload = load_json(CORPUS_MANIFEST)
    return {item["document_id"]: item for item in payload["documents"]}


def normalize_query(query: str) -> str:
    return PUNCT_RE.sub("", query).lower()


def token_set(text: str) -> set[str]:
    return {part for part in PUNCT_RE.split(text.lower()) if part}


def quote_in_source(source_rel: str, quote: str) -> bool:
    source_path = REPO_ROOT / source_rel
    if not source_path.is_file():
        return False
    return quote in source_path.read_text(encoding="utf-8")


def scan_secrets(text: str) -> list[str]:
    hits: list[str] = []
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            hits.append(pattern.pattern)
    return hits


def validate_dataset() -> list[Finding]:
    findings: list[Finding] = []
    schema = load_json(SCHEMA_PATH)
    if schema.get("title") != "ai-assistant RAG Evaluation Case":
        findings.append(Finding(code="SCHEMA_MISSING", case_id=None, message="缺少 Case Schema 标题"))

    try:
        raw_cases = load_cases()
        manifest = load_manifest()
        index = load_json(INDEX_PATH)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        return [Finding(code="LOAD_ERROR", case_id=None, message=str(exc))]

    parsed: list[RagEvalCase] = []
    seen_ids: set[str] = set()
    for raw in raw_cases:
        case_id = raw.get("case_id") if isinstance(raw, dict) else None
        try:
            case = RagEvalCase.model_validate(raw)
        except ValidationError as exc:
            findings.append(Finding(code="SCHEMA_INVALID", case_id=case_id, message=str(exc)))
            continue
        if case.case_id in seen_ids:
            findings.append(Finding(code="DUPLICATE_CASE_ID", case_id=case.case_id, message="case_id 重复"))
        seen_ids.add(case.case_id)
        parsed.append(case)

    if not 30 <= len(parsed) <= 50:
        findings.append(
            Finding(
                code="CASE_COUNT",
                case_id=None,
                message=f"案例数应为 30-50，实际 {len(parsed)}",
            )
        )

    categories = {case.category for case in parsed}
    missing = sorted(REQUIRED_CATEGORIES - categories)
    if missing:
        findings.append(Finding(code="CATEGORY_GAP", case_id=None, message=f"缺少类别: {missing}"))

    splits = {case.split for case in parsed}
    if splits != SPLIT_VALUES:
        findings.append(Finding(code="SPLIT_MISSING", case_id=None, message=f"split 集合不完整: {sorted(splits)}"))

    gold_cases = [case for case in parsed if case.provenance.review_status == "gold"]
    gold_ids = {case.case_id for case in gold_cases}
    if index.get("gold_status") != "pending_human_review" and not gold_cases:
        findings.append(
            Finding(
                code="GOLD_STATUS",
                case_id=None,
                message="无 Gold 案例时 index.gold_status 必须是 pending_human_review",
            )
        )
    if index.get("gold_count") != len(gold_cases):
        findings.append(
            Finding(
                code="GOLD_COUNT",
                case_id=None,
                message=f"index.gold_count={index.get('gold_count')} 与实际 {len(gold_cases)} 不一致",
            )
        )
    listed = set(index.get("gold_case_ids") or [])
    if listed and listed != gold_ids:
        findings.append(
            Finding(
                code="GOLD_ID_MISMATCH",
                case_id=None,
                message=f"index.gold_case_ids 与案例不一致: {sorted(listed ^ gold_ids)}",
            )
        )

    queries_by_norm: dict[str, list[RagEvalCase]] = defaultdict(list)
    for case in parsed:
        queries_by_norm[normalize_query(case.query)].append(case)
        if case.split not in SPLIT_VALUES:
            findings.append(Finding(code="SPLIT_INVALID", case_id=case.case_id, message=case.split))
        if case.synthetic is not True:
            findings.append(Finding(code="NOT_SYNTHETIC", case_id=case.case_id, message="必须 synthetic=true"))
        if set(case.expected_document_ids) & set(case.forbidden_document_ids):
            findings.append(Finding(code="DOC_ID_CONFLICT", case_id=case.case_id, message="期望与禁止文档相交"))

        if case.should_answer:
            if not case.expected_evidence:
                findings.append(
                    Finding(code="ANSWERABLE_NO_EVIDENCE", case_id=case.case_id, message="可回答案例缺少证据")
                )
            if not case.expected_document_ids:
                findings.append(
                    Finding(code="ANSWERABLE_NO_DOC", case_id=case.case_id, message="可回答案例缺少文档 ID")
                )

        if case.provenance.review_status == "gold":
            if not case.review.reviewer or not case.review.reviewed_at:
                findings.append(
                    Finding(code="GOLD_NO_REVIEWER", case_id=case.case_id, message="Gold 缺少人工审核人/时间")
                )
            if case.review.decision not in GOLD_DECISIONS:
                findings.append(
                    Finding(code="GOLD_NO_DECISION", case_id=case.case_id, message="Gold 缺少有效审核决定")
                )

        blob = json.dumps(case.model_dump(), ensure_ascii=False)
        for hit in scan_secrets(blob):
            findings.append(Finding(code="SECRET_PATTERN", case_id=case.case_id, message=hit))

        for evidence in case.expected_evidence:
            meta = manifest.get(evidence.document_id)
            if meta is None:
                findings.append(
                    Finding(code="UNKNOWN_DOCUMENT", case_id=case.case_id, message=evidence.document_id)
                )
                continue
            if evidence.source != meta["source"]:
                findings.append(Finding(code="SOURCE_MISMATCH", case_id=case.case_id, message=evidence.source))
            if evidence.version != meta["version"]:
                findings.append(
                    Finding(
                        code="VERSION_MISMATCH",
                        case_id=case.case_id,
                        message=f"{evidence.version} != {meta['version']}",
                    )
                )
            if evidence.effective_at != meta["effective_at"]:
                findings.append(
                    Finding(
                        code="EFFECTIVE_MISMATCH",
                        case_id=case.case_id,
                        message=str(evidence.effective_at),
                    )
                )
            if evidence.document_id not in case.expected_document_ids:
                findings.append(
                    Finding(
                        code="EVIDENCE_NOT_EXPECTED_DOC",
                        case_id=case.case_id,
                        message=evidence.document_id,
                    )
                )
            if not (REPO_ROOT / evidence.source).is_file():
                findings.append(Finding(code="SOURCE_MISSING", case_id=case.case_id, message=evidence.source))
                continue
            if not quote_in_source(evidence.source, evidence.exact_quote):
                findings.append(
                    Finding(
                        code="QUOTE_NOT_FOUND",
                        case_id=case.case_id,
                        message=evidence.exact_quote,
                    )
                )
            text = (REPO_ROOT / evidence.source).read_text(encoding="utf-8")
            for secret_hit in scan_secrets(text):
                findings.append(Finding(code="CORPUS_SECRET", case_id=case.case_id, message=secret_hit))

        for doc_id in case.expected_document_ids + case.forbidden_document_ids:
            if doc_id not in manifest:
                findings.append(Finding(code="UNKNOWN_DOCUMENT", case_id=case.case_id, message=doc_id))

        if case.category == "cross_tenant":
            for doc_id in case.forbidden_document_ids:
                meta = manifest.get(doc_id)
                if meta and meta["tenant_id"] == case.identity.tenant_id:
                    findings.append(
                        Finding(
                            code="CROSS_TENANT_SAME_TENANT",
                            case_id=case.case_id,
                            message=doc_id,
                        )
                    )

    for norm, group in queries_by_norm.items():
        split_names = {item.split for item in group}
        if len(split_names) > 1:
            findings.append(
                Finding(
                    code="SPLIT_LEAK",
                    case_id=group[0].case_id,
                    message=f"相同问题出现在多个 split: {norm}",
                )
            )

    for i, left in enumerate(parsed):
        left_tokens = token_set(left.query)
        if len(left_tokens) < 3:
            continue
        for right in parsed[i + 1 :]:
            if left.category != right.category or left.identity.tenant_id != right.identity.tenant_id:
                continue
            if left.identity.user_id != right.identity.user_id:
                continue
            right_tokens = token_set(right.query)
            if not right_tokens:
                continue
            overlap = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
            if overlap >= 0.85:
                findings.append(
                    Finding(
                        code="NEAR_DUPLICATE",
                        case_id=left.case_id,
                        message=f"{left.case_id} ~ {right.case_id} overlap={overlap:.2f}",
                    )
                )

    return findings
