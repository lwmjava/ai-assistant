"""记忆管理器 — 对话记忆系统的核心编排器。

负责：
1. 窗口管理：滑动窗口，限制传入管线的消息数量
2. 记忆压缩：超窗口触发 LLM 摘要压缩，保留关键信息
3. 上下文注入：将压缩后的记忆注入到系统提示词

骨架阶段仅支持内存级记忆管理；内核打磨阶段补充：
- 数据库持久化（跨会话记忆）
- 夜间蒸馏调度器（Evolution cron）
- 多级记忆（短期 / 中期 / 长期）
- 记忆检索（向量相似度匹配历史记忆）
"""

import logging

from app.llm.base import ChatMessage, LLMProvider
from app.memory.base import (
    CompressionStrategy,
    ConversationMemory,
    MemoryConfig,
    MemorySnapshot,
)
from app.memory.compressor import MemoryCompressor

logger = logging.getLogger(__name__)


class MemoryManager:
    """记忆管理器。

    使用方式::

        mgr = MemoryManager(llm_provider, config=MemoryConfig(window_size=20))
        memory = await mgr.manage(history_messages)

        # 注入到管线
        state = AgentState(user_input=message, history=memory.recent_messages)
        state.context = memory.memory_context  # 注入压缩记忆
    """

    def __init__(
        self,
        llm: LLMProvider | None = None,
        config: MemoryConfig | None = None,
    ) -> None:
        self._llm = llm
        self.config = config or MemoryConfig()
        self._compressor = MemoryCompressor(llm)

    # ── 核心入口 ──────────────────────────────────

    async def manage(
        self,
        messages: list[ChatMessage],
        *,
        existing_snapshot: MemorySnapshot | None = None,
    ) -> ConversationMemory:
        """管理对话记忆：窗口裁剪 + 必要时压缩。

        这是记忆系统的主要入口。调用方传入完整的历史消息列表，
        返回裁剪后的 ConversationMemory（含压缩快照）。

        Args:
            messages: 完整的历史消息列表（按时间升序）。
            existing_snapshot: 已有的记忆快照（续接之前的压缩）。

        Returns:
            ConversationMemory，包含窗口内消息 + 压缩快照。
        """
        total = len(messages)
        window = self.config.window_size
        threshold = self.config.compression_threshold

        # 情况 1：消息数未超窗口，无需压缩
        if total <= window:
            return ConversationMemory(
                recent_messages=list(messages),
                snapshot=existing_snapshot or MemorySnapshot(),
                total_messages=total,
            )

        # 情况 2：消息数超窗口但未达压缩阈值（或阈值=0不压缩）→ 简单滑动窗口
        if threshold <= 0 or total <= threshold:
            logger.debug(
                "窗口溢出（%d > %d），未达压缩阈值（%d），使用滑动窗口",
                total,
                window,
                threshold,
            )
            return ConversationMemory(
                recent_messages=list(messages[-window:]),
                snapshot=existing_snapshot or MemorySnapshot(),
                total_messages=total,
            )

        # 情况 3：触发压缩 — 保留最近 N 条，压缩旧消息
        keep_recent = min(self.config.keep_recent, window)
        recent = list(messages[-keep_recent:])
        old = list(messages[:-keep_recent])

        logger.info(
            "触发记忆压缩：%d 条旧消息 → LLM 摘要（保留最近 %d 条）",
            len(old),
            keep_recent,
        )

        snapshot = await self._compressor.compress(
            old,
            self.config,
            existing_snapshot=existing_snapshot,
        )

        return ConversationMemory(
            recent_messages=recent,
            snapshot=snapshot,
            total_messages=total,
        )

    # ── 便捷方法 ──────────────────────────────────

    def wrap_history(
        self,
        messages: list[ChatMessage],
        memory: ConversationMemory,
    ) -> list[ChatMessage]:
        """将记忆上下文包装为一条系统消息，插入历史最前面。

        用于在管线中注入压缩记忆：Agent 看到的第一条消息就是记忆摘要。

        Args:
            messages: 窗口内的最近消息。
            memory: 对话记忆（含压缩快照）。

        Returns:
            包含记忆上下文的完整消息列表。
        """
        if not self.config.inject_memory or memory.snapshot.is_empty:
            return list(messages)

        context = memory.memory_context
        if not context:
            return list(messages)

        # 在历史最前面插入一条系统消息作为记忆上下文
        from app.llm.base import ChatRole

        memory_msg = ChatMessage(role=ChatRole.SYSTEM, content=context)
        return [memory_msg] + list(messages)

    def get_memory_context(self, memory: ConversationMemory) -> str:
        """获取记忆上下文文本（用于注入 AgentState.context）。"""
        if not self.config.inject_memory:
            return ""
        return memory.memory_context

    # ── 状态查询 ──────────────────────────────────

    def should_compress(self, message_count: int) -> bool:
        """判断是否应该触发压缩。"""
        threshold = self.config.compression_threshold
        if threshold <= 0:
            return message_count > self.config.window_size
        return message_count > threshold

    def estimate_compression_ratio(self, memory: ConversationMemory) -> float:
        """估算压缩率（0.0 ~ 1.0）。

        0.0 = 未压缩，1.0 = 全部压缩。
        """
        if memory.total_messages == 0:
            return 0.0
        if memory.snapshot.is_empty:
            # 使用了滑动窗口（丢弃而非压缩）
            discarded = memory.total_messages - len(memory.recent_messages)
            return discarded / memory.total_messages if discarded > 0 else 0.0
        return memory.snapshot.compressed_count / memory.total_messages


# ── 全局单例（进程级缓存）──

_memory_manager: MemoryManager | None = None


def get_memory_manager(llm: LLMProvider | None = None) -> MemoryManager:
    """获取全局 MemoryManager 单例。

    首次调用时创建实例；后续调用返回同一实例。
    可通过参数注入 LLM 提供商（首次调用时生效）。
    """
    global _memory_manager
    if _memory_manager is None:
        from app.core.config import settings

        config = MemoryConfig(
            window_size=settings.MEMORY_WINDOW_SIZE,
            compression_threshold=settings.MEMORY_COMPRESSION_THRESHOLD,
            strategy=CompressionStrategy(settings.MEMORY_STRATEGY),
            max_summary_chars=settings.MEMORY_MAX_SUMMARY_CHARS,
            inject_memory=settings.MEMORY_ENABLED,
            keep_recent=settings.MEMORY_KEEP_RECENT,
        )
        _memory_manager = MemoryManager(llm=llm, config=config)
    return _memory_manager


def reset_memory_manager() -> None:
    """重置全局单例（测试用）。"""
    global _memory_manager
    _memory_manager = None