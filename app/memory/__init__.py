"""记忆系统模块。

提供对话记忆管理、窗口裁剪、LLM 压缩、上下文注入功能。

对外暴露：
- ``MemoryManager``：记忆管理器
- ``MemoryCompressor``：记忆压缩器
- ``MemoryConfig`` / ``ConversationMemory`` / ``MemorySnapshot``：数据类型
- ``CompressionStrategy``：压缩策略枚举
- ``get_memory_manager``：全局单例
"""

from app.memory.base import (
    CompressionStrategy,
    ConversationMemory,
    MemoryConfig,
    MemorySnapshot,
)
from app.memory.compressor import MemoryCompressor
from app.memory.manager import MemoryManager, get_memory_manager, reset_memory_manager

__all__ = [
    "MemoryManager",
    "MemoryCompressor",
    "MemoryConfig",
    "ConversationMemory",
    "MemorySnapshot",
    "CompressionStrategy",
    "get_memory_manager",
    "reset_memory_manager",
]