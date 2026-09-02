"""进化系统模块 — Reflect 异步反思 + Distill 夜间蒸馏的自我改进闭环。

提供 Agent 自我进化能力：
- ``Reflector``：对话结束后异步反思，提取改进点与待办事项
- ``ReflectResult`` / ``ImprovementPoint`` / ``ActionItem``：反思结果数据类型
- ``Severity`` / ``ImprovementCategory``：反思相关枚举
- ``Distiller``：夜间蒸馏器，批量分析近期对话提炼改进建议
- ``EvolutionScheduler``：定时调度器，按配置触发蒸馏任务
- ``DistillResult`` / ``DistillInsight`` / ``SkillSuggestion``：蒸馏结果数据类型

当前实现：LLM 驱动的单轮反思与批量对话分析，结果以日志输出。可按需扩展：
- Skill 自动更新（反思 / 蒸馏结果 → Skill manifest）
- 知识库缺口自动发现与补充
- 改进趋势追踪（时间序列分析）
"""
from app.evolution.models import (
    ActionItem,
    ImprovementCategory,
    ImprovementPoint,
    ReflectResult,
    Severity,
)
from app.evolution.reflector import Reflector


from app.evolution.distiller import Distiller
from app.evolution.models import DistillInsight, DistillResult, SkillSuggestion
from app.evolution.scheduler import EvolutionScheduler
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
]