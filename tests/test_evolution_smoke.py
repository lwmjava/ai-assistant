"""Smoke test for evolution (Reflect) system."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio

from app.evolution import (
    ActionItem,
    ImprovementCategory,
    ImprovementPoint,
    ReflectResult,
    Reflector,
    Severity,
)
from app.llm.base import ChatMessage, ChatRole, LLMOptions, LLMProvider


# ── Mock LLM that returns controlled JSON ──

class MockLLM(LLMProvider):
    """Mock LLM that returns pre-defined responses."""

    def __init__(self, response: str = ""):
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


# ── Test 1: Data types ──
print("Test 1: Data types instantiation")
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
assert result.critical_count == 0  # HIGH is not CRITICAL
assert len(result.to_dict()["improvements"]) == 1
print("  OK")


# ── Test 2: ReflectResult empty ──
print("Test 2: ReflectResult empty")
empty = ReflectResult()
assert not empty.has_improvements
assert not empty.has_action_items
assert empty.critical_count == 0
assert empty.to_dict()["improvements"] == []
print("  OK")


# ── Test 3: Severity and Category enums ──
print("Test 3: Severity and Category enums")
assert len(list(Severity)) == 4
assert len(list(ImprovementCategory)) >= 7
assert Severity.CRITICAL.value == "critical"
assert ImprovementCategory.SKILL.value == "skill"
print("  OK")


# ── Test 4: Reflector with valid JSON ──
print("Test 4: Reflector with valid JSON response")
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
result = asyncio.run(reflector.reflect(
    "用户：你好\n助手：你好",
    conversation_id="test-1",
    quality_score=0.85,
))
assert result.error is None
assert result.summary != ""
assert len(result.improvements) == 1
assert result.improvements[0].severity == Severity.HIGH
assert result.improvements[0].category == ImprovementCategory.ACCURACY
assert len(result.action_items) == 1
assert result.action_items[0].description == "明天提交报告"
print("  OK")


# ── Test 5: Reflector with empty improvements ──
print("Test 5: Reflector with empty improvements")
empty_json = """{
  "summary": "对话质量良好，无需改进",
  "improvements": [],
  "action_items": []
}"""
reflector = Reflector(MockLLM(empty_json))
result = asyncio.run(reflector.reflect("用户：你好\n助手：你好"))
assert result.error is None
assert not result.has_improvements
assert not result.has_action_items
print("  OK")


# ── Test 6: Reflector with markdown-wrapped JSON ──
print("Test 6: Reflector with markdown-wrapped JSON")
markdown_json = """```json
{
  "summary": "ok",
  "improvements": [],
  "action_items": []
}
```"""
reflector = Reflector(MockLLM(markdown_json))
result = asyncio.run(reflector.reflect("test"))
assert result.error is None
assert result.summary == "ok"
print("  OK")


# ── Test 7: Reflector with malformed JSON ──
print("Test 7: Reflector with malformed JSON (should survive)")
reflector = Reflector(MockLLM("not valid json at all"))
result = asyncio.run(reflector.reflect("test"))
assert result.error is not None, "Should have parse error"
assert not result.has_improvements
print("  OK")


# ── Test 8: Reflector with missing optional fields ──
print("Test 8: Reflector with missing optional fields")
partial_json = """{
  "summary": "good",
  "improvements": [
    {"severity": "low", "category": "clarity", "summary": "minor issue"}
  ],
  "action_items": []
}"""
reflector = Reflector(MockLLM(partial_json))
result = asyncio.run(reflector.reflect("test"))
assert result.error is None
assert len(result.improvements) == 1
assert result.improvements[0].detail == ""  # missing field → default
assert result.improvements[0].suggestion == ""  # missing field → default
print("  OK")


# ── Test 9: Reflector with invalid severity/category ──
print("Test 9: Reflector with invalid severity/category (should skip)")
invalid_json = """{
  "summary": "test",
  "improvements": [
    {"severity": "INVALID", "category": "INVALID", "summary": "bad data"}
  ],
  "action_items": []
}"""
reflector = Reflector(MockLLM(invalid_json))
result = asyncio.run(reflector.reflect("test"))
# Should survive without crashing - invalid enums are skipped
assert result.error is None
print("  OK")


print("\n=== All 9 tests passed ===")