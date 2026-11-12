"""反思与蒸馏运行记录的持久化模型。

这些表由本包在写入前按需建出，不改变生产默认开关。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, Column, Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin


def _uuid() -> str:
    """生成短 UUID 主键。"""
    return uuid.uuid4().hex


class EvolutionReflectRun(SQLModel, TimestampMixin, table=True):
    """一次对话结束后的反思运行记录。"""

    __tablename__ = "evolution_reflect_runs"
    __table_args__ = (
        CheckConstraint("status IN ('succeeded','failed','skipped')"),
        CheckConstraint("duration_ms >= 0"),
        CheckConstraint("llm_call_count >= 0"),
        CheckConstraint("improvement_count >= 0"),
        CheckConstraint("action_item_count >= 0"),
    )

    id: str = Field(default_factory=_uuid, primary_key=True, max_length=64)
    conversation_id: str = Field(index=True, max_length=64)
    tenant_id: str | None = Field(default=None, index=True, max_length=64)
    status: str = Field(index=True, max_length=16)
    summary: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    quality_score: float = 0.0
    revision_count: int = 0
    improvement_count: int = 0
    action_item_count: int = 0
    critical_count: int = 0
    llm_call_count: int = 0
    budget_max_llm_calls: int = 0
    budget_capped: bool = False
    result_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    started_at: datetime
    finished_at: datetime
    duration_ms: float = 0.0


class EvolutionDistillRun(SQLModel, TimestampMixin, table=True):
    """一次按配置执行的夜间蒸馏运行记录。"""

    __tablename__ = "evolution_distill_runs"
    __table_args__ = (
        CheckConstraint("status IN ('running','succeeded','failed','skipped')"),
        CheckConstraint("duration_ms >= 0"),
        CheckConstraint("llm_call_count >= 0"),
        CheckConstraint("conversations_analyzed >= 0"),
        CheckConstraint("messages_analyzed >= 0"),
    )

    id: str = Field(default_factory=_uuid, primary_key=True, max_length=64)
    trigger: str = Field(default="scheduler", max_length=32, index=True)
    status: str = Field(index=True, max_length=16)
    analysis_period: str = Field(default="", max_length=128)
    conversations_analyzed: int = 0
    messages_analyzed: int = 0
    insight_count: int = 0
    skill_suggestion_count: int = 0
    llm_call_count: int = 0
    budget_max_conversations: int = 0
    budget_max_llm_calls: int = 0
    budget_capped: bool = False
    result_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: float = 0.0


class EvolutionCandidate(SQLModel, TimestampMixin, table=True):
    """反思或蒸馏产出的技能候选 / 待办，确认前不会生效。"""

    __tablename__ = "evolution_candidates"
    __table_args__ = (
        CheckConstraint("kind IN ('skill','todo')"),
        CheckConstraint("status IN ('pending','rejected','promoted','accepted')"),
        CheckConstraint("source_type IN ('reflect','distill')"),
        UniqueConstraint("source_type", "source_run_id", "fingerprint", name="uq_evolution_candidate_source"),
    )

    id: str = Field(default_factory=_uuid, primary_key=True, max_length=64)
    kind: str = Field(index=True, max_length=16)
    status: str = Field(default="pending", index=True, max_length=16)
    source_type: str = Field(index=True, max_length=16)
    source_run_id: str = Field(index=True, max_length=64)
    conversation_id: str | None = Field(default=None, index=True, max_length=64)
    tenant_id: str | None = Field(default=None, index=True, max_length=64)
    owner_user_id: str | None = Field(default=None, index=True, max_length=64)
    title: str = Field(default="", max_length=256)
    description: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    payload_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    fingerprint: str = Field(max_length=64, index=True)
    reviewed_by: str | None = Field(default=None, max_length=64)
    reviewed_at: datetime | None = None
    review_note: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    skill_draft_id: str | None = Field(default=None, index=True, max_length=64)


class EvolutionSkillEvaluation(SQLModel, TimestampMixin, table=True):
    """一次按时间窗口汇总的技能效果评估记录。"""

    __tablename__ = "evolution_skill_evaluations"
    __table_args__ = (
        CheckConstraint("decision IN ('keep','retire','insufficient_data')"),
        CheckConstraint("activation_count >= 0"),
        CheckConstraint("conversation_count >= 0"),
        CheckConstraint("reflect_count >= 0"),
        CheckConstraint("issue_count >= 0"),
        CheckConstraint("critical_count >= 0"),
        CheckConstraint("high_count >= 0"),
        CheckConstraint("skill_issue_count >= 0"),
        CheckConstraint("delete_suggestion_count >= 0"),
        CheckConstraint("window_hours > 0"),
    )

    id: str = Field(default_factory=_uuid, primary_key=True, max_length=64)
    skill_id: str | None = Field(default=None, index=True, max_length=64)
    skill_name: str = Field(index=True, max_length=64)
    tenant_id: str | None = Field(default=None, index=True, max_length=64)
    window_hours: int
    period_start: datetime
    period_end: datetime
    activation_count: int = 0
    conversation_count: int = 0
    reflect_count: int = 0
    avg_quality_score: float = 0.0
    issue_count: int = 0
    critical_count: int = 0
    high_count: int = 0
    skill_issue_count: int = 0
    delete_suggestion_count: int = 0
    decision: str = Field(index=True, max_length=32)
    applied: bool = False
    reason: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    metrics_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    source_distill_run_id: str | None = Field(default=None, index=True, max_length=64)


class EvolutionSkillRetirement(SQLModel, TimestampMixin, table=True):
    """技能下线记录。只标记停用，不删除原记录，便于回滚。"""

    __tablename__ = "evolution_skill_retirements"
    __table_args__ = (
        CheckConstraint("status IN ('retired','rolled_back')"),
    )

    id: str = Field(default_factory=_uuid, primary_key=True, max_length=64)
    evaluation_id: str = Field(index=True, max_length=64)
    skill_id: str = Field(index=True, max_length=64)
    skill_name: str = Field(index=True, max_length=64)
    tenant_id: str | None = Field(default=None, index=True, max_length=64)
    previous_enabled: bool
    previous_lifecycle: str = Field(max_length=16)
    previous_name_key: str = Field(default="", max_length=256)
    status: str = Field(default="retired", index=True, max_length=16)
    reason: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    retired_at: datetime
    rolled_back_at: datetime | None = None
