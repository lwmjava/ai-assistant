"""Reflect 反思器 — 对话结束后异步审查，提取改进点与待办事项。

通过 LLM 对完整对话进行回顾性分析：
1. 提取改进点（按严重程度和分类标记）
2. 提取待办事项（用户隐含或明确的后续任务）
3. 评估整体对话质量

当前实现：LLM 驱动的反思。可按需扩展：
- 改进点 → Skill manifest 自动更新
- Action Item → Workflow 调度器写入
- 改进趋势追踪（时间序列分析）
- 跨会话模式识别（同类问题聚合）
"""

import json
import logging
import re

from app.evolution.models import (
    ActionItem,
    ImprovementCategory,
    ImprovementPoint,
    ReflectResult,
    Severity,
)
from app.llm.base import ChatMessage, ChatRole, LLMOptions, LLMProvider

logger = logging.getLogger(__name__)

# ── 反思提示词 ──

_REFLECT_SYSTEM_PROMPT = """你是一个 Agent 对话质量审查员。你的任务是回顾一轮完整的 Agent-用户对话，
从中提取改进建议和待办事项。

请按以下 JSON 格式输出（只输出 JSON，不要附加其他文字）：

{
  "summary": "整体反思摘要（1-2 句话，概括对话质量和主要发现）",
  "improvements": [
    {
      "severity": "critical|high|medium|low",
      "category": "accuracy|completeness|clarity|structure|safety|efficiency|skill|other",
      "summary": "一句话摘要",
      "detail": "详细描述问题所在",
      "suggestion": "具体改进建议"
    }
  ],
  "action_items": [
    {
      "description": "待办事项描述",
      "priority": "high|medium|low",
      "assignee_hint": "隐含的负责人（可为 null）",
      "deadline_hint": "隐含的截止时间（可为 null）"
    }
  ]
}

审查要点：
1. 事实准确性：回答中的事实是否可靠？有无幻觉？
2. 信息完整性：是否遗漏了用户关心的关键信息？
3. 逻辑一致性：推理步骤是否清晰、无矛盾？
4. 工具使用效率：是否合理使用了工具？有无不必要的调用？
5. 安全性：回答是否安全无害？有无越狱风险？
6. 用户满意度信号：从用户回复中推断满意度

注意：
- 如果对话质量良好，improvements 可以为空数组 []
- 如果用户没有隐含待办事项，action_items 可以为空数组 []
- 只提取真正有价值的改进点，不要为了填充而编造"""


class Reflector:
    """对话反思器。

    使用方式::

        reflector = Reflector(llm_provider)
        result = await reflector.reflect(
            conversation_text="用户：...\\n助手：...",
            quality_score=0.85,
            revision_count=1,
        )
        if result.has_improvements:
            for imp in result.improvements:
                print(f"[{imp.severity}] {imp.summary}")

    设计决策：
    - 异步非阻塞：反思在对话结束后异步执行，不增加用户等待时间
    - 结构化输出：JSON 格式，便于后续自动化处理（Skill 更新等）
    - 无副作用：当前仅返回分析结果，不修改任何系统状态
    """

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def reflect(
        self,
        conversation_text: str,
        *,
        conversation_id: str = "",
        quality_score: float = 0.0,
        revision_count: int = 0,
        options: LLMOptions | None = None,
    ) -> ReflectResult:
        """对一轮对话进行反思分析。

        Args:
            conversation_text: 完整对话文本（用户与助手交替，带角色标记）。
            conversation_id: 关联的会话 ID。
            quality_score: 管线 QualityGate 评分（若启用）。
            revision_count: 自纠错已执行轮数。
            options: LLM 调用选项（temperature 等）。

        Returns:
            ReflectResult：包含改进点列表和待办事项列表。
        """
        result = ReflectResult(
            conversation_id=conversation_id,
            quality_score=quality_score,
            revision_count=revision_count,
        )

        try:
            raw = await self._llm.chat(
                [
                    ChatMessage(role=ChatRole.SYSTEM, content=_REFLECT_SYSTEM_PROMPT),
                    ChatMessage(role=ChatRole.USER, content=conversation_text),
                ],
                options or LLMOptions(temperature=0.3, max_tokens=2048),
            )

            parsed = self._parse_json(raw)
            if parsed is None:
                result.error = "反思 JSON 解析失败"
                return result

            # 填充摘要
            result.summary = parsed.get("summary", "")

            # 解析改进点
            for imp_data in parsed.get("improvements", []):
                try:
                    imp = ImprovementPoint(
                        severity=Severity(imp_data.get("severity", "medium")),
                        category=ImprovementCategory(imp_data.get("category", "other")),
                        summary=imp_data.get("summary", ""),
                        detail=imp_data.get("detail", ""),
                        suggestion=imp_data.get("suggestion", ""),
                        affected_skill=imp_data.get("affected_skill"),
                    )
                    result.improvements.append(imp)
                except (ValueError, TypeError) as exc:
                    logger.debug("跳过无效的改进点: %s", exc)

            # 解析待办事项
            for item_data in parsed.get("action_items", []):
                try:
                    item = ActionItem(
                        description=item_data.get("description", ""),
                        priority=item_data.get("priority", "medium"),
                        assignee_hint=item_data.get("assignee_hint"),
                        deadline_hint=item_data.get("deadline_hint"),
                    )
                    if item.description.strip():
                        result.action_items.append(item)
                except (ValueError, TypeError) as exc:
                    logger.debug("跳过无效的待办事项: %s", exc)

            logger.info(
                "反思完成：conversation=%s, improvements=%d, action_items=%d, critical=%d",
                conversation_id,
                len(result.improvements),
                len(result.action_items),
                result.critical_count,
            )

        except Exception as exc:  # noqa: BLE001 — 反思失败不应影响对话流程
            logger.exception("反思过程异常")
            result.error = str(exc)

        return result

    # ── 内部工具 ──

    @staticmethod
    def _parse_json(raw: str) -> dict | None:
        """从 LLM 输出中提取 JSON 对象。

        处理 LLM 可能在 JSON 外包裹 markdown 代码块的情况。
        """
        if not raw:
            return None
        text = raw.strip()
        # 尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # 尝试提取 markdown 代码块中的 JSON
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass
        # 尝试提取首个 { ... } 块
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        logger.warning("无法解析反思 JSON 输出（前 200 字符）: %s", text[:200])
        return None