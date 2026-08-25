# 计划：夜间蒸馏调度器（骨架阶段）

## 目标
引入定时批量对话分析能力（夜间蒸馏），满足 PRD §5.4 "记忆蒸馏" 与 "Cron 定时引擎" 的进化系统要求。
在低峰时段自动分析近期对话，提炼共性改进建议与技能改善方向。

## 分支策略
- 基于 `agent-supervisor` 创建扁平分支 **`feat/evolution-distill`**
- 遵循 Conventional Commits：`feat(evolution): 新增夜间蒸馏调度器骨架`

## 与 PRD 的对应关系

| 需求 | PRD 要求 | 实现状态 |
|------|----------|:--:|
| 记忆蒸馏 | 对历史对话进行压缩与提炼（§5.4） | ✅ 批量对话分析 |
| Cron 定时引擎 | 定时执行进化任务（§5.4） | ✅ 时间窗口调度 |
| 改进建议 | 提炼共性改进点（§5.4） | ✅ 8 类洞察 |
| 技能进化 | 蒸馏结果驱动 Skill 更新（§5.4） | ✅ SkillSuggestion |

## 1. 架构设计

```
app/evolution/
├── __init__.py          # 公开 API 导出
├── models.py            # 数据模型：DistillResult / DistillInsight / SkillSuggestion
├── distiller.py         # 蒸馏器：LLM 驱动的批量对话分析
└── scheduler.py         # 调度器：cron 定时触发蒸馏任务

app/core/
├── config.py            # 新增 EVOLUTION_DISTILL_* 配置项（8 项）
└── main.py              # lifespan 集成：启动/停止调度器
```

### 1.1 核心数据结构

```
DistillResult (一次蒸馏分析的完整结果)
├── conversations_analyzed / messages_analyzed: int
├── analysis_period: str          # 人类可读的时间范围
├── summary: str                  # 整体分析摘要
├── insights: list[DistillInsight]  # 洞察列表
├── skill_suggestions: list[SkillSuggestion]  # 技能建议
├── total_issues / critical_count / high_count / medium_count / low_count
└── error: str | None

DistillInsight (单条蒸馏洞察)
├── severity: InsightSeverity     # CRITICAL | HIGH | MEDIUM | LOW
├── category: InsightCategory     # 8 种分类
├── summary / detail / suggestion: str
├── conversation_ids: list[str]   # 可追溯
└── frequency: int                # 发生频次

SkillSuggestion (技能改善建议)
├── skill_name: str
├── action: str                   # create | update | delete
├── description: str
├── triggers: list[str]           # 触发关键词
├── prompt_injection: str         # 建议注入的提示词
└── insight_index: int            # 关联洞察
```

### 1.2 调度器设计

```
EvolutionScheduler（基于 asyncio 的轻量 cron 调度）
├── _loop() → _tick() 循环
├── _is_in_time_window()：时间窗口检查（默认 02:00-05:00 UTC）
├── _last_distill_time：最小间隔保护（默认 6 小时）
├── start_scheduler() / stop_scheduler()：lifespan 集成
└── 单 tick 异常不影响循环本身
```

### 1.3 蒸馏器设计

```
Distiller（LLM 驱动的批量分析）
├── distill_recent(hours, max_conversations) → DistillResult
├── _fetch_recent_conversations()：从 DB 获取近期对话
├── _build_analysis_text()：序列化为分析文本（每会话截断 2000 字符）
└── _parse_json()：从 LLM 输出提取结构化 JSON
```

## 2. 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 调度方式 | 时间窗口检查（非 cron 表达式） | 骨架阶段简化，避免引入 croniter 依赖 |
| 时间窗口 | 02:00-05:00 UTC | 对应北京时间 10:00-13:00，业务低峰 |
| 分布式锁 | 无（骨架阶段） | 单机部署足够；内核打磨阶段补充 Redis 锁 |
| 分析方式 | 批量 LLM 调用 | 一次调用分析一批对话，节省 Token 成本 |
| 文本截断 | 每会话 ≤2000 字符 | 避免超出 LLM 上下文窗口 |
| 默认关闭 | `EVOLUTION_DISTILL_ENABLED=False` | 避免无配置时消耗 LLM Token |

## 3. 配置项

```yaml
# Evolution 进化系统（夜间蒸馏）
EVOLUTION_DISTILL_ENABLED: false          # 是否启用夜间蒸馏调度器
EVOLUTION_DISTILL_INTERVAL_SECONDS: 3600  # 调度器扫描间隔（秒）
EVOLUTION_DISTILL_HOURS: 24               # 分析最近多少小时的对话
EVOLUTION_DISTILL_MAX_CONVERSATIONS: 50   # 单次蒸馏最多分析的会话数
EVOLUTION_DISTILL_MIN_INTERVAL_HOURS: 6   # 两次蒸馏之间的最小间隔（小时）
EVOLUTION_DISTILL_WINDOW_START_HOUR: 2    # 蒸馏时间窗口起始（UTC 小时）
EVOLUTION_DISTILL_WINDOW_END_HOUR: 5      # 蒸馏时间窗口结束（UTC 小时）
```

## 4. 测试覆盖

| 测试 | 说明 |
|------|------|
| Test 1 | DistillInsight 创建与字段验证 |
| Test 2 | SkillSuggestion 创建与字段验证 |
| Test 3 | DistillResult calculate_stats 统计计算 |
| Test 4 | DistillResult 默认值 |
| Test 5 | DistillResult 含技能建议的完整场景 |
| Test 6 | InsightSeverity 枚举值 |
| Test 7 | InsightCategory 枚举值（8 种分类） |
| Test 8 | DistillResult 错误处理 |
| Test 9 | DistillResult 空洞察场景 |
| Test 10 | Scheduler _is_in_time_window 时间窗口判断 |

## 5. 后续计划（内核打磨阶段）

- cron 表达式解析（引入 croniter）
- 分布式锁（Redis）防止多实例重复蒸馏
- 蒸馏结果 DB 持久化（可查询历史）
- 改进趋势追踪（时间序列分析）
- Skill 自动更新（蒸馏结果 → Skill manifest）
- 知识库缺口自动发现与补充