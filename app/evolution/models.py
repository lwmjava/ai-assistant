"""进化系统数据类型 — Reflect 反思 + 改进点 + Action Item。

定义：
- ``ReflectResult``：一次反思的完整产出（改进点 + 待办事项）
- ``ImprovementPoint``：单条改进建议（严重程度 + 分类 + 建议操作）
- ``ActionItem``：对话中提取的待办事项
- ``Severity`` / ``ImprovementCategory``：枚举类型

骨架阶段仅支持内存中的数据类型；内核打磨阶段补充：
- DB 持久化模型（ReflectRecord SQLModel）
- 改进点 → Skill manifest 自动更新
- Action Item → Workflow 调度器写入
"""

from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    """改进建议的严重程度。"""

    CRITICAL = "critical"  # 严重：事实错误 / 幻觉 / 安全风险
    HIGH = "high"  # 高：遗漏关键信息 / 逻辑错误
    MEDIUM = "medium"  # 中：表述不清晰 / 结构不佳
    LOW = "low"  # 低：微调优化 / 风格建议


class ImprovementCategory(str, Enum):
    """改进建议的分类。"""

    ACCURACY = "accuracy"  # 事实准确性
    COMPLETENESS = "completeness"  # 信息完整性
    CLARITY = "clarity"  # 表述清晰度
    STRUCTURE = "structure"  # 结构组织
    SAFETY = "safety"  # 安全性
    EFFICIENCY = "efficiency"  # 效率（工具调用 / 步骤数）
    SKILL = "skill"  # 技能相关（触发 / 提示词 / 工具）
    OTHER = "other"


@dataclass
class ImprovementPoint:
    """单条改进建议。

    由 Reflector 从对话审查中提取，每条代表一个可改进的方面。
    """

    severity: Severity = Severity.MEDIUM
    category: ImprovementCategory = ImprovementCategory.OTHER
    summary: str = ""  # 一句话摘要（如 "遗漏了数据库表结构说明"）
    detail: str = ""  # 详细描述（如 "在回答 SQL 查询时，未提及相关的表结构..."）
    suggestion: str = ""  # 改进建议（如 "建议在回答数据库类问题时，先列出相关表结构"）
    affected_skill: str | None = None  # 关联的技能名称（若适用）

    def to_dict(self) -> dict:
        return {
            "severity": self.severity.value,
            "category": self.category.value,
            "summary": self.summary,
            "detail": self.detail,
            "suggestion": self.suggestion,
            "affected_skill": self.affected_skill,
        }


@dataclass
class ActionItem:
    """对话中提取的待办事项。

    用户在对话中隐含或明确表达的后续任务。
    """

    description: str = ""  # 待办事项描述
    priority: str = "medium"  # high / medium / low
    assignee_hint: str | None = None  # 隐含的负责人（如 "我" / "你" / 用户名）
    deadline_hint: str | None = None  # 隐含的截止时间（自然语言）

    def to_dict(self) -> dict:
        return {
            "description": self.description,
            "priority": self.priority,
            "assignee_hint": self.assignee_hint,
            "deadline_hint": self.deadline_hint,
        }


@dataclass
class ReflectResult:
    """一次反思的完整产出。

    包含：是否触发、改进点列表、待办事项列表、反思摘要。
    """

    conversation_id: str = ""
    # 反思摘要
    summary: str = ""  # 整体反思摘要（如 "本轮对话整体质量较好，但存在 2 个改进点"）
    # 改进点
    improvements: list[ImprovementPoint] = field(default_factory=list)
    # 待办事项
    action_items: list[ActionItem] = field(default_factory=list)
    # 元信息
    quality_score: float = 0.0  # 管线中的 QualityGate 评分
    revision_count: int = 0  # 自纠错轮数
    error: str | None = None  # 反思过程异常信息

    @property
    def has_improvements(self) -> bool:
        return len(self.improvements) > 0

    @property
    def has_action_items(self) -> bool:
        return len(self.action_items) > 0

    @property
    def critical_count(self) -> int:
        return sum(1 for imp in self.improvements if imp.severity == Severity.CRITICAL)

    def to_dict(self) -> dict:
        return {
            "conversation_id": self.conversation_id,
            "summary": self.summary,
            "improvements": [imp.to_dict() for imp in self.improvements],
            "action_items": [item.to_dict() for item in self.action_items],
            "quality_score": self.quality_score,
            "revision_count": self.revision_count,
            "error": self.error,
        }