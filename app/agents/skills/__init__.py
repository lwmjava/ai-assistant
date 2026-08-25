"""技能系统模块。

提供 YAML 声明式技能注册、匹配、激活功能。

对外暴露：
- ``SkillManager``：技能管理器
- ``SkillManifest`` / ``SkillMatch`` / ``SkillContext``：数据类型
- ``SkillMode`` / ``TriggerType``：枚举
- ``get_skill_manager``：全局单例
"""

from app.agents.skills.base import (
    SkillContext,
    SkillManifest,
    SkillMatch,
    SkillMode,
    SkillTrigger,
    TriggerType,
)
from app.agents.skills.manager import SkillManager, get_skill_manager, reset_skill_manager

__all__ = [
    "SkillManager",
    "SkillManifest",
    "SkillMatch",
    "SkillContext",
    "SkillMode",
    "SkillTrigger",
    "TriggerType",
    "get_skill_manager",
    "reset_skill_manager",
]