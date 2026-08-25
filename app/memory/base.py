"""记忆系统数据类型 — 无外部依赖，仅使用 Python 标准库。

定义记忆系统的全生命周期数据：
- ``MemoryConfig``：记忆配置（窗口大小、压缩阈值、压缩策略）
- ``MemorySnapshot``：记忆快照（压缩后的摘要 + 原始消息计数）
- ``CompressionStrategy``：压缩策略枚举
"""

from dataclasses import dataclass, field
from enum import Enum


class CompressionStrategy(str, Enum):
    """记忆压缩策略。

    - ``summary``：将旧消息压缩为一段摘要文本（保留关键信息，丢弃细节）
    - ``key_points``：提取关键要点列表（更结构化，信息密度更高）
    - ``none``：不压缩，直接丢弃旧消息（等同于纯滑动窗口）
    """

    SUMMARY = "summary"
    KEY_POINTS = "key_points"
    NONE = "none"


@dataclass
class MemoryConfig:
    """记忆系统配置。

    所有字段有默认值，调用方按需覆盖。
    """

    # 对话窗口大小：最多保留最近 N 轮对话（1 轮 = 用户消息 + 助手回复）
    window_size: int = 20

    # 压缩阈值：当历史消息超过此数量时触发压缩
    # 设为 0 表示始终压缩（即使窗口未满也压缩旧消息）
    compression_threshold: int = 30

    # 压缩策略
    strategy: CompressionStrategy = CompressionStrategy.SUMMARY

    # 压缩后保留的摘要最大长度（字符数）
    max_summary_chars: int = 2000

    # 是否在系统提示词中注入记忆上下文
    inject_memory: bool = True

    # 压缩时保留最近 N 轮不压缩（避免压缩太激进丢失近期上下文）
    keep_recent: int = 5


@dataclass
class MemorySnapshot:
    """记忆快照 — 压缩后的记忆状态。

    可序列化，用于持久化到数据库（Phase 4 内核打磨）。
    """

    # 压缩后的摘要文本
    summary: str = ""

    # 被压缩的原始消息数量
    compressed_count: int = 0

    # 压缩时的时间戳（ISO 格式）
    compressed_at: str = ""

    # 压缩策略
    strategy: CompressionStrategy = CompressionStrategy.SUMMARY

    @property
    def is_empty(self) -> bool:
        return not self.summary and self.compressed_count == 0


@dataclass
class ConversationMemory:
    """对话记忆 — 管理一次会话的记忆状态。

    Attributes:
        recent_messages: 窗口内的最近消息（未被压缩）
        snapshot: 压缩后的旧消息快照
        total_messages: 会话中所有消息的总数（含已压缩的）
    """

    recent_messages: list = field(default_factory=list)
    snapshot: MemorySnapshot = field(default_factory=MemorySnapshot)
    total_messages: int = 0

    @property
    def memory_context(self) -> str:
        """生成可注入到系统提示词中的记忆上下文文本。

        仅当 snapshot 非空时返回有意义的内容。
        """
        if self.snapshot.is_empty:
            return ""
        return (
            f"[历史对话摘要]（以下为较早对话的压缩摘要，共 {self.snapshot.compressed_count} 条消息）\n"
            f"{self.snapshot.summary}"
        )

    @property
    def is_compressed(self) -> bool:
        """是否已经触发过压缩。"""
        return not self.snapshot.is_empty