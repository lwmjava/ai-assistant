# 计划：调试与追踪（骨架阶段）

## 目标
引入 Agent 管线全链路执行追踪，满足 PRD §5.1.2 AGT-05 的调试模式要求。
开发者可查看完整管线执行 trace（阶段耗时、LLM 调用、工具调用、质量门评分）。

## 分支策略
- 基于 `agent-supervisor` 创建扁平分支 **`feat/debug-trace`**
- 遵循 Conventional Commits：`feat(debug): 新增 Agent 管线调试追踪骨架`

## 与 PRD 的对应关系

| 需求 | PRD 要求 | 实现状态 |
|------|----------|:--:|
| 调试模式 | 开发者可查看完整图执行 trace（P2，§5.1.2） | ✅ |
| 全局开关 | `agent.debug_mode` 控制 + tenant_admin 可开启（§5.1.4） | ✅ DEBUG_ENABLED |
| 事件记录 | 阶段进入/退出、LLM 调用、工具调用、质量门 | ✅ 5 种事件类型 |
| 性能分析 | 各阶段耗时统计 | ✅ 摘要含 LLM/工具总耗时 |

## 1. 架构设计

```
app/debug/
├── __init__.py          # 公开 API 导出
└── trace.py             # AgentTrace / TraceEvent / TraceCollector

app/agents/
└── pipeline.py          # 集成：AgentPipeline 注入 trace 钩子

app/services/
└── chat_service.py      # 集成：_create_trace() / _collect_trace()
```

### 1.1 核心数据结构

```
AgentTrace (一次管线执行的完整追踪)
├── run_id: str          # 12 位 hex 唯一 ID
├── debug_mode: bool     # 是否开启调试（关闭时跳过所有记录）
├── started_at / finished_at: float  # 时间戳（monotonic）
├── events: list[TraceEvent]  # 事件序列
├── error: str | None    # 管线异常信息
│
├── start() / finish()   # 标记执行起止
├── stage_start(name) / stage_end(name)  # 阶段事件
├── llm_call(model, prompt, response, latency_ms)  # LLM 调用
├── tool_call(tool_name, args, result, latency_ms) # 工具调用
├── quality_gate(score, threshold)  # 质量门评分
├── summary() → dict     # 精简摘要（供 SSE / 日志）
└── to_dict() → dict     # 完整序列化（供调试 API）

TraceEvent (单次追踪事件)
├── type: str            # stage_start / stage_end / llm_call / tool_call / quality_gate
├── name: str            # 阶段名 / 工具名 / 模型名
├── timestamp: float     # monotonic 时间戳
└── data: dict           # 附加数据（prompt/response/args/score 等）

TraceCollector (全局单例收集器)
├── add(trace)           # 添加 trace（超容量移除最旧）
├── get(run_id) → AgentTrace | None
├── list_recent(limit) → list[dict]  # 最近摘要列表
├── clear()              # 清空缓存
└── get_instance() / reset_instance()
```

### 1.2 管线集成点

| 集成点 | 记录内容 | 触发条件 |
|--------|----------|----------|
| `AgentPipeline.run()` | start / finish 起止 | trace 非空 |
| `AgentPipeline._stage()` | LLM 调用（模型名、prompt 预览、response 预览、延迟） | `trace.debug_mode` |
| `AgentPipeline.run()` | 理解/规划/检索/行动/反思/响应 阶段 start/end | trace 非空 |
| `AgentPipeline._quality_gate_loop()` | 质量门评分（score、threshold、passed） | trace 非空 |
| `ChatService._create_trace()` | 创建 Trace（仅 DEBUG_ENABLED 时） | settings.DEBUG_ENABLED |
| `ChatService._collect_trace()` | 收集 Trace 到全局缓存 | trace 非空 |

### 1.3 设计原则

- **零侵入**：trace 为可选参数，仅当 `debug_mode=True` 时才记录事件
- **安全**：prompt/response 仅保留前 500 字符预览，避免敏感信息泄漏
- **内存级**：骨架阶段仅内存缓存（最多 100 条），内核打磨阶段补充 DB 持久化

## 2. 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 时间基准 | `time.monotonic()` | 不受系统时钟调整影响，适合测量间隔 |
| 事件存储 | 内存 list（100 条上限） | 骨架阶段简化，内核打磨阶段补充 DB |
| 数据截断 | prompt/response 截断 500 字符 | 避免内存膨胀，调试模式下仍可定位问题 |
| run_id 格式 | uuid4 hex 前 12 位 | 短 ID 便于日志查看，碰撞概率极低 |
| 全局开关 | `DEBUG_ENABLED` 配置项 | 统一控制是否启用追踪，默认关闭 |

## 3. 配置项

```yaml
# 调试与追踪（Debug / Trace）
DEBUG_ENABLED: false          # 是否启用调试模式（全局开关）
DEBUG_TRACE_MAX_SIZE: 100     # 内存中保留最近 N 条 trace
```

## 4. 测试覆盖

| 测试 | 说明 |
|------|------|
| Test 1 | TraceEvent 创建与序列化 |
| Test 2 | AgentTrace 基础生命周期（start/finish/duration） |
| Test 3 | AgentTrace 阶段事件（stage_start/stage_end） |
| Test 4 | AgentTrace LLM 调用事件（含截断） |
| Test 5 | AgentTrace 工具调用 + 质量门事件 |
| Test 6 | AgentTrace 摘要（summary） |
| Test 7 | AgentTrace 完整序列化（to_dict） |
| Test 8 | debug_mode=False 时跳过所有事件记录 |
| Test 9 | TraceCollector 添加/获取/列表/清空 |
| Test 10 | TraceCollector 单例与容量限制 |

## 5. 后续计划（内核打磨阶段）

- Trace 持久化到数据库（按 run_id 查询历史）
- 调试 API 端点（GET /api/debug/traces, GET /api/debug/traces/{run_id}）
- 前端时间线可视化
- 性能分析报告（各阶段耗时统计、热点识别）
- run_stream() 流式模式的 trace 支持