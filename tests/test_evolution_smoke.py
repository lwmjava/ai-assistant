"""进化系统（Reflect 反思）的冒烟测试。

覆盖数据类型构造、枚举取值，以及 Reflector 对各类 LLM 返回内容的解析行为
（合法 JSON、空改进项、Markdown 包裹、畸形 JSON、缺字段、非法枚举值）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.evolution import (
    ActionItem,
    ImprovementCategory,
    ImprovementPoint,
    ReflectResult,
    Reflector,
    Severity,
)
from app.llm.base import ChatMessage, LLMOptions, LLMProvider


class MockLLM(LLMProvider):
    """返回预设响应的 Mock LLM，用于隔离外部依赖。"""

    def __init__(self, response: str = "") -> None:
        self._response = response
        self._model = "mock"

    @property
    def model(self) -> str:
        return self._model

    async def chat(self, messages: list[ChatMessage], options: LLMOptions | None = None) -> str:
        return self._response

    async def stream_chat(self, messages: list[ChatMessage], options: LLMOptions | None = None):
        for char in self._response:
            yield char
        return


def test_data_types_instantiation() -> None:
    """改进点、待办事项与反思结果的数据类可正确构造并序列化。"""
    imp = ImprovementPoint(
        severity=Severity.HIGH,
        category=ImprovementCategory.ACCURACY,
        summary="事实错误",
        detail="回答了错误的数据",
        suggestion="建议核实数据来源",
    )
    assert imp.severity == Severity.HIGH
    assert imp.category == ImprovementCategory.ACCURACY
    assert imp.to_dict()["severity"] == "high"

    item = ActionItem(
        description="明天提交报告",
        priority="high",
        assignee_hint="用户",
        deadline_hint="明天",
    )
    assert item.description == "明天提交报告"
    assert item.to_dict()["priority"] == "high"

    result = ReflectResult(
        conversation_id="conv-1",
        summary="质量良好",
        improvements=[imp],
        action_items=[item],
        quality_score=0.85,
        revision_count=1,
    )
    assert result.has_improvements
    assert result.has_action_items
    assert result.critical_count == 0  # HIGH 不属于 CRITICAL
    assert len(result.to_dict()["improvements"]) == 1


def test_reflect_result_empty() -> None:
    """默认构造的反思结果应为空且可安全序列化。"""
    empty = ReflectResult()
    assert not empty.has_improvements
    assert not empty.has_action_items
    assert empty.critical_count == 0
    assert empty.to_dict()["improvements"] == []


def test_severity_and_category_enums() -> None:
    """严重程度与分类枚举的成员数量与取值正确。"""
    assert len(list(Severity)) == 4
    assert len(list(ImprovementCategory)) >= 7
    assert Severity.CRITICAL.value == "critical"
    assert ImprovementCategory.SKILL.value == "skill"


async def test_reflector_with_valid_json() -> None:
    """Reflector 能解析合法 JSON 并还原为结构化结果。"""
    valid_json = """{
  "summary": "对话质量良好，但有一处事实错误",
  "improvements": [
    {
      "severity": "high",
      "category": "accuracy",
      "summary": "事实错误",
      "detail": "回答了错误的数据",
      "suggestion": "核实数据来源"
    }
  ],
  "action_items": [
    {
      "description": "明天提交报告",
      "priority": "high",
      "assignee_hint": "用户",
      "deadline_hint": "明天"
    }
  ]
}"""
    reflector = Reflector(MockLLM(valid_json))
    result = await reflector.reflect(
        "用户：你好\n助手：你好",
        conversation_id="test-1",
        quality_score=0.85,
    )
    assert result.error is None
    assert result.summary != ""
    assert len(result.improvements) == 1
    assert result.improvements[0].severity == Severity.HIGH
    assert result.improvements[0].category == ImprovementCategory.ACCURACY
    assert len(result.action_items) == 1
    assert result.action_items[0].description == "明天提交报告"


async def test_reflector_with_empty_improvements() -> None:
    """改进项与待办事项均为空时，结果应标记为空而不报错。"""
    empty_json = """{
  "summary": "对话质量良好，无需改进",
  "improvements": [],
  "action_items": []
}"""
    reflector = Reflector(MockLLM(empty_json))
    result = await reflector.reflect("用户：你好\n助手：你好")
    assert result.error is None
    assert not result.has_improvements
    assert not result.has_action_items


async def test_reflector_with_markdown_wrapped_json() -> None:
    """Reflector 能剥离 Markdown 代码围栏后再解析 JSON。"""
    markdown_json = """```json
{
  "summary": "ok",
  "improvements": [],
  "action_items": []
}
```"""
    reflector = Reflector(MockLLM(markdown_json))
    result = await reflector.reflect("test")
    assert result.error is None
    assert result.summary == "ok"


async def test_reflector_with_malformed_json() -> None:
    """JSON 解析失败时应记录错误而非抛出异常。"""
    reflector = Reflector(MockLLM("not valid json at all"))
    result = await reflector.reflect("test")
    assert result.error is not None, "Should have parse error"
    assert not result.has_improvements


async def test_reflector_with_missing_optional_fields() -> None:
    """改进项缺少可选字段时回落到默认值。"""
    partial_json = """{
  "summary": "good",
  "improvements": [
    {"severity": "low", "category": "clarity", "summary": "minor issue"}
  ],
  "action_items": []
}"""
    reflector = Reflector(MockLLM(partial_json))
    result = await reflector.reflect("test")
    assert result.error is None
    assert len(result.improvements) == 1
    assert result.improvements[0].detail == ""  # 缺失字段回落默认值
    assert result.improvements[0].suggestion == ""  # 缺失字段回落默认值


async def test_reflector_with_invalid_enum_values() -> None:
    """非法枚举值应被跳过且不导致解析失败。"""
    invalid_json = """{
  "summary": "test",
  "improvements": [
    {"severity": "INVALID", "category": "INVALID", "summary": "bad data"}
  ],
  "action_items": []
}"""
    reflector = Reflector(MockLLM(invalid_json))
    result = await reflector.reflect("test")
    # 非法枚举值被跳过，整体解析不应失败
    assert result.error is None
