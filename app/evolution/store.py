"""反思与蒸馏运行记录的写入与查询。"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.database import engine as default_engine
from app.evolution.distiller import Distiller
from app.evolution.models import DistillResult, ReflectResult
from app.evolution.records import (
    EvolutionCandidate,
    EvolutionDistillRun,
    EvolutionReflectRun,
    EvolutionSkillEvaluation,
    EvolutionSkillRetirement,
)
from app.evolution.reflector import Reflector
from app.llm.base import LLMProvider

logger = logging.getLogger(__name__)

_RUNNING_STALE_HOURS = 6


def evolution_tables():
    """返回本模块管理的数据表。"""
    return [
        EvolutionReflectRun.__table__,
        EvolutionDistillRun.__table__,
        EvolutionCandidate.__table__,
        EvolutionSkillEvaluation.__table__,
        EvolutionSkillRetirement.__table__,
    ]


def ensure_evolution_tables(bind: Engine | None = None) -> None:
    """在目标库上幂等创建反思与蒸馏运行表。"""
    from sqlmodel import SQLModel

    SQLModel.metadata.create_all(bind or default_engine, tables=evolution_tables())


def _session(bind: Engine | None = None) -> Session:
    return Session(bind or default_engine)


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _duration_ms(started_at: datetime, finished_at: datetime) -> float:
    return max(0.0, (_aware(finished_at) - _aware(started_at)).total_seconds() * 1000.0)


def _dump(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def list_reflect_runs(session: Session, conversation_id: str | None = None) -> list[EvolutionReflectRun]:
    """按时间倒序列出反思运行记录。"""
    stmt = select(EvolutionReflectRun).order_by(col(EvolutionReflectRun.started_at).desc())
    if conversation_id is not None:
        stmt = stmt.where(EvolutionReflectRun.conversation_id == conversation_id)
    return list(session.exec(stmt).all())


def list_distill_runs(session: Session) -> list[EvolutionDistillRun]:
    """按时间倒序列出蒸馏运行记录。"""
    stmt = select(EvolutionDistillRun).order_by(col(EvolutionDistillRun.started_at).desc())
    return list(session.exec(stmt).all())


def _add_reflect_run(
    session: Session,
    result: ReflectResult,
    *,
    tenant_id: str | None,
    status: str,
    llm_call_count: int,
    budget_max_llm_calls: int,
    budget_capped: bool,
    started_at: datetime,
    finished_at: datetime,
) -> EvolutionReflectRun:
    row = EvolutionReflectRun(
        conversation_id=result.conversation_id,
        tenant_id=tenant_id,
        status=status,
        summary=result.summary or "",
        quality_score=result.quality_score,
        revision_count=result.revision_count,
        improvement_count=len(result.improvements),
        action_item_count=len(result.action_items),
        critical_count=result.critical_count,
        llm_call_count=llm_call_count,
        budget_max_llm_calls=budget_max_llm_calls,
        budget_capped=budget_capped,
        result_json=_dump(result.to_dict()),
        error=result.error,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=_duration_ms(started_at, finished_at),
    )
    session.add(row)
    return row


async def reflect_and_record(
    conversation_text: str,
    *,
    llm: LLMProvider,
    conversation_id: str = "",
    tenant_id: str | None = None,
    quality_score: float = 0.0,
    revision_count: int = 0,
    bind: Engine | None = None,
) -> ReflectResult:
    """执行一次反思并写入可查询运行记录。"""
    ensure_evolution_tables(bind)
    started_at = datetime.now(UTC)
    budget = max(0, int(settings.EVOLUTION_REFLECT_BUDGET_MAX_LLM_CALLS))
    budget_capped = False
    llm_call_count = 0

    if budget <= 0:
        result = ReflectResult(
            conversation_id=conversation_id,
            quality_score=quality_score,
            revision_count=revision_count,
            error="反思预算已用尽",
        )
        budget_capped = True
        status = "skipped"
    else:
        reflector = Reflector(llm)
        result = await reflector.reflect(
            conversation_text,
            conversation_id=conversation_id,
            quality_score=quality_score,
            revision_count=revision_count,
        )
        llm_call_count = 1
        status = "failed" if result.error else "succeeded"

    finished_at = datetime.now(UTC)
    with _session(bind) as session:
        run = _add_reflect_run(
            session,
            result,
            tenant_id=tenant_id,
            status=status,
            llm_call_count=llm_call_count,
            budget_max_llm_calls=budget,
            budget_capped=budget_capped,
            started_at=started_at,
            finished_at=finished_at,
        )
        session.commit()
    return result


def _interval_elapsed(session: Session, now: datetime) -> bool:
    min_interval = timedelta(hours=settings.EVOLUTION_DISTILL_MIN_INTERVAL_HOURS)
    stmt = (
        select(EvolutionDistillRun)
        .where(col(EvolutionDistillRun.status).in_(("running", "succeeded", "failed", "skipped")))
        .order_by(col(EvolutionDistillRun.started_at).desc())
    )
    last = session.exec(stmt).first()
    if last is None:
        return True
    return _aware(now) - _aware(last.started_at) >= min_interval


def _expire_stale_running_runs(session: Session, now: datetime) -> None:
    stale_before = now - timedelta(hours=_RUNNING_STALE_HOURS)
    stmt = select(EvolutionDistillRun).where(EvolutionDistillRun.status == "running")
    for row in session.exec(stmt).all():
        started = _aware(row.started_at)
        if started <= _aware(stale_before):
            row.status = "failed"
            row.error = row.error or "蒸馏运行超时未完成"
            row.finished_at = now
            row.duration_ms = _duration_ms(started, now)
            session.add(row)


def _has_active_run(session: Session) -> bool:
    stmt = select(EvolutionDistillRun).where(EvolutionDistillRun.status == "running")
    return session.exec(stmt).first() is not None


def _finish_distill_run(
    session: Session,
    run_id: str,
    result: DistillResult,
    *,
    status: str,
    llm_call_count: int,
    budget_capped: bool,
    finished_at: datetime,
) -> EvolutionDistillRun:
    row = session.get(EvolutionDistillRun, run_id)
    if row is None:
        raise RuntimeError("蒸馏运行记录不存在")
    row.status = status
    row.analysis_period = result.analysis_period or row.analysis_period
    row.conversations_analyzed = result.conversations_analyzed
    row.messages_analyzed = result.messages_analyzed
    row.insight_count = len(result.insights)
    row.skill_suggestion_count = len(result.skill_suggestions)
    row.llm_call_count = llm_call_count
    row.budget_capped = budget_capped
    row.result_json = _dump(result.to_dict())
    row.error = result.error
    row.finished_at = finished_at
    row.duration_ms = _duration_ms(row.started_at, finished_at)
    session.add(row)
    return row


async def distill_and_record(
    *,
    llm: LLMProvider | None = None,
    bind: Engine | None = None,
    now: datetime | None = None,
    respect_schedule: bool = True,
    trigger: str = "scheduler",
) -> EvolutionDistillRun | None:
    """按配置执行一次蒸馏并留下运行记录。

    开关关闭、未到时间窗口或未过最小间隔时不创建记录。
    """
    if not settings.EVOLUTION_DISTILL_ENABLED:
        return None

    bind = bind or default_engine
    ensure_evolution_tables(bind)
    now = now or datetime.now(UTC)

    if respect_schedule:
        from app.evolution.scheduler import _is_in_time_window

        if not _is_in_time_window(now):
            return None

    with _session(bind) as session:
        _expire_stale_running_runs(session, now)
        session.commit()
        if respect_schedule and not _interval_elapsed(session, now):
            return None
        if _has_active_run(session):
            return None
        started = EvolutionDistillRun(
            trigger=trigger,
            status="running",
            analysis_period=f"最近 {settings.EVOLUTION_DISTILL_HOURS} 小时",
            budget_max_conversations=settings.EVOLUTION_DISTILL_MAX_CONVERSATIONS,
            budget_max_llm_calls=max(0, int(settings.EVOLUTION_DISTILL_BUDGET_MAX_LLM_CALLS)),
            started_at=now,
            result_json="{}",
        )
        session.add(started)
        session.commit()
        session.refresh(started)
        run_id = started.id

    budget = max(0, int(settings.EVOLUTION_DISTILL_BUDGET_MAX_LLM_CALLS))
    budget_capped = False
    llm_call_count = 0
    result = DistillResult(analysis_period=f"最近 {settings.EVOLUTION_DISTILL_HOURS} 小时")

    try:
        if budget <= 0:
            result.error = "蒸馏预算已用尽"
            budget_capped = True
            status = "skipped"
        else:
            distiller = Distiller(llm)
            result = await distiller.distill_recent(
                hours=settings.EVOLUTION_DISTILL_HOURS,
                max_conversations=settings.EVOLUTION_DISTILL_MAX_CONVERSATIONS,
                bind=bind,
            )
            # 无对话时不会调用模型；有分析文本时计一次调用，解析失败也已消耗预算。
            if result.conversations_analyzed > 0 and result.summary != "对话内容为空，无法分析。":
                llm_call_count = 1
            status = "failed" if result.error else "succeeded"
    except Exception as exc:  # noqa: BLE001 — 蒸馏失败也要留下记录
        logger.exception("蒸馏执行失败")
        result.error = str(exc)
        status = "failed"

    finished_at = datetime.now(UTC)
    with _session(bind) as session:
        row = _finish_distill_run(
            session,
            run_id,
            result,
            status=status,
            llm_call_count=llm_call_count,
            budget_capped=budget_capped,
            finished_at=finished_at,
        )
        session.commit()
        session.refresh(row)
        session.expunge(row)
        return row
