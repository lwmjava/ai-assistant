"""进化系统（Distill 夜间蒸馏）的冒烟测试。

覆盖蒸馏洞察与技能建议的数据类构造、结果统计计算、枚举取值，
以及调度器时间窗口判断。

其中 ``test_distill_insight_instantiation`` 是回归测试：DistillInsight 曾因
合并时丢失 ``@dataclass`` 装饰器，导致以关键字参数构造时抛出 TypeError，
而该异常又被 distiller 的 except 静默吞掉，使蒸馏洞察被全部丢弃。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime, timezone

from app.evolution.models import (
    DistillInsight,
    DistillResult,
    InsightCategory,
    InsightSeverity,
    SkillSuggestion,
)
from app.evolution.scheduler import _is_in_time_window


def test_distill_insight_instantiation() -> None:
    """蒸馏洞察可正确构造并保留来源会话与频次。

    回归测试：本类曾因缺少 @dataclass 装饰器导致关键字参数构造抛 TypeError。
    """
    insight = DistillInsight(
        severity=InsightSeverity.HIGH,
        category=InsightCategory.ACCURACY,
        summary="幻觉问题",
        detail="模型在回答中编造了不存在的数据",
        suggestion="增加事实核查步骤",
        conversation_ids=["abc123", "def456"],
        frequency=5,
    )
    assert insight.severity == InsightSeverity.HIGH
    assert insight.category == InsightCategory.ACCURACY
    assert insight.summary == "幻觉问题"
    assert insight.frequency == 5
    assert len(insight.conversation_ids) == 2


def test_skill_suggestion_instantiation() -> None:
    """技能建议的数据类可正确构造。"""
    skill = SkillSuggestion(
        skill_name="weather_qa",
        action="create",
        description="天气查询技能",
        triggers=["天气", "下雨", "温度"],
        prompt_injection="请优先查询天气 API 获取实时数据",
        insight_index=0,
    )
    assert skill.skill_name == "weather_qa"
    assert skill.action == "create"
    assert len(skill.triggers) == 3
    assert skill.insight_index == 0


def test_distill_result_calculate_stats() -> None:
    """统计计算按严重程度正确归类各级别数量。"""
    result = DistillResult(
        conversations_analyzed=10,
        messages_analyzed=50,
        analysis_period="最近 24 小时",
        summary="整体质量良好",
        insights=[
            DistillInsight(severity=InsightSeverity.CRITICAL, category=InsightCategory.ACCURACY),
            DistillInsight(severity=InsightSeverity.HIGH, category=InsightCategory.COMPLETENESS),
            DistillInsight(severity=InsightSeverity.HIGH, category=InsightCategory.EFFICIENCY),
            DistillInsight(severity=InsightSeverity.MEDIUM, category=InsightCategory.CLARITY),
            DistillInsight(severity=InsightSeverity.LOW, category=InsightCategory.PATTERN),
        ],
    )
    result.calculate_stats()
    assert result.total_issues == 5
    assert result.critical_count == 1
    assert result.high_count == 2
    assert result.medium_count == 1
    assert result.low_count == 1


def test_distill_result_defaults() -> None:
    """默认构造的蒸馏结果应为空且无错误。"""
    result = DistillResult()
    assert result.conversations_analyzed == 0
    assert result.messages_analyzed == 0
    assert result.total_issues == 0
    assert result.critical_count == 0
    assert result.insights == []
    assert result.skill_suggestions == []
    assert result.error is None


def test_distill_result_with_skill_suggestions() -> None:
    """蒸馏结果可携带多条技能建议并分别保留动作类型。"""
    result = DistillResult(
        conversations_analyzed=5,
        messages_analyzed=30,
        analysis_period="最近 24 小时",
        summary="发现 2 个改进点",
        insights=[
            DistillInsight(
                severity=InsightSeverity.HIGH,
                category=InsightCategory.SKILL,
                summary="需要技能覆盖",
                frequency=3,
            ),
        ],
        skill_suggestions=[
            SkillSuggestion(
                skill_name="refund_qa",
                action="create",
                description="退款咨询技能",
                triggers=["退款", "退货"],
            ),
            SkillSuggestion(
                skill_name="greeting",
                action="update",
                description="更新问候语",
                triggers=["你好", "嗨"],
                prompt_injection="请用热情友好的语气回复",
            ),
        ],
    )
    result.calculate_stats()
    assert result.total_issues == 1
    assert result.high_count == 1
    assert len(result.skill_suggestions) == 2
    assert result.skill_suggestions[0].skill_name == "refund_qa"
    assert result.skill_suggestions[1].action == "update"


def test_insight_severity_enum() -> None:
    """洞察严重程度枚举的取值正确。"""
    assert InsightSeverity.CRITICAL.value == "critical"
    assert InsightSeverity.HIGH.value == "high"
    assert InsightSeverity.MEDIUM.value == "medium"
    assert InsightSeverity.LOW.value == "low"


def test_insight_category_enum() -> None:
    """洞察分类枚举包含全部预期取值。"""
    categories = [c.value for c in InsightCategory]
    assert "accuracy" in categories
    assert "completeness" in categories
    assert "clarity" in categories
    assert "efficiency" in categories
    assert "pattern" in categories
    assert "gap" in categories
    assert "skill" in categories
    assert "other" in categories


def test_distill_result_error_handling() -> None:
    """携带错误的蒸馏结果仍能安全执行统计计算。"""
    result = DistillResult(error="LLM 调用超时")
    result.calculate_stats()
    assert result.error == "LLM 调用超时"
    assert result.total_issues == 0
    assert result.conversations_analyzed == 0


def test_distill_result_empty_insights() -> None:
    """无洞察时统计结果应全为零。"""
    result = DistillResult(
        conversations_analyzed=20,
        messages_analyzed=100,
        summary="未发现显著改进点",
    )
    result.calculate_stats()
    assert result.total_issues == 0
    assert result.critical_count == 0
    assert result.high_count == 0


def test_scheduler_time_window() -> None:
    """调度器时间窗口判断正确（配置为 UTC 2–5 点，左闭右开）。"""
    # 窗口内
    assert _is_in_time_window(datetime(2026, 1, 1, 3, 0, 0, tzinfo=timezone.utc)) is True
    # 窗口外
    assert _is_in_time_window(datetime(2026, 1, 1, 8, 0, 0, tzinfo=timezone.utc)) is False
    # 下边界（含）
    assert _is_in_time_window(datetime(2026, 1, 1, 2, 0, 0, tzinfo=timezone.utc)) is True
    # 上边界（不含）
    assert _is_in_time_window(datetime(2026, 1, 1, 5, 0, 0, tzinfo=timezone.utc)) is False
