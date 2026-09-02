"""进化系统数据类型 — Reflect 反思与 Distill 蒸馏的数据结构定义。

Reflect（单轮对话反思）：
- ``Severity``：改进建议的严重程度枚举
- ``ImprovementCategory``：改进建议的分类枚举
- ``ImprovementPoint``：单条改进建议（严重程度 + 分类 + 建议操作）
- ``ActionItem``：从对话中提取的待办事项
- ``ReflectResult``：一次反思的完整产出（改进点 + 待办事项 + 摘要 + 元信息）

Distill（批量对话蒸馏）：
- ``InsightSeverity``：洞察严重程度枚举
- ``InsightCategory``：洞察分类枚举
- ``DistillInsight``：单条蒸馏洞察（可追溯来源会话 + 发生频次）
- ``SkillSuggestion``：技能创建 / 修改建议
- ``DistillResult``：一次蒸馏分析的完整结果（洞察 + 技能建议 + 统计）

当前实现：以上均为内存级数据类型，随进程生命周期存在。可按需扩展：
- 反思与蒸馏结果的数据库持久化模型（如 ReflectRecord SQLModel）
- 改进点 / 技能建议 → Skill manifest 自动更新
- 待办事项 → Workflow 调度器写入
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
    OTHER = "other"  # 其他：未归入上述分类的改进建议


class InsightSeverity(str, Enum):
    """洞察严重程度。"""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class InsightCategory(str, Enum):
    """洞察分类。"""

    ACCURACY = "accuracy"  # 事实准确性问题
    COMPLETENESS = "completeness"  # 信息完整性问题
    CLARITY = "clarity"  # 表达清晰度
    EFFICIENCY = "efficiency"  # 工具使用效率
    PATTERN = "pattern"  # 用户行为模式
    GAP = "gap"  # 知识库/能力缺口
    SKILL = "skill"  # 技能改进建议
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


@dataclass
class DistillInsight:
    """单条蒸馏洞察。

    从批量对话中提炼的一条可执行改进建议，包含严重度、分类、具体描述。
    """

    severity: InsightSeverity = InsightSeverity.MEDIUM
    category: InsightCategory = InsightCategory.OTHER
    summary: str = ""
    detail: str = ""
    suggestion: str = ""
    # 涉及的会话 ID 列表（可追溯）
    conversation_ids: list[str] = field(default_factory=list)
    # 发生频次（同类问题出现的次数）
    frequency: int = 0


@dataclass
class SkillSuggestion:
    """技能改善建议。

    从蒸馏分析中提取的技能创建/修改建议，供后续自动更新 Skill manifest。
    """

    skill_name: str = ""
    action: str = "create"  # create | update | delete
    description: str = ""
    # 建议的触发条件（关键词/正则）
    triggers: list[str] = field(default_factory=list)
    # 建议的提示词注入内容
    prompt_injection: str = ""
    # 关联的洞察 ID
    insight_index: int = 0


@dataclass
class DistillResult:
    """一次蒸馏分析的完整结果。

    包含从批量对话中提炼的全部洞察、技能建议和统计摘要。
    """

    # 分析范围
    conversations_analyzed: int = 0
    messages_analyzed: int = 0
    analysis_period: str = ""  # 人类可读的时间范围

    # 分析结果
    summary: str = ""  # 整体分析摘要
    insights: list[DistillInsight] = field(default_factory=list)
    skill_suggestions: list[SkillSuggestion] = field(default_factory=list)

    # 统计
    total_issues: int = 0
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0

    # 错误信息
    error: str | None = None

    def calculate_stats(self) -> None:
        """根据 insights 计算统计字段。"""
        self.total_issues = len(self.insights)
        self.critical_count = sum(
            1 for i in self.insights if i.severity == InsightSeverity.CRITICAL
        )
        self.high_count = sum(
            1 for i in self.insights if i.severity == InsightSeverity.HIGH
        )
        self.medium_count = sum(
            1 for i in self.insights if i.severity == InsightSeverity.MEDIUM
        )
        self.low_count = sum(
            1 for i in self.insights if i.severity == InsightSeverity.LOW
        )
