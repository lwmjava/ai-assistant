"""进化系统模块 — Reflect 异步反思 + Distill 夜间蒸馏的自我改进闭环。

提供 Agent 自我进化能力：
- ``Reflector``：对话结束后异步反思，提取改进点与待办事项
- ``ReflectResult`` / ``ImprovementPoint`` / ``ActionItem``：反思结果数据类型
- ``Severity`` / ``ImprovementCategory``：反思相关枚举
- ``Distiller``：夜间蒸馏器，批量分析近期对话提炼改进建议
- ``EvolutionScheduler``：定时调度器，按配置触发蒸馏任务
- ``DistillResult`` / ``DistillInsight`` / ``SkillSuggestion``：蒸馏结果数据类型

当前实现：LLM 驱动的单轮反思与批量对话分析；开关打开时写入可查询运行记录。
"""
from app.evolution.distiller import Distiller
from app.evolution.models import (
    ActionItem,
    DistillInsight,
    DistillResult,
    ImprovementCategory,
    ImprovementPoint,
    ReflectResult,
    Severity,
    SkillSuggestion,
)
from app.evolution.records import (
    EvolutionCandidate,
    EvolutionDistillRun,
    EvolutionReflectRun,
    EvolutionSkillEvaluation,
    EvolutionSkillRetirement,
)
from app.evolution.reflector import Reflector
from app.evolution.scheduler import EvolutionScheduler
from app.evolution.store import distill_and_record, list_distill_runs, list_reflect_runs, reflect_and_record

__all__ = [
    # Reflect（单轮对话反思）
    "Reflector",
    "ReflectResult",
    "ImprovementPoint",
    "ActionItem",
    "Severity",
    "ImprovementCategory",
    # Distill（批量对话蒸馏）
    "Distiller",
    "DistillResult",
    "DistillInsight",
    "SkillSuggestion",
    "EvolutionScheduler",
    "EvolutionReflectRun",
    "EvolutionDistillRun",
    "EvolutionCandidate",
    "reflect_and_record",
    "distill_and_record",
    "list_reflect_runs",
    "list_distill_runs",
    "EvolutionSkillEvaluation",
    "EvolutionSkillRetirement",
]
