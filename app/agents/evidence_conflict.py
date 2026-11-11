"""功能概述
对候选回答做记忆与检索证据的条件验证，并在次数和预算内做有限纠错。

功能涵盖
- 只使用当前租户、已授权、未撤销、未过期且未被取代的证据。
- 新旧记忆冲突取未取代的较新版本；记忆与检索冲突按事实域选择胜出源。
- 已授权来源无法唯一确定时拒答，不猜测。
- 纠错次数和调用次数有界；预算耗尽或已取消时停止，不再继续纠错。
- 不把不合格证据的内容写入输出，也不把它交给后续纠错步骤。

已完成
- 纯函数实现，不访问网络，不读取生产数据。
- 默认不接入回答链，调用方必须显式使用本模块。

待完善
- 不在这里改引用核验或输出围栏。
- 不把本实验策略设为生产默认。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

SourceType = Literal["memory", "retrieval"]
FactDomain = Literal["personal", "published"]
ConflictType = Literal[
    "none",
    "old_vs_new_memory",
    "memory_vs_retrieval",
    "irreconcilable",
    "insufficient",
    "ineligible_source",
]
CorrectionStatus = Literal[
    "unchanged",
    "corrected",
    "refused",
    "budget_exhausted",
    "cancelled",
]

REFUSAL_TEXT = "现有已授权证据存在冲突或不足，无法确定答案。"
MAX_CORRECTIONS = 1
MAX_CALLS_PER_CASE = 2


@dataclass(frozen=True)
class EvidenceItem:
    """一条记忆或检索证据。"""

    id: str
    source_type: SourceType
    tenant_id: str
    user_id: str
    subject_key: str
    value: str
    content: str
    authorized: bool = True
    revoked: bool = False
    superseded: bool = False
    expired: bool = False
    version: int = 1
    recorded_at: str = ""
    grade: str = "user_statement"


@dataclass(frozen=True)
class CandidateAnswer:
    """待验证的候选回答。"""

    text: str
    claimed_subject: str
    claimed_value: str
    used_evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class CorrectionContext:
    """一次离线验证的租户、预算和取消状态。"""

    tenant_id: str
    user_id: str
    fact_domain: FactDomain
    cancelled: bool = False
    budget_remaining: int = MAX_CALLS_PER_CASE
    max_corrections: int = MAX_CORRECTIONS


@dataclass(frozen=True)
class VerificationVerdict:
    """对当前回答的验证结论。"""

    ok: bool
    conflict_type: ConflictType
    winning_value: str | None
    winning_evidence_ids: tuple[str, ...]
    dropped_evidence_ids: tuple[str, ...]
    should_refuse: bool
    used_ineligible: bool
    leaked: bool


@dataclass(frozen=True)
class CorrectionResult:
    """有限纠错后的输出。"""

    status: CorrectionStatus
    answer: CandidateAnswer
    conflict_type: ConflictType
    attempts: int
    calls: int
    winning_evidence_ids: tuple[str, ...] = ()
    dropped_evidence_ids: tuple[str, ...] = ()
    leaked: bool = False
    used_ineligible: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)


def normalize_value(value: str) -> str:
    """把事实值收成可比较的字符串。"""
    return " ".join(value.strip().split()).casefold()


def partition_evidence(
    items: list[EvidenceItem],
    context: CorrectionContext,
) -> tuple[list[EvidenceItem], list[EvidenceItem]]:
    """按租户、主体和授权条件拆出可用证据与淘汰证据。"""
    eligible: list[EvidenceItem] = []
    dropped: list[EvidenceItem] = []
    for item in items:
        same_tenant = item.tenant_id == context.tenant_id
        memory_owner_ok = item.source_type != "memory" or item.user_id == context.user_id
        if (
            same_tenant
            and memory_owner_ok
            and item.authorized
            and not item.revoked
            and not item.superseded
            and not item.expired
        ):
            eligible.append(item)
        else:
            dropped.append(item)
    return eligible, dropped


def is_sensitive_drop(item: EvidenceItem, context: CorrectionContext) -> bool:
    """跨租户、未授权、已撤销或非本人记忆才视为敏感泄漏源。"""
    return (
        item.tenant_id != context.tenant_id
        or (item.source_type == "memory" and item.user_id != context.user_id)
        or not item.authorized
        or item.revoked
    )


def ineligible_markers(dropped: list[EvidenceItem], eligible: list[EvidenceItem]) -> tuple[str, ...]:
    """淘汰证据中不得出现在输出里的内容。"""
    allowed = {normalize_value(item.value) for item in eligible}
    allowed.update(item.content.strip() for item in eligible if item.content.strip())
    markers: list[str] = []
    seen: set[str] = set()
    for item in dropped:
        for raw in (item.content, item.value):
            text = raw.strip()
            if not text or text in seen or normalize_value(text) in allowed:
                continue
            seen.add(text)
            markers.append(text)
    return tuple(markers)


def output_leaks(text: str, markers: tuple[str, ...]) -> bool:
    """检查输出是否泄漏了淘汰证据中的内容。"""
    if not text:
        return False
    for marker in markers:
        if marker and marker in text:
            return True
    return False


def _ranked(items: list[EvidenceItem]) -> list[EvidenceItem]:
    return sorted(items, key=lambda item: (item.version, item.recorded_at, item.id), reverse=True)


def _unique_top_value(items: list[EvidenceItem]) -> tuple[EvidenceItem, ...] | None:
    if not items:
        return None
    ranked = _ranked(items)
    top = ranked[0]
    tied = [item for item in ranked if item.version == top.version and item.recorded_at == top.recorded_at]
    values = {normalize_value(item.value) for item in tied}
    if len(values) > 1:
        return None
    winners = [item for item in tied if normalize_value(item.value) == normalize_value(top.value)]
    return tuple(winners)


def resolve_authorized_fact(
    eligible: list[EvidenceItem],
    *,
    subject: str,
    fact_domain: FactDomain,
) -> tuple[str | None, tuple[str, ...], ConflictType]:
    """在可用证据中为同一主题选出唯一当前事实。"""
    memories = [item for item in eligible if item.source_type == "memory" and item.subject_key == subject]
    documents = [item for item in eligible if item.source_type == "retrieval" and item.subject_key == subject]
    memory_top = _unique_top_value(memories)
    document_top = _unique_top_value(documents)

    if fact_domain == "personal":
        if memories and memory_top is None:
            return None, (), "irreconcilable"
        if memory_top is not None:
            return memory_top[0].value, tuple(item.id for item in memory_top), "none"
        if documents and document_top is None:
            return None, (), "irreconcilable"
        if document_top is not None:
            return document_top[0].value, tuple(item.id for item in document_top), "none"
        return None, (), "insufficient"

    if documents and document_top is None:
        return None, (), "irreconcilable"
    if document_top is not None:
        return document_top[0].value, tuple(item.id for item in document_top), "none"
    if memories:
        return None, (), "insufficient"
    return None, (), "insufficient"


def classify_conflict(
    answer: CandidateAnswer,
    evidence: list[EvidenceItem],
    dropped: list[EvidenceItem],
    *,
    winning_value: str | None,
    conflict_from_resolve: ConflictType,
) -> ConflictType:
    """区分新旧记忆、记忆与检索，以及无法调和的冲突。"""
    if conflict_from_resolve in {"irreconcilable", "insufficient"}:
        return conflict_from_resolve

    used = {item.id: item for item in evidence + dropped if item.id in set(answer.used_evidence_ids)}
    used_ineligible = any(item.id in {drop.id for drop in dropped} for item in used.values())
    if used_ineligible or any(item.superseded for item in used.values()):
        superseded_used = [
            item
            for item in used.values()
            if item.superseded and item.source_type == "memory" and item.subject_key == answer.claimed_subject
        ]
        if superseded_used and winning_value is not None:
            return "old_vs_new_memory"
        return "ineligible_source"

    if winning_value is None:
        return conflict_from_resolve

    if normalize_value(answer.claimed_value) == normalize_value(winning_value):
        return "none"

    used_memory = any(item.source_type == "memory" and not item.superseded for item in used.values())
    used_retrieval = any(item.source_type == "retrieval" for item in used.values())
    has_memory = any(
        item.source_type == "memory" and item.subject_key == answer.claimed_subject for item in evidence
    )
    has_retrieval = any(
        item.source_type == "retrieval" and item.subject_key == answer.claimed_subject for item in evidence
    )
    if (used_memory and has_retrieval) or (used_retrieval and has_memory) or (has_memory and has_retrieval):
        return "memory_vs_retrieval"

    dropped_old = any(
        item.source_type == "memory" and item.superseded and item.subject_key == answer.claimed_subject
        for item in dropped
    )
    if dropped_old:
        return "old_vs_new_memory"
    return "ineligible_source" if used_ineligible else "memory_vs_retrieval"


def verify_answer(
    answer: CandidateAnswer,
    items: list[EvidenceItem],
    context: CorrectionContext,
) -> VerificationVerdict:
    """验证候选回答是否与当前已授权证据一致，且没有泄漏淘汰内容。"""
    eligible, dropped = partition_evidence(items, context)
    leak_markers = ineligible_markers(
        [item for item in dropped if is_sensitive_drop(item, context)],
        eligible,
    )
    leaked = output_leaks(answer.text, leak_markers)
    used_ineligible = any(item_id in {item.id for item in dropped} for item_id in answer.used_evidence_ids)
    winning_value, winning_ids, resolved = resolve_authorized_fact(
        eligible,
        subject=answer.claimed_subject,
        fact_domain=context.fact_domain,
    )
    conflict_type = classify_conflict(
        answer,
        eligible,
        dropped,
        winning_value=winning_value,
        conflict_from_resolve=resolved,
    )
    should_refuse = winning_value is None or resolved in {"irreconcilable", "insufficient"}
    matches = (
        winning_value is not None
        and normalize_value(answer.claimed_value) == normalize_value(winning_value)
        and not leaked
        and not used_ineligible
        and not should_refuse
    )
    return VerificationVerdict(
        ok=matches and conflict_type == "none",
        conflict_type=conflict_type if not matches else "none",
        winning_value=winning_value,
        winning_evidence_ids=winning_ids,
        dropped_evidence_ids=tuple(item.id for item in dropped),
        should_refuse=should_refuse,
        used_ineligible=used_ineligible,
        leaked=leaked,
    )


def _corrected_text(answer: CandidateAnswer, winning_value: str, markers: tuple[str, ...]) -> str:
    text = answer.text
    if answer.claimed_value and answer.claimed_value in text:
        text = text.replace(answer.claimed_value, winning_value)
    else:
        text = f"根据已授权的当前证据，{answer.claimed_subject} 为 {winning_value}。"
    for marker in markers:
        if marker and marker in text:
            text = text.replace(marker, "")
    text = " ".join(text.split())
    if output_leaks(text, markers) or not text:
        return f"根据已授权的当前证据，{answer.claimed_subject} 为 {winning_value}。"
    return text


def apply_correction(
    answer: CandidateAnswer,
    verdict: VerificationVerdict,
    markers: tuple[str, ...],
) -> CandidateAnswer:
    """按验证结论生成有限纠错后的回答，必要时改为拒答。"""
    if verdict.should_refuse or verdict.winning_value is None:
        return CandidateAnswer(
            text=REFUSAL_TEXT,
            claimed_subject=answer.claimed_subject,
            claimed_value="",
            used_evidence_ids=(),
        )
    return CandidateAnswer(
        text=_corrected_text(answer, verdict.winning_value, markers),
        claimed_subject=answer.claimed_subject,
        claimed_value=verdict.winning_value,
        used_evidence_ids=verdict.winning_evidence_ids,
    )


def run_bounded_correction(
    answer: CandidateAnswer,
    items: list[EvidenceItem],
    context: CorrectionContext,
) -> CorrectionResult:
    """验证并在次数、预算和取消约束内做至多一次纠错。"""
    eligible, dropped = partition_evidence(items, context)
    markers = ineligible_markers(dropped, eligible)
    leak_markers = ineligible_markers(
        [item for item in dropped if is_sensitive_drop(item, context)],
        eligible,
    )
    dropped_ids = tuple(item.id for item in dropped)
    used_ineligible = any(item_id in set(dropped_ids) for item_id in answer.used_evidence_ids)

    if context.cancelled:
        return CorrectionResult(
            status="cancelled",
            answer=answer,
            conflict_type="none",
            attempts=0,
            calls=0,
            dropped_evidence_ids=dropped_ids,
            leaked=output_leaks(answer.text, leak_markers),
            used_ineligible=used_ineligible,
            notes=("cancelled_before_verification",),
        )

    if context.budget_remaining <= 0:
        return CorrectionResult(
            status="budget_exhausted",
            answer=answer,
            conflict_type=verify_answer(answer, items, context).conflict_type,
            attempts=0,
            calls=0,
            dropped_evidence_ids=dropped_ids,
            leaked=output_leaks(answer.text, leak_markers),
            used_ineligible=used_ineligible,
            notes=("budget_exhausted_before_verification",),
        )

    calls = 1
    budget_left = context.budget_remaining - 1
    verdict = verify_answer(answer, items, context)
    current = answer

    if verdict.ok:
        return CorrectionResult(
            status="unchanged",
            answer=current,
            conflict_type="none",
            attempts=0,
            calls=calls,
            winning_evidence_ids=verdict.winning_evidence_ids,
            dropped_evidence_ids=verdict.dropped_evidence_ids,
            leaked=False,
            used_ineligible=False,
        )

    if context.max_corrections <= 0 or budget_left <= 0:
        return CorrectionResult(
            status="budget_exhausted",
            answer=current,
            conflict_type=verdict.conflict_type,
            attempts=0,
            calls=calls,
            winning_evidence_ids=verdict.winning_evidence_ids,
            dropped_evidence_ids=verdict.dropped_evidence_ids,
            leaked=verdict.leaked,
            used_ineligible=verdict.used_ineligible,
            notes=("no_budget_for_correction",),
        )

    attempts = 1
    calls += 1
    original_conflict = verdict.conflict_type
    current = apply_correction(current, verdict, markers)
    verdict = verify_answer(current, items, context)
    if verdict.leaked or output_leaks(current.text, leak_markers):
        current = CandidateAnswer(
            text=REFUSAL_TEXT,
            claimed_subject=answer.claimed_subject,
            claimed_value="",
            used_evidence_ids=(),
        )
        verdict = verify_answer(current, items, context)

    if current.text == REFUSAL_TEXT or verdict.should_refuse:
        status: CorrectionStatus = "refused"
        conflict_type = original_conflict if original_conflict != "none" else verdict.conflict_type
    elif verdict.ok:
        status = "corrected"
        conflict_type = original_conflict
    else:
        status = "corrected"
        conflict_type = verdict.conflict_type

    return CorrectionResult(
        status=status,
        answer=current,
        conflict_type=conflict_type,
        attempts=attempts,
        calls=calls,
        winning_evidence_ids=verdict.winning_evidence_ids,
        dropped_evidence_ids=verdict.dropped_evidence_ids,
        leaked=output_leaks(current.text, leak_markers),
        used_ineligible=any(
            item_id in set(verdict.dropped_evidence_ids) for item_id in current.used_evidence_ids
        ),
        notes=(),
    )
