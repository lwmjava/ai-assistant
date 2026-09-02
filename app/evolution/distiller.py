"""夜间蒸馏器 — 批量分析近期对话，提炼改进建议。

通过 LLM 对一批近期对话进行批量分析，提炼：
1. 共性改进洞察（按严重度与分类标记）
2. 技能改善建议（新技能创建 / 现有技能修改）
3. 知识库或能力缺口

当前实现：LLM 驱动的批量分析，结果以日志输出。
可按需扩展：自动 Skill 更新、DB 持久化、趋势分析。
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select, func

from app.core.database import engine
from app.evolution.models import (
    DistillInsight,
    DistillResult,
    InsightCategory,
    InsightSeverity,
    SkillSuggestion,
)
from app.llm.base import ChatMessage, ChatRole, LLMOptions, LLMProvider
from app.llm.factory import get_llm_provider
from app.models.conversation import Conversation, Message

logger = logging.getLogger(__name__)

# ── 蒸馏提示词 ──

_DISTILL_SYSTEM_PROMPT = """你是一个 AI 助手质量分析专家。你的任务是对一批近期对话进行批量分析，
从中提炼共性改进建议和技能改善方向。

请按以下 JSON 格式输出（只输出 JSON，不要附加其他文字）：

{
  "summary": "整体分析摘要（2-3 句话，概括这批对话的整体质量和主要发现）",
  "insights": [
    {
      "severity": "critical|high|medium|low",
      "category": "accuracy|completeness|clarity|efficiency|pattern|gap|skill|other",
      "summary": "一句话摘要",
      "detail": "详细描述问题或模式",
      "suggestion": "具体改进建议",
      "frequency": 数字
    }
  ],
  "skill_suggestions": [
    {
      "skill_name": "建议的技能名称",
      "action": "create|update|delete",
      "description": "技能描述",
      "triggers": ["触发关键词1", "触发关键词2"],
      "prompt_injection": "建议注入的提示词内容"
    }
  ]
}

分析要点：
1. 共性模式：哪些问题反复出现？哪些回答模式不够好？
2. 事实准确性：有无幻觉或错误信息？
3. 信息完整性：是否遗漏了关键信息？
4. 工具使用：是否合理使用了工具？有无不必要的调用？
5. 用户满意度信号：从对话中推断用户是否满意
6. 能力缺口：哪些问题类型目前的回答质量较差？

注意：
- insights 只提取真正有价值的改进点，不要为了填充而编造
- skill_suggestions 只建议值得创建/修改的技能，无建议时可为空数组
- frequency 应该是同类问题在分析范围内出现的次数估算"""


class Distiller:
    """夜间蒸馏器。

    使用方式::

        distiller = Distiller()
        result = await distiller.distill_recent(hours=24, max_conversations=50)
        for insight in result.insights:
            print(f"[{insight.severity}] {insight.summary}")

    设计决策：
    - 异步非阻塞：蒸馏在后台定时执行，不占用用户请求路径
    - 批量分析：一次 LLM 调用分析一批对话，节省 Token 成本
    - 结构化输出：JSON 格式，便于后续自动化处理
    """

    def __init__(self, llm: LLMProvider | None = None) -> None:
        self._llm = llm

    @property
    def llm(self) -> LLMProvider:
        if self._llm is None:
            self._llm = get_llm_provider()
        return self._llm

    async def distill_recent(
        self,
        *,
        hours: int = 24,
        max_conversations: int = 50,
        options: LLMOptions | None = None,
    ) -> DistillResult:
        """对最近 N 小时的对话进行批量蒸馏分析。

        Args:
            hours: 分析最近多少小时的对话。
            max_conversations: 最多分析的会话数。
            options: LLM 调用选项。

        Returns:
            DistillResult：包含洞察列表和技能建议。
        """
        result = DistillResult()

        # 1. 获取近期对话
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        conversations = self._fetch_recent_conversations(cutoff, max_conversations)

        if not conversations:
            result.summary = "没有足够的数据进行蒸馏分析。"
            result.analysis_period = f"最近 {hours} 小时"
            return result

        result.analysis_period = f"最近 {hours} 小时"
        result.conversations_analyzed = len(conversations)
        result.messages_analyzed = sum(
            len(getattr(c, "messages", []) or []) for c in conversations
        )

        # 2. 构建分析文本
        analysis_text = self._build_analysis_text(conversations)

        if not analysis_text.strip():
            result.summary = "对话内容为空，无法分析。"
            return result

        # 3. LLM 批量分析
        try:
            raw = await self.llm.chat(
                [
                    ChatMessage(role=ChatRole.SYSTEM, content=_DISTILL_SYSTEM_PROMPT),
                    ChatMessage(role=ChatRole.USER, content=analysis_text),
                ],
                options or LLMOptions(temperature=0.3, max_tokens=4096),
            )

            parsed = self._parse_json(raw)
            if parsed is None:
                result.error = "蒸馏 JSON 解析失败"
                return result

            # 填充摘要
            result.summary = parsed.get("summary", "")

            # 解析洞察
            for i, imp_data in enumerate(parsed.get("insights", [])):
                try:
                    insight = DistillInsight(
                        severity=InsightSeverity(imp_data.get("severity", "medium")),
                        category=InsightCategory(imp_data.get("category", "other")),
                        summary=imp_data.get("summary", ""),
                        detail=imp_data.get("detail", ""),
                        suggestion=imp_data.get("suggestion", ""),
                        frequency=imp_data.get("frequency", 0),
                    )
                    result.insights.append(insight)
                except (ValueError, TypeError) as exc:
                    logger.debug("跳过无效的蒸馏洞察: %s", exc)

            # 解析技能建议
            for i, skill_data in enumerate(parsed.get("skill_suggestions", [])):
                try:
                    skill = SkillSuggestion(
                        skill_name=skill_data.get("skill_name", ""),
                        action=skill_data.get("action", "create"),
                        description=skill_data.get("description", ""),
                        triggers=skill_data.get("triggers", []),
                        prompt_injection=skill_data.get("prompt_injection", ""),
                        insight_index=i,
                    )
                    if skill.skill_name.strip():
                        result.skill_suggestions.append(skill)
                except (ValueError, TypeError) as exc:
                    logger.debug("跳过无效的技能建议: %s", exc)

            result.calculate_stats()

            logger.info(
                "蒸馏完成：对话=%d, 消息=%d, 洞察=%d (critical=%d, high=%d), 技能建议=%d",
                result.conversations_analyzed,
                result.messages_analyzed,
                result.total_issues,
                result.critical_count,
                result.high_count,
                len(result.skill_suggestions),
            )

        except Exception as exc:  # noqa: BLE001 — 蒸馏失败不应影响服务
            logger.exception("蒸馏过程异常")
            result.error = str(exc)

        return result

    # ── 数据获取 ──

    @staticmethod
    def _fetch_recent_conversations(
        cutoff: datetime, max_count: int
    ) -> list[Conversation]:
        """从数据库获取最近的、有实际对话内容的会话。"""
        try:
            with Session(engine) as session:
                # 获取最近更新的会话
                stmt = (
                    select(Conversation)
                    .where(Conversation.updated_at >= cutoff)
                    .order_by(Conversation.updated_at.desc())
                    .limit(max_count)
                )
                conversations = list(session.exec(stmt).all())

                # 预加载消息（SQLModel relationship 默认懒加载）
                for conv in conversations:
                    _ = conv.messages  # 触发加载

                # 过滤掉没有消息的会话
                conversations = [
                    c for c in conversations if len(c.messages) >= 2
                ]

                return conversations
        except Exception as exc:  # noqa: BLE001
            logger.exception("获取近期对话失败")
            return []

    # ── 文本构建 ──

    @staticmethod
    def _build_analysis_text(conversations: list[Conversation]) -> str:
        """将批量的对话序列化为分析文本。

        每段对话会截断过长内容，避免超出 LLM 上下文窗口。
        """
        lines: list[str] = []
        lines.append(f"# 批量对话分析（共 {len(conversations)} 个会话）\n")

        role_map = {"user": "用户", "assistant": "助手", "system": "系统"}

        for i, conv in enumerate(conversations, 1):
            lines.append(f"## 会话 {i}（{conv.id[:8]}...）")
            conv_lines: list[str] = []
            total_chars = 0
            max_chars_per_conv = 2000  # 每个会话最多 2000 字符

            for m in sorted(conv.messages, key=lambda x: x.created_at):
                role_label = role_map.get(m.role, m.role)
                line = f"{role_label}：{m.content}"
                if total_chars + len(line) > max_chars_per_conv:
                    conv_lines.append("...（后续内容已截断）")
                    break
                conv_lines.append(line)
                total_chars += len(line)

            lines.extend(conv_lines)
            lines.append("")  # 空行分隔

        return "\n".join(lines)

    # ── JSON 解析 ──

    @staticmethod
    def _parse_json(raw: str) -> dict | None:
        """从 LLM 输出中提取 JSON 对象。"""
        if not raw:
            return None
        text = raw.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        logger.warning("无法解析蒸馏 JSON 输出（前 200 字符）: %s", text[:200])
        return None