"""根据反思记录评估技能效果，并在证据充足时标记下线。

评估只使用已有运行记录和对话中的技能选用信息，不发起新的模型调用。
下线只把持久化技能标为停用，不删除行，也不改内置 YAML。生产总开关默认关闭时不会自动下线。
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.database import engine as default_engine
from app.evolution.records import (
    EvolutionDistillRun,
    EvolutionReflectRun,
    EvolutionSkillEvaluation,
    EvolutionSkillRetirement,
)
from app.models.conversation import Conversation, Message
from app.models.skill import Skill

logger = logging.getLogger(__name__)

MIN_REFLECT_SAMPLES = 3
LOW_QUALITY_SCORE = 0.5
HIGH_SEVERITY_RATE = 0.6
CRITICAL_RATE = 0.5


class SkillRetirementError(Exception):
    """下线回滚时的可见错误。"""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


@dataclass
class _SkillStats:
    skill_name: str
    tenant_id: str | None = None
    activation_count: int = 0
    conversation_ids: set[str] = field(default_factory=set)
    reflect_ids: set[str] = field(default_factory=set)
    quality_scores: list[float] = field(default_factory=list)
    issue_count: int = 0
    critical_count: int = 0
    high_count: int = 0
    skill_issue_count: int = 0
    reflects_with_high_or_critical: set[str] = field(default_factory=set)
    reflects_with_critical: set[str] = field(default_factory=set)
    reflects_with_skill_issue: set[str] = field(default_factory=set)
    delete_suggestion_count: int = 0


def _session(bind: Engine | None = None) -> Session:
    return Session(bind or default_engine)


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _parse_names(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    names: list[str] = []
    for item in data:
        name = str(item).strip()
        if name and name not in names:
            names.append(name)
    return names


def list_skill_evaluations(
    session: Session,
    *,
    skill_name: str | None = None,
) -> list[EvolutionSkillEvaluation]:
    """按时间倒序列出技能效果评估记录。"""
    stmt = select(EvolutionSkillEvaluation).order_by(col(EvolutionSkillEvaluation.period_end).desc())
    if skill_name is not None:
        stmt = stmt.where(EvolutionSkillEvaluation.skill_name == skill_name)
    return list(session.exec(stmt).all())


def list_skill_retirements(
    session: Session,
    *,
    skill_id: str | None = None,
    status: str | None = None,
) -> list[EvolutionSkillRetirement]:
    """按时间倒序列出技能下线记录。"""
    stmt = select(EvolutionSkillRetirement).order_by(col(EvolutionSkillRetirement.retired_at).desc())
    if skill_id is not None:
        stmt = stmt.where(EvolutionSkillRetirement.skill_id == skill_id)
    if status is not None:
        stmt = stmt.where(EvolutionSkillRetirement.status == status)
    return list(session.exec(stmt).all())


def evaluate_skills(
    *,
    session: Session | None = None,
    bind: Engine | None = None,
    now: datetime | None = None,
    window_hours: int | None = None,
    apply_retirements: bool | None = None,
    source_distill_run_id: str | None = None,
) -> list[EvolutionSkillEvaluation]:
    """汇总窗口内技能效果，必要时把低效技能标记下线。

    ``apply_retirements`` 为 None 时，仅在进化总开关打开时执行下线。
    传入会话时只 flush，由调用方提交。
    """
    from app.evolution.store import ensure_evolution_tables

    ensure_evolution_tables(bind)
    owns_session = session is None
    sess = session or _session(bind)
    now = _aware(now or datetime.now(UTC))
    hours = int(window_hours if window_hours is not None else settings.EVOLUTION_DISTILL_HOURS)
    hours = max(1, hours)
    apply = settings.EVOLUTION_ENABLED if apply_retirements is None else bool(apply_retirements)
    period_start = now - timedelta(hours=hours)
    try:
        stats = _collect_stats(sess, period_start, now)
        rows = [
            _evaluate_one(
                sess,
                item,
                window_hours=hours,
                period_start=period_start,
                period_end=now,
                apply_retirements=apply,
                source_distill_run_id=source_distill_run_id,
            )
            for item in stats.values()
        ]
        sess.flush()
        if owns_session:
            sess.commit()
            for row in rows:
                sess.refresh(row)
                sess.expunge(row)
        return rows
    finally:
        if owns_session:
            sess.close()


def rollback_skill_retirement(session: Session, retirement_id: str) -> EvolutionSkillRetirement:
    """把一次下线恢复到下线前的启用状态。不存在的技能行无法回滚。"""
    row = session.get(EvolutionSkillRetirement, retirement_id)
    if row is None:
        raise SkillRetirementError(404, "下线记录不存在")
    if row.status == "rolled_back":
        return row
    skill = session.get(Skill, row.skill_id)
    if skill is None:
        raise SkillRetirementError(409, "技能记录不存在，无法回滚")
    skill.enabled = row.previous_enabled
    skill.lifecycle = row.previous_lifecycle
    row.status = "rolled_back"
    row.rolled_back_at = datetime.now(UTC)
    session.add(skill)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _collect_stats(
    session: Session, period_start: datetime, period_end: datetime
) -> dict[tuple[str, str | None], _SkillStats]:
    stats: dict[tuple[str, str | None], _SkillStats] = {}

    def bucket(name: str, tenant_id: str | None) -> _SkillStats:
        key = (name, tenant_id or None)
        item = stats.get(key)
        if item is None:
            item = _SkillStats(skill_name=name, tenant_id=tenant_id or None)
            stats[key] = item
        return item

    conversations = {
        row.id: row
        for row in session.exec(select(Conversation).where(col(Conversation.created_at) <= period_end)).all()
    }
    messages = session.exec(
        select(Message).where(
            Message.role == "assistant",
            col(Message.created_at) >= period_start,
            col(Message.created_at) <= period_end,
        )
    ).all()
    conversation_skills: dict[str, set[str]] = defaultdict(set)
    for message in messages:
        names = _parse_names(message.skill_names)
        if not names:
            continue
        conv = conversations.get(message.conversation_id)
        tenant_id = conv.tenant_id if conv is not None else None
        for name in names:
            item = bucket(name, tenant_id)
            item.activation_count += 1
            item.conversation_ids.add(message.conversation_id)
            conversation_skills[message.conversation_id].add(name)

    reflects = session.exec(
        select(EvolutionReflectRun).where(
            EvolutionReflectRun.status == "succeeded",
            col(EvolutionReflectRun.started_at) >= period_start,
            col(EvolutionReflectRun.started_at) <= period_end,
        )
    ).all()
    for run in reflects:
        payload = _load_json(run.result_json)
        raw_improvements = payload.get("improvements")
        improvements = raw_improvements if isinstance(raw_improvements, list) else []
        attributed = set(conversation_skills.get(run.conversation_id, set()))
        for raw in improvements:
            if not isinstance(raw, dict):
                continue
            affected = str(raw.get("affected_skill") or "").strip()
            if affected:
                attributed.add(affected)
        if not attributed:
            continue
        for name in attributed:
            item = bucket(name, run.tenant_id)
            if run.id in item.reflect_ids:
                continue
            item.reflect_ids.add(run.id)
            item.conversation_ids.add(run.conversation_id)
            item.quality_scores.append(float(run.quality_score))
            _accumulate_issues(item, run.id, improvements, name)

    distill_runs = session.exec(
        select(EvolutionDistillRun).where(
            EvolutionDistillRun.status == "succeeded",
            col(EvolutionDistillRun.started_at) >= period_start,
            col(EvolutionDistillRun.started_at) <= period_end,
        )
    ).all()
    for distill_run in distill_runs:
        payload = _load_json(distill_run.result_json)
        raw_suggestions = payload.get("skill_suggestions")
        suggestions = raw_suggestions if isinstance(raw_suggestions, list) else []
        for raw in suggestions:
            if not isinstance(raw, dict):
                continue
            if str(raw.get("action") or "").strip() != "delete":
                continue
            name = str(raw.get("skill_name") or "").strip()
            if not name:
                continue
            for item in stats.values():
                if item.skill_name == name:
                    item.delete_suggestion_count += 1

    return stats


def _accumulate_issues(item: _SkillStats, run_id: str, improvements: list[Any], skill_name: str) -> None:
    for raw in improvements:
        if not isinstance(raw, dict):
            continue
        affected = str(raw.get("affected_skill") or "").strip()
        category = str(raw.get("category") or "").strip()
        severity = str(raw.get("severity") or "").strip()
        related = (not affected) or affected == skill_name
        if not related:
            continue
        item.issue_count += 1
        if severity == "critical":
            item.critical_count += 1
            item.reflects_with_critical.add(run_id)
            item.reflects_with_high_or_critical.add(run_id)
        elif severity == "high":
            item.high_count += 1
            item.reflects_with_high_or_critical.add(run_id)
        if category == "skill" or affected == skill_name:
            item.skill_issue_count += 1
            item.reflects_with_skill_issue.add(run_id)


def _load_json(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _evaluate_one(
    session: Session,
    stats: _SkillStats,
    *,
    window_hours: int,
    period_start: datetime,
    period_end: datetime,
    apply_retirements: bool,
    source_distill_run_id: str | None,
) -> EvolutionSkillEvaluation:
    reflect_count = len(stats.reflect_ids)
    avg_quality = (sum(stats.quality_scores) / len(stats.quality_scores)) if stats.quality_scores else 0.0
    high_rate = (len(stats.reflects_with_high_or_critical) / reflect_count) if reflect_count else 0.0
    critical_rate = (len(stats.reflects_with_critical) / reflect_count) if reflect_count else 0.0
    skill_issue_rate = (len(stats.reflects_with_skill_issue) / reflect_count) if reflect_count else 0.0
    decision, reason = _decide(
        reflect_count=reflect_count,
        avg_quality=avg_quality,
        high_rate=high_rate,
        critical_rate=critical_rate,
        skill_issue_count=stats.skill_issue_count,
    )
    skill = _resolve_skill(session, stats.skill_name, stats.tenant_id)
    if decision == "retire" and not apply_retirements:
        reason = f"{reason}；进化总开关关闭，仅记录评估、不执行下线"

    metrics: dict[str, Any] = {
        "high_severity_rate": high_rate,
        "critical_rate": critical_rate,
        "skill_issue_rate": skill_issue_rate,
        "min_reflect_samples": MIN_REFLECT_SAMPLES,
        "low_quality_score": LOW_QUALITY_SCORE,
        "high_severity_rate_threshold": HIGH_SEVERITY_RATE,
        "critical_rate_threshold": CRITICAL_RATE,
        "retirement_id": None,
        "apply_retirements": apply_retirements,
        "conversation_ids": sorted(cid for cid in stats.conversation_ids if cid),
    }
    row = EvolutionSkillEvaluation(
        skill_id=skill.id if skill is not None else None,
        skill_name=stats.skill_name,
        tenant_id=stats.tenant_id,
        window_hours=window_hours,
        period_start=period_start,
        period_end=period_end,
        activation_count=stats.activation_count,
        conversation_count=len(stats.conversation_ids),
        reflect_count=reflect_count,
        avg_quality_score=avg_quality,
        issue_count=stats.issue_count,
        critical_count=stats.critical_count,
        high_count=stats.high_count,
        skill_issue_count=stats.skill_issue_count,
        delete_suggestion_count=stats.delete_suggestion_count,
        decision=decision,
        applied=False,
        reason=reason,
        metrics_json=_dump(metrics),
        source_distill_run_id=source_distill_run_id,
    )
    session.add(row)
    session.flush()
    if decision == "retire" and apply_retirements:
        applied, reason, retirement_id = _apply_retirement(session, skill, stats, reason, row.id)
        row.applied = applied
        row.reason = reason
        metrics["retirement_id"] = retirement_id
        row.metrics_json = _dump(metrics)
        session.add(row)
    return row


def _decide(
    *,
    reflect_count: int,
    avg_quality: float,
    high_rate: float,
    critical_rate: float,
    skill_issue_count: int,
) -> tuple[str, str]:
    if reflect_count < MIN_REFLECT_SAMPLES:
        return "insufficient_data", "有效反思样本不足，不下线"
    if critical_rate >= CRITICAL_RATE and skill_issue_count >= 2:
        return "retire", "严重问题比例偏高，且多次指向该技能"
    if avg_quality < LOW_QUALITY_SCORE and high_rate >= HIGH_SEVERITY_RATE:
        return "retire", "平均质量偏低，且多数反思存在高严重度问题"
    return "keep", "效果指标未达到下线条件"


def _resolve_skill(session: Session, name: str, tenant_id: str | None) -> Skill | None:
    rows: list[Skill] = []
    if tenant_id:
        rows.extend(
            session.exec(
                select(Skill).where(
                    Skill.name == name,
                    Skill.tenant_id == tenant_id,
                    Skill.scope == "private",
                )
            ).all()
        )
    if not rows:
        rows.extend(
            session.exec(
                select(Skill).where(
                    Skill.name == name,
                    Skill.scope == "global",
                )
            ).all()
        )
    if not rows:
        return None
    active = [row for row in rows if row.lifecycle == "active"]
    pool = active or rows
    enabled = [row for row in pool if row.enabled]
    return (enabled or pool)[0]


def _apply_retirement(
    session: Session,
    skill: Skill | None,
    stats: _SkillStats,
    reason: str,
    evaluation_id: str,
) -> tuple[bool, str, str | None]:
    if skill is None:
        return False, f"{reason}；未找到可下线的持久化技能，不修改内置清单", None
    if skill.lifecycle == "draft":
        return False, f"{reason}；草稿技能不自动下线", None
    existing = session.exec(
        select(EvolutionSkillRetirement).where(
            EvolutionSkillRetirement.skill_id == skill.id,
            EvolutionSkillRetirement.status == "retired",
        )
    ).first()
    if existing is not None and (not skill.enabled) and skill.lifecycle == "retired":
        return False, f"{reason}；该技能已处于下线状态", existing.id
    previous_enabled = skill.enabled
    previous_lifecycle = skill.lifecycle
    previous_name_key = skill.name_key
    skill.enabled = False
    skill.lifecycle = "retired"
    session.add(skill)
    retirement = EvolutionSkillRetirement(
        evaluation_id=evaluation_id,
        skill_id=skill.id,
        skill_name=stats.skill_name,
        tenant_id=stats.tenant_id,
        previous_enabled=previous_enabled,
        previous_lifecycle=previous_lifecycle,
        previous_name_key=previous_name_key,
        status="retired",
        reason=reason,
        retired_at=datetime.now(UTC),
    )
    session.add(retirement)
    session.flush()
    return True, reason, retirement.id
