"""从反思或蒸馏结果提取可查询、可审核的技能候选与待办。

候选写入后保持待审状态，不会自动上线技能，也不会自动调度待办。
技能候选需要先转成技能草稿，再由既有的草稿确认接口生效。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, datetime

from sqlmodel import Session, col, select

from app.core.security import Role
from app.evolution.models import (
    DistillResult,
    ImprovementCategory,
    ImprovementPoint,
    ReflectResult,
)
from app.evolution.records import EvolutionCandidate, EvolutionDistillRun, EvolutionReflectRun
from app.models.conversation import Conversation
from app.models.user import User
from app.services.skill_service import SkillRequestError, SkillText, create_skill_draft_from_text

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[a-z0-9_-]{1,64}$")
_TITLE_MAX = 256
_DESC_MAX = 2000


class EvolutionCandidateError(Exception):
    """候选查询或审核失败。"""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def missing_candidate_reason(result: ReflectResult | DistillResult) -> str | None:
    """当没有可提取候选时，返回可解释的原因。"""
    if isinstance(result, ReflectResult):
        if result.error:
            return f"反思未成功，未提取候选：{result.error}"
        if result.has_action_items or _skill_improvements(result):
            return None
        return "反思未提取到技能建议或待办"
    if result.error:
        return f"蒸馏未成功，未提取候选：{result.error}"
    if result.skill_suggestions:
        return None
    return "蒸馏未提取到技能建议"


def record_candidates_from_reflect(
    session: Session,
    run: EvolutionReflectRun,
    result: ReflectResult,
) -> list[EvolutionCandidate]:
    """把一次成功反思中的技能建议和待办写成候选记录。"""
    if run.status != "succeeded":
        return []
    owner_user_id = _conversation_owner(session, run.conversation_id)
    rows: list[EvolutionCandidate] = []
    for item in result.action_items:
        if not item.description.strip():
            continue
        rows.append(
            _new_candidate(
                kind="todo",
                source_type="reflect",
                source_run_id=run.id,
                conversation_id=run.conversation_id or None,
                tenant_id=run.tenant_id,
                owner_user_id=owner_user_id,
                title=_title(item.description),
                description=item.description.strip(),
                payload=item.to_dict(),
            )
        )
    for improvement in _skill_improvements(result):
        title = improvement.affected_skill or improvement.summary or improvement.suggestion
        description = improvement.suggestion or improvement.detail or improvement.summary
        rows.append(
            _new_candidate(
                kind="skill",
                source_type="reflect",
                source_run_id=run.id,
                conversation_id=run.conversation_id or None,
                tenant_id=run.tenant_id,
                owner_user_id=owner_user_id,
                title=_title(title),
                description=description.strip(),
                payload=improvement.to_dict(),
            )
        )
    return _persist(session, rows)


def record_candidates_from_distill(
    session: Session,
    run: EvolutionDistillRun,
    result: DistillResult,
) -> list[EvolutionCandidate]:
    """把一次成功蒸馏中的技能建议写成候选记录。

    蒸馏可能跨租户汇总，这类候选不带租户，仅系统管理员可查阅。
    """
    if run.status != "succeeded":
        return []
    rows: list[EvolutionCandidate] = []
    for suggestion in result.skill_suggestions:
        title = suggestion.skill_name or suggestion.description
        description = suggestion.description or suggestion.prompt_injection
        if not title.strip() and not description.strip():
            continue
        rows.append(
            _new_candidate(
                kind="skill",
                source_type="distill",
                source_run_id=run.id,
                conversation_id=None,
                tenant_id=None,
                owner_user_id=None,
                title=_title(title or description),
                description=(description or title).strip(),
                payload=suggestion.to_dict(),
            )
        )
    return _persist(session, rows)


def list_candidates(
    session: Session,
    user: User,
    *,
    kind: str | None = None,
    status: str | None = None,
    conversation_id: str | None = None,
) -> list[EvolutionCandidate]:
    """按权限列出可查阅的候选，新记录在前。"""
    stmt = select(EvolutionCandidate).order_by(col(EvolutionCandidate.created_at).desc())
    if kind is not None:
        stmt = stmt.where(EvolutionCandidate.kind == kind)
    if status is not None:
        stmt = stmt.where(EvolutionCandidate.status == status)
    if conversation_id is not None:
        stmt = stmt.where(EvolutionCandidate.conversation_id == conversation_id)
    return [row for row in session.exec(stmt).all() if _can_read(user, row)]


def get_candidate(session: Session, user: User, candidate_id: str) -> EvolutionCandidate:
    """读取一条可见候选，不存在或不可见时返回同一错误。"""
    row = session.get(EvolutionCandidate, candidate_id)
    if row is None or not _can_read(user, row):
        raise EvolutionCandidateError(404, "候选不存在")
    return row


def reject_candidate(
    session: Session,
    user: User,
    candidate_id: str,
    *,
    note: str | None = None,
) -> EvolutionCandidate:
    """拒绝一条待审候选，不改变技能或待办生效状态。"""
    row = _writable_pending(session, user, candidate_id)
    _mark_reviewed(row, user, "rejected", note)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def accept_todo_candidate(
    session: Session,
    user: User,
    candidate_id: str,
    *,
    note: str | None = None,
) -> EvolutionCandidate:
    """把待办标记为已接受。只留下审核记录，不创建工作流或改系统行为。"""
    row = _writable_pending(session, user, candidate_id)
    if row.kind != "todo":
        raise EvolutionCandidateError(409, "只有待办候选可以接受")
    _mark_reviewed(row, user, "accepted", note)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def promote_skill_candidate(
    session: Session,
    user: User,
    candidate_id: str,
    *,
    scope: str = "private",
    text: SkillText | None = None,
    preconditions: str = "",
    workflow: str = "",
    tools: list[str] | None = None,
    escalation: str = "",
    tests: str = "",
    note: str | None = None,
) -> EvolutionCandidate:
    """把技能候选转成未启用的技能草稿。草稿仍须经过既有确认接口才会生效。"""
    row = _writable_pending(session, user, candidate_id)
    if row.kind != "skill":
        raise EvolutionCandidateError(409, "只有技能候选可以转成草稿")
    if scope not in {"private", "global"}:
        raise EvolutionCandidateError(422, "技能范围无效")
    draft_text = text or _draft_text_from_candidate(row)
    source_text = (
        f"evolution-candidate:{row.id}\n"
        f"source={row.source_type}:{row.source_run_id}\n"
        f"{row.description}"
    )[:2000]
    try:
        change = create_skill_draft_from_text(
            session,
            user,
            draft_text,
            scope,  # type: ignore[arg-type]
            preconditions=preconditions,
            workflow=workflow or draft_text.system_prompt,
            tools=tools or [],
            escalation=escalation or "超出该技能范围时按普通对话回答，不声称已调用工具。",
            tests=tests or "确认前不参与对话；确认后按技能清单回归。",
            source_text=source_text,
            commit=False,
        )
    except SkillRequestError as exc:
        raise EvolutionCandidateError(exc.status_code, exc.detail) from exc
    _mark_reviewed(row, user, "promoted", note)
    row.skill_draft_id = change.view.id
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _skill_improvements(result: ReflectResult) -> list[ImprovementPoint]:
    rows: list[ImprovementPoint] = []
    for item in result.improvements:
        affected = (item.affected_skill or "").strip()
        blob = f"{item.summary}\n{item.suggestion}\n{item.detail}"
        if item.category == ImprovementCategory.SKILL or affected or "技能" in blob:
            rows.append(item)
    return rows


def _conversation_owner(session: Session, conversation_id: str | None) -> str | None:
    if not conversation_id:
        return None
    conv = session.get(Conversation, conversation_id)
    if conv is None:
        return None
    return conv.user_id


def _title(raw: str) -> str:
    text = re.sub(r"\s+", " ", (raw or "").strip())
    return text[:_TITLE_MAX]


def _payload_fingerprint(kind: str, payload: dict) -> str:
    blob = json.dumps({"kind": kind, "payload": payload}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _new_candidate(
    *,
    kind: str,
    source_type: str,
    source_run_id: str,
    conversation_id: str | None,
    tenant_id: str | None,
    owner_user_id: str | None,
    title: str,
    description: str,
    payload: dict,
) -> EvolutionCandidate:
    return EvolutionCandidate(
        kind=kind,
        status="pending",
        source_type=source_type,
        source_run_id=source_run_id,
        conversation_id=conversation_id,
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        title=title,
        description=description[:_DESC_MAX],
        payload_json=json.dumps(payload, ensure_ascii=False),
        fingerprint=_payload_fingerprint(kind, payload),
    )


def _persist(session: Session, rows: list[EvolutionCandidate]) -> list[EvolutionCandidate]:
    saved: list[EvolutionCandidate] = []
    for row in rows:
        existing = session.exec(
            select(EvolutionCandidate).where(
                EvolutionCandidate.source_type == row.source_type,
                EvolutionCandidate.source_run_id == row.source_run_id,
                EvolutionCandidate.fingerprint == row.fingerprint,
            )
        ).first()
        if existing is not None:
            saved.append(existing)
            continue
        session.add(row)
        saved.append(row)
    session.flush()
    return saved


def _is_privileged(user: User) -> bool:
    return user.role in {Role.SYSTEM_ADMIN.value, Role.SYSTEM_VIEWER.value}


def _can_read(user: User, row: EvolutionCandidate) -> bool:
    if _is_privileged(user):
        return True
    if row.tenant_id is None:
        return False
    if row.tenant_id != user.tenant_id:
        return False
    if user.role == Role.TENANT_ADMIN.value:
        return True
    if row.owner_user_id:
        return row.owner_user_id == user.id
    return False


def _can_write(user: User, row: EvolutionCandidate) -> bool:
    if user.role == Role.SYSTEM_ADMIN.value:
        return True
    if row.tenant_id is None:
        return False
    if row.tenant_id != user.tenant_id:
        return False
    if user.role == Role.TENANT_ADMIN.value:
        return True
    if row.owner_user_id:
        return row.owner_user_id == user.id
    return False


def _writable_pending(session: Session, user: User, candidate_id: str) -> EvolutionCandidate:
    row = session.get(EvolutionCandidate, candidate_id)
    if row is None or not _can_read(user, row):
        raise EvolutionCandidateError(404, "候选不存在")
    if not _can_write(user, row):
        raise EvolutionCandidateError(403, "无权审核这条候选")
    if row.status != "pending":
        raise EvolutionCandidateError(409, "只有待审候选可以审核")
    return row


def _mark_reviewed(row: EvolutionCandidate, user: User, status: str, note: str | None) -> None:
    row.status = status
    row.reviewed_by = user.id
    row.reviewed_at = datetime.now(UTC)
    if note:
        row.review_note = note


def suggest_skill_name(raw: str) -> str:
    """把候选标题收成合法技能名；无法收时使用占位名。"""
    slug = re.sub(r"[^a-z0-9_-]+", "_", (raw or "").strip().lower()).strip("_-")
    slug = slug[:64].strip("_-")
    return slug if _NAME_RE.match(slug or "") else "skill_candidate"


def _clip(text: str, limit: int, fallback: str) -> str:
    value = re.sub(r"\s+", " ", (text or "").strip()) or fallback
    return value[:limit]


def _draft_text_from_candidate(row: EvolutionCandidate) -> SkillText:
    payload = json.loads(row.payload_json or "{}")
    name = suggest_skill_name(str(payload.get("skill_name") or payload.get("affected_skill") or row.title))
    description = _clip(
        str(payload.get("description") or payload.get("suggestion") or payload.get("summary") or row.description),
        200,
        "待人工补充技能目的。",
    )
    suggestion = str(
        payload.get("suggestion")
        or payload.get("prompt_injection")
        or payload.get("detail")
        or row.description
    )
    keywords = payload.get("triggers") if isinstance(payload.get("triggers"), list) else []
    cleaned = [str(word).strip()[:32] for word in keywords if str(word).strip()]
    if not cleaned:
        cleaned = [name[:32]]
    constraints = "需人工审阅后确认；未确认前不得用于对话。"
    system_prompt = _clip(suggestion, 2000, "待人工补充技能说明。")
    example = _clip(str(payload.get("example") or row.description or description), 200, "待补充示例。")
    return SkillText(
        name=name,
        description=description,
        keywords=cleaned[:8],
        constraints=constraints,
        system_prompt=system_prompt,
        example=example,
    )
