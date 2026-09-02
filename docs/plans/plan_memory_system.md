# 计划：记忆系统（骨架阶段）

## 目标
引入对话记忆系统，实现滑动窗口管理 + LLM 压缩，解决超长对话上下文爆炸问题，为 Phase 4 的 Reflect 反思与夜间蒸馏闭环奠定基础。

## 分支策略
- 基于 `agent-supervisor` 创建扁平分支 **`feat/memory-system`**
- 遵循 Conventional Commits：`feat(memory): 新增对话记忆系统骨架`

## 与 PRD 的对应关系

| PRD 要求 | 实现状态 |
|----------|:--:|
| 对话窗口管理（P1） | ✅ 滑动窗口 + 可配置窗口大小 |
| 记忆压缩（P1） | ✅ LLM 摘要压缩（summary / key_points / none） |
| 增量压缩 | ✅ 在已有摘要基础上追加更新 |
| 上下文注入 | ✅ 压缩记忆注入管线 system prompt |
| Reflect 反思（P1） | 🔮 Phase 4 内核打磨 |
| 夜间蒸馏（P1） | 🔮 Phase 4 内核打磨 |
| Action Items 提取（P1） | 🔮 Phase 4 内核打磨 |
| 长期记忆（P2） | 🔮 v1.5 |

## 1. 架构设计

```
app/memory/
├── __init__.py          # 公开 API 导出
├── base.py              # 数据类型（MemoryConfig / MemorySnapshot / ConversationMemory）
├── compressor.py        # MemoryCompressor：LLM 摘要压缩
└── manager.py           # MemoryManager：窗口管理 + 压缩编排 + 上下文注入

集成点：
  app/services/chat_service.py  ← _build_memory() → MemoryManager.manage()
  app/agents/pipeline.py        ← state.context 注入记忆上下文
  app/core/config.py            ← MEMORY_* 配置项
```

### 数据流

```
对话执行
  └─ ChatService._build_memory(conv)
       └─ MemoryManager.manage(all_messages)
            ├─ 消息数 <= 窗口 → 直接返回（无压缩）
            ├─ 消息数 > 窗口 但 <= 阈值 → 滑动窗口（丢弃旧消息）
            └─ 消息数 > 阈值 → 触发压缩
                 ├─ 保留最近 keep_recent 条
                 ├─ 旧消息 → MemoryCompressor.compress() → LLM 摘要
                 └─ 返回 ConversationMemory(recent + snapshot)

  └─ state.history = memory.recent_messages
  └─ state.context = memory.memory_context  ← 注入压缩记忆
```

## 2. 三种压缩策略

| 策略 | 行为 | 适用场景 |
|------|------|----------|
| `summary` | LLM 生成一段摘要文本 | 通用场景，信息密度适中 |
| `key_points` | LLM 提取关键要点列表（- 开头） | 需要结构化记忆的场景 |
| `none` | 不压缩，纯滑动窗口 | 简单场景，不需要记忆 |

## 3. 关键设计决策

| 决策 | 理由 |
|------|------|
| **三层判断逻辑** | 窗口内→直接返回 / 窗口外阈值内→滑动窗口 / 超阈值→LLM 压缩，避免不必要的 LLM 调用 |
| **threshold=0 禁用压缩** | 简单场景只需滑动窗口，不需要压缩 |
| **keep_recent 保留最近 N 条** | 压缩太激进会丢失近期上下文，保留最近 5 条确保连贯性 |
| **增量压缩** | 已有摘要作为前缀注入压缩提示词，避免重复压缩 |
| **压缩失败不阻塞** | LLM 压缩失败时回退空快照，不影响对话 |
| **全局单例 + 可注入 LLM** | MemoryManager 可在测试中注入 Mock LLM |

## 4. API 接口

### MemoryManager 公开方法

| 方法 | 签名 | 用途 |
|------|------|------|
| `manage()` | `(messages, existing_snapshot?) -> ConversationMemory` | 核心入口：窗口裁剪 + 压缩 |
| `wrap_history()` | `(messages, memory) -> list[ChatMessage]` | 包装记忆为系统消息 |
| `get_memory_context()` | `(memory) -> str` | 获取记忆上下文文本 |
| `should_compress()` | `(message_count) -> bool` | 判断是否应触发压缩 |
| `estimate_compression_ratio()` | `(memory) -> float` | 估算压缩率 |

### 配置项（app/core/config.py）

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `MEMORY_ENABLED` | `True` | 是否启用记忆系统 |
| `MEMORY_WINDOW_SIZE` | `20` | 窗口大小（最多保留最近 N 轮） |
| `MEMORY_COMPRESSION_THRESHOLD` | `30` | 压缩阈值（0=禁用压缩） |
| `MEMORY_STRATEGY` | `"summary"` | 压缩策略 |
| `MEMORY_MAX_SUMMARY_CHARS` | `2000` | 摘要最大字符数 |
| `MEMORY_KEEP_RECENT` | `5` | 压缩时保留最近 N 轮 |

## 5. 验证结果

```
Test 1: Window management (10 messages, window=5, threshold=0)  ✅
Test 2: Below window (3 messages, window=10)                     ✅
Test 3: Within threshold (25 messages, window=20, threshold=30)  ✅
Test 4: MemoryConfig defaults                                    ✅
Test 5: MemorySnapshot                                           ✅
Test 6: ConversationMemory (memory_context)                      ✅
Test 7: should_compress                                          ✅
Test 8: estimate_compression_ratio                               ✅
Test 9: wrap_history (system message injection)                  ✅
```

## 6. 文件清单

| 文件 | 操作 | 内容 |
|------|:--:|------|
| `app/memory/__init__.py` | 新增 | 公开 API 导出 |
| `app/memory/base.py` | 新增 | 数据类型（MemoryConfig/MemorySnapshot/ConversationMemory 等） |
| `app/memory/compressor.py` | 新增 | MemoryCompressor：LLM 摘要压缩 |
| `app/memory/manager.py` | 新增 | MemoryManager：窗口管理 + 编排 + 注入 |
| `app/services/chat_service.py` | 修改 | +_build_memory() + 记忆上下文注入 |
| `app/core/config.py` | 修改 | +MEMORY_* 配置项 |
| `tests/test_memory_smoke.py` | 新增 | 9 个冒烟测试 |

## 7. 骨架标记（SKELETON）— 内核打磨阶段待补充

| 标记 | 内容 | 优先级 |
|------|------|:--:|
| Reflect 异步反思 | 对话结束后自动审查 → 提取改进点 → 触发 Skill 更新 | P0 |
| 夜间蒸馏调度器 | Evolution cron 定时压缩长期记忆 | P0 |
| 数据库持久化 | MemorySnapshot 持久化到 DB，支持跨会话记忆 | P1 |
| 多级记忆 | 短期（窗口内）/ 中期（压缩摘要）/ 长期（跨会话） | P1 |
| 记忆检索 | 向量相似度匹配历史记忆，RAG 式检索相关上下文 | P2 |
| 规则压缩 | 基于消息长度/角色过滤的非 LLM 压缩 | P1 |
| 压缩质量评估 | 评估压缩后信息保留率，自动调整策略 | P2 |
| 记忆可视化 | 管理后台展示记忆压缩历史与效果 | P2 |

## 8. 后续演进

- **Phase 4 内核打磨**：Reflect 异步反思 → Skill 改进闭环 + 夜间蒸馏
- **Phase 5 管理后台**：记忆管理 UI + 压缩效果仪表板
- **v1.5**：长期记忆（跨会话用户偏好/事实提取）