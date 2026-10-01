# 实现说明：超窗压缩回归

> 日期：2026-10-01
> 计划：`docs/plans/plan_d1.md` 的 MEM-001 节
> 结果：默认 20 / 30 / 5 的两段行为已有回归。压缩算法未改。

## 完成的行为

直接调用 `MemoryManager.manage()`，配置用 `MemoryConfig()` 的默认值。消息正文是 `body-01` 到 `body-N`，按条计数。

1. 总条数 25，窗口 20，阈值 30，保留 5。未达阈值，不压缩。`recent_messages` 按顺序是 `body-06` 到 `body-25`。
2. 总条数 31，同一组默认值。假 LLM 的 `chat` 返回固定摘要「固定摘要：较早的对话已压缩。」`is_compressed` 为真，摘要等于该字符串，`compressed_count` 为 26。`recent_messages` 按顺序是 `body-27` 到 `body-31`。`body-01` 到 `body-26` 出现在压缩用的用户提示里，且不在最近消息里。`body-27` 到 `body-31` 不在该提示里。

## 代码位置

- 回归用例：`tests/test_memory_overflow.py`
- 被锁住的实现：`app/memory/manager.py` 的 `manage()`，本批没有改这个文件

## 验证

解释器：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`（conda 环境 `ai-assistant`，Python 3.12.0）。文档中的 `D:\install\anaconda3\envs\ai-assistant\Scripts` 在本机不存在。

`ruff check tests/test_memory_overflow.py`：通过，退出码 0。

验收命令原样执行了两次，都是退出码 1。两次里超窗用例都通过。失败发生在 `tests/test_rag.py::test_retriever_context_is_tenant_current_only` 的准备阶段：删除 `data/pytest-tmp/run` 时 `PermissionError: [WinError 5]`。该目录当时被占用，空目录也无法改名。这不是压缩断言失败。

同一筛选改用空闲临时目录后通过：

```text
python -m pytest tests/ -k "memory or context" -v --tb=line --basetemp data/pytest-tmp/mem001
13 passed, 2 skipped, 442 deselected
exit 0
```

其中 `test_retriever_context_is_tenant_current_only` 为通过。关键字 `context` 会带上这条检索用例；本批没有改它。

## 没做的事

没有改 `app/memory/`。没有把 `ChatService` 改成读取记忆配置。没有做长期记忆、跨会话记忆或平滑过渡。没有改 `tests/test_context_merge.py` 的预算断言。默认临时目录被占用时，不带 `--basetemp` 的验收命令仍会在准备阶段退出码 1。
