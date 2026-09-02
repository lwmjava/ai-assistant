"""进化系统数据模型。

定义蒸馏分析的结果结构：蒸馏洞察、技能改善建议、批量分析结果。
"""

from dataclasses import dataclass, field
from enum import Enum


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