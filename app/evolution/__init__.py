"""进化系统 — 自我改进闭环。

提供：
- ``Distiller``：夜间蒸馏器，批量分析近期对话提炼改进建议
- ``EvolutionScheduler``：cron 定时调度器，按配置触发蒸馏任务
- ``DistillResult`` / ``DistillInsight``：蒸馏结果数据模型

骨架阶段仅支持 LLM 驱动的批量对话分析；内核打磨阶段补充：
- Skill 自动更新（蒸馏结果 → Skill manifest）
- 知识库缺口自动发现与补充
- 改进趋势追踪（时间序列分析）
"""

from app.evolution.distiller import Distiller
from app.evolution.models import DistillInsight, DistillResult, SkillSuggestion
from app.evolution.scheduler import EvolutionScheduler

__all__ = [
    "Distiller",
    "DistillResult",
    "DistillInsight",
    "SkillSuggestion",
    "EvolutionScheduler",
]