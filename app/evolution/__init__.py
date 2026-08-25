"""进化系统模块。

提供 Agent 自我进化能力：
- ``Reflector``：对话结束后异步反思，提取改进点与待办事项
- ``ReflectResult`` / ``ImprovementPoint`` / ``ActionItem``：数据类型
- ``Severity`` / ``ImprovementCategory``：枚举

对外暴露：
- ``Reflector``：反思器
- ``ReflectResult``：反思结果
- ``ImprovementPoint``：改进建议
- ``ActionItem``：待办事项
- ``Severity`` / ``ImprovementCategory``：枚举
"""

from app.evolution.models import (
    ActionItem,
    ImprovementCategory,
    ImprovementPoint,
    ReflectResult,
    Severity,
)
from app.evolution.reflector import Reflector

__all__ = [
    "Reflector",
    "ReflectResult",
    "ImprovementPoint",
    "ActionItem",
    "Severity",
    "ImprovementCategory",
]