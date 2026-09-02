"""Smoke test for memory system."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm.base import ChatMessage, ChatRole
from app.memory import (
    MemoryManager,
    MemoryConfig,
    CompressionStrategy,
    ConversationMemory,
    MemorySnapshot,
    get_memory_manager,
    reset_memory_manager,
)

# Helper: create test messages
def msg(role: str, content: str) -> ChatMessage:
    return ChatMessage(role=ChatRole(role), content=content)

# Test 1: Window management (no compression, threshold=0 disables compression)
print("Test 1: Window management (10 messages, window=5, threshold=0)")
mgr = MemoryManager(config=MemoryConfig(window_size=5, compression_threshold=0))
messages = [msg("user", f"message {i}") for i in range(10)]
import asyncio
memory = asyncio.run(mgr.manage(messages))
print(f"  recent={len(memory.recent_messages)}, total={memory.total_messages}, compressed={memory.is_compressed}")
assert len(memory.recent_messages) == 5, f"Expected 5, got {len(memory.recent_messages)}"
assert memory.total_messages == 10
assert not memory.is_compressed  # threshold=0 means no compression, just sliding window

# Test 2: Below window (no compression)
print("\nTest 2: Below window (3 messages, window=10)")
mgr2 = MemoryManager(config=MemoryConfig(window_size=10))
messages2 = [msg("user", f"msg {i}") for i in range(3)]
memory2 = asyncio.run(mgr2.manage(messages2))
print(f"  recent={len(memory2.recent_messages)}, total={memory2.total_messages}")
assert len(memory2.recent_messages) == 3
assert not memory2.is_compressed

# Test 3: Within threshold (sliding window only)
print("\nTest 3: Within threshold (25 messages, window=20, threshold=30)")
mgr3 = MemoryManager(config=MemoryConfig(window_size=20, compression_threshold=30))
messages3 = [msg("user", f"msg {i}") for i in range(25)]
memory3 = asyncio.run(mgr3.manage(messages3))
print(f"  recent={len(memory3.recent_messages)}, total={memory3.total_messages}")
assert len(memory3.recent_messages) == 20  # window size
assert not memory3.is_compressed

# Test 4: MemoryConfig defaults
print("\nTest 4: MemoryConfig defaults")
cfg = MemoryConfig()
assert cfg.window_size == 20
assert cfg.compression_threshold == 30
assert cfg.strategy == CompressionStrategy.SUMMARY
assert cfg.max_summary_chars == 2000
assert cfg.inject_memory is True
assert cfg.keep_recent == 5
print("  OK")

# Test 5: MemorySnapshot
print("\nTest 5: MemorySnapshot")
snap = MemorySnapshot(summary="test summary", compressed_count=10)
assert not snap.is_empty
assert snap.compressed_count == 10
snap2 = MemorySnapshot()
assert snap2.is_empty
print("  OK")

# Test 6: ConversationMemory
print("\nTest 6: ConversationMemory")
mem = ConversationMemory(
    recent_messages=[],
    snapshot=MemorySnapshot(summary="previous conversation about Python", compressed_count=5),
    total_messages=25,
)
assert mem.is_compressed
ctx = mem.memory_context
assert "Python" in ctx
assert "5" in ctx  # compressed_count
print(f"  memory_context: {ctx[:60]}...")

# Test 7: should_compress
print("\nTest 7: should_compress")
mgr7 = MemoryManager(config=MemoryConfig(window_size=10, compression_threshold=20))
assert not mgr7.should_compress(5)
assert not mgr7.should_compress(15)
assert mgr7.should_compress(25)
print("  OK")

# Test 8: estimate_compression_ratio
print("\nTest 8: estimate_compression_ratio")
mem8 = ConversationMemory(
    recent_messages=[msg("user", "hi")],
    snapshot=MemorySnapshot(summary="sum", compressed_count=10),
    total_messages=11,
)
ratio = mgr7.estimate_compression_ratio(mem8)
print(f"  ratio={ratio:.2f}")
assert ratio > 0.9

# Test 9: wrap_history
print("\nTest 9: wrap_history")
recent = [msg("user", "hello"), msg("assistant", "hi there")]
wrapped = mgr7.wrap_history(recent, mem8)
print(f"  wrapped messages: {len(wrapped)}")
assert len(wrapped) == 3  # memory context + 2 recent
assert wrapped[0].role == ChatRole.SYSTEM
assert "sum" in wrapped[0].content

print("\nAll tests passed!")