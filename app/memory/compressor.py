"""记忆压缩器 — 将旧消息压缩为摘要文本。

通过 LLM 对历史对话进行摘要压缩，保留关键信息，丢弃细节。
骨架阶段仅支持 LLM 压缩；内核打磨阶段补充：
- 规则压缩（基于消息长度/角色过滤）
- 增量压缩（在上次摘要基础上追加）
- 多级压缩（短期/中期/长期记忆）
"""

import logging
from datetime import UTC, datetime

from app.llm.base import ChatMessage, ChatRole, LLMOptions, LLMProvider
from app.memory.base import CompressionStrategy, MemoryConfig, MemorySnapshot

logger = logging.getLogger(__name__)

# 压缩提示词模板
_COMPRESS_SUMMARY_PROMPT = """你是一个对话摘要压缩器。请将以下历史对话压缩为一段简洁的摘要。

要求：
1. 保留关键信息：用户的核心需求、重要决策、关键事实
2. 保留上下文线索：话题的演进脉络、未解决的问题
3. 丢弃冗余信息：寒暄、重复表述、已解决的细节
4. 摘要长度控制在 {max_chars} 字符以内
5. 使用中文输出（除非对话本身是英文）

历史对话：
{history_text}

请输出摘要："""

_COMPRESS_KEY_POINTS_PROMPT = """你是一个对话要点提取器。请从以下历史对话中提取关键要点。

要求：
1. 每条要点以 "- " 开头
2. 按重要性排序（最重要在前）
3. 包含：用户偏好、决策、待办事项、关键事实
4. 总共不超过 10 条要点
5. 使用中文输出（除非对话本身是英文）

历史对话：
{history_text}

请输出关键要点列表："""


class MemoryCompressor:
    """记忆压缩器 — 调用 LLM 将对话历史压缩为摘要。

    使用方式::

        compressor = MemoryCompressor(llm_provider)
        snapshot = await compressor.compress(messages, config)
    """

    def __init__(self, llm: LLMProvider | None = None) -> None:
        self._llm = llm

    @property
    def llm(self) -> LLMProvider | None:
        return self._llm

    async def compress(
        self,
        messages: list[ChatMessage],
        config: MemoryConfig | None = None,
        *,
        existing_snapshot: MemorySnapshot | None = None,
    ) -> MemorySnapshot:
        """压缩一组消息为记忆快照。

        Args:
            messages: 待压缩的消息列表。
            config: 记忆配置（默认使用 MemoryConfig()）。
            existing_snapshot: 已有的记忆快照（增量压缩时合并）。

        Returns:
            压缩后的 MemorySnapshot。
        """
        cfg = config or MemoryConfig()

        if cfg.strategy == CompressionStrategy.NONE:
            return MemorySnapshot(
                compressed_count=len(messages),
                compressed_at=_now_iso(),
                strategy=cfg.strategy,
            )

        if not messages:
            return MemorySnapshot()

        if self._llm is None:
            logger.warning("MemoryCompressor 未配置 LLM，跳过压缩")
            return MemorySnapshot(
                compressed_count=len(messages),
                compressed_at=_now_iso(),
                strategy=cfg.strategy,
            )

        # 构建历史文本
        history_text = _messages_to_text(messages)

        # 选择提示词模板
        if cfg.strategy == CompressionStrategy.KEY_POINTS:
            prompt = _COMPRESS_KEY_POINTS_PROMPT.format(history_text=history_text)
        else:
            prompt = _COMPRESS_SUMMARY_PROMPT.format(
                max_chars=cfg.max_summary_chars,
                history_text=history_text,
            )

        # 增量压缩：将已有摘要作为前缀
        if existing_snapshot and not existing_snapshot.is_empty:
            prompt = (
                f"[已有的对话摘要]\n{existing_snapshot.summary}\n\n"
                f"---\n以下是新的对话内容，请在上次摘要的基础上补充更新：\n\n"
                f"{prompt}"
            )

        try:
            summary = await self._llm.chat(
                [
                    ChatMessage(role=ChatRole.SYSTEM, content="你是一个专业的对话摘要压缩器。"),
                    ChatMessage(role=ChatRole.USER, content=prompt),
                ],
                LLMOptions(temperature=0.3, max_tokens=min(cfg.max_summary_chars // 2, 2048)),
            )
            summary = summary.strip()

            # 截断过长摘要
            if len(summary) > cfg.max_summary_chars:
                summary = summary[:cfg.max_summary_chars] + "..."

            total_count = len(messages)
            if existing_snapshot:
                total_count += existing_snapshot.compressed_count

            logger.info(
                "记忆压缩完成：%d 条消息 → %d 字符摘要",
                len(messages),
                len(summary),
            )
            return MemorySnapshot(
                summary=summary,
                compressed_count=total_count,
                compressed_at=_now_iso(),
                strategy=cfg.strategy,
            )
        except Exception:  # noqa: BLE001 — 压缩失败不应阻塞对话
            logger.exception("记忆压缩失败")
            return MemorySnapshot(
                compressed_count=len(messages),
                compressed_at=_now_iso(),
                strategy=cfg.strategy,
            )


def _messages_to_text(messages: list[ChatMessage]) -> str:
    """将消息列表转换为可读文本。"""
    role_map = {
        ChatRole.USER.value: "用户",
        ChatRole.ASSISTANT.value: "助手",
        ChatRole.SYSTEM.value: "系统",
    }
    lines: list[str] = []
    for m in messages:
        role = role_map.get(m.role.value, m.role.value)
        # 截断过长消息（避免压缩时 token 爆炸）
        content = m.content[:2000] if len(m.content) > 2000 else m.content
        lines.append(f"[{role}] {content}")
    return "\n\n".join(lines)


def _now_iso() -> str:
    """返回当前 UTC 时间的 ISO 格式字符串。"""
    return datetime.now(UTC).isoformat()