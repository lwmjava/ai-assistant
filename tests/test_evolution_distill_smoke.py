"""Smoke test for evolution/distill module."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.evolution.models import (
    DistillInsight,
    DistillResult,
    InsightCategory,
    InsightSeverity,
    SkillSuggestion,
)


# ── Test 1: DistillInsight ──
print("Test 1: DistillInsight")
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
print("  OK")


# ── Test 2: SkillSuggestion ──
print("Test 2: SkillSuggestion")
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
print("  OK")


# ── Test 3: DistillResult calculate_stats ──
print("Test 3: DistillResult calculate_stats")
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
print("  OK")


# ── Test 4: DistillResult defaults ──
print("Test 4: DistillResult defaults")
result = DistillResult()
assert result.conversations_analyzed == 0
assert result.messages_analyzed == 0
assert result.total_issues == 0
assert result.critical_count == 0
assert result.insights == []
assert result.skill_suggestions == []
assert result.error is None
print("  OK")


# ── Test 5: DistillResult with skill suggestions ──
print("Test 5: DistillResult with skill suggestions")
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
print("  OK")


# ── Test 6: InsightSeverity enum ──
print("Test 6: InsightSeverity enum")
assert InsightSeverity.CRITICAL.value == "critical"
assert InsightSeverity.HIGH.value == "high"
assert InsightSeverity.MEDIUM.value == "medium"
assert InsightSeverity.LOW.value == "low"
print("  OK")


# ── Test 7: InsightCategory enum ──
print("Test 7: InsightCategory enum")
categories = [c.value for c in InsightCategory]
assert "accuracy" in categories
assert "completeness" in categories
assert "clarity" in categories
assert "efficiency" in categories
assert "pattern" in categories
assert "gap" in categories
assert "skill" in categories
assert "other" in categories
print("  OK")


# ── Test 8: DistillResult error handling ──
print("Test 8: DistillResult error handling")
result = DistillResult(error="LLM 调用超时")
result.calculate_stats()
assert result.error == "LLM 调用超时"
assert result.total_issues == 0
assert result.conversations_analyzed == 0
print("  OK")


# ── Test 9: DistillResult empty insights ──
print("Test 9: DistillResult empty insights")
result = DistillResult(
    conversations_analyzed=20,
    messages_analyzed=100,
    summary="未发现显著改进点",
)
result.calculate_stats()
assert result.total_issues == 0
assert result.critical_count == 0
assert result.high_count == 0
print("  OK")


# ── Test 10: Scheduler time window check ──
print("Test 10: Scheduler _is_in_time_window")
from datetime import datetime, timezone
from app.evolution.scheduler import _is_in_time_window

# Mock settings values are 2-5 (UTC)
# Hour 3 should be in window
dt_in = datetime(2026, 1, 1, 3, 0, 0, tzinfo=timezone.utc)
assert _is_in_time_window(dt_in) is True

# Hour 8 should be outside window
dt_out = datetime(2026, 1, 1, 8, 0, 0, tzinfo=timezone.utc)
assert _is_in_time_window(dt_out) is False

# Hour 2 (boundary) should be in window
dt_boundary = datetime(2026, 1, 1, 2, 0, 0, tzinfo=timezone.utc)
assert _is_in_time_window(dt_boundary) is True

# Hour 5 (exclusive end) should be outside
dt_end = datetime(2026, 1, 1, 5, 0, 0, tzinfo=timezone.utc)
assert _is_in_time_window(dt_end) is False
print("  OK")


print("\n=== All 10 tests passed ===")