# 计划：Reflect 反思（骨架阶段）

## 目标
引入对话结束后异步反思机制，自动审查完整对话、提取改进点与待办事项，为 Skill 自动改进、夜间蒸馏等进化闭环奠定基础。

## 分支策略
- 基于 `agent-supervisor` 创建扁平分支 **`feat/reflect-loop`**
- 遵循 Conventional Commits：`feat(evolution): 新增 Reflect 异步反思系统骨架`

## 与 PRD 的对应关系

| PRD 要求 | 实现状态 |
|----------|:--:|
| Reflect 反思（P1） | ✅ 对话结束后 LLM 异步审查 |
| 提取改进点 | ✅ 4 级严重程度 + 8 类改进分类 |
| 提取 Action Items | ✅ 描述 + 优先级 + 隐含负责人/截止时间 |
| Skill 自动改进 | 🔮 内核打磨（改进点 → Skill manifest 更新） |
| Action Item → Workflow 调度 | 🔮 内核打磨 |
| 夜间蒸馏调度器 | 🔮 后续骨架（Evolution 调度器） |

## 与现有管线反思的区别

| 维度 | 管线内反思（已有） | Reflect 异步反思（新增） |
|------|-------------------|------------------------|
| 触发时机 | 同步，在管线"反思"阶段 | 异步，对话结束后 |
| 审查范围 | 仅审查当前回答草稿 | 审查完整对话（多轮） |
| 产出 | 修正要点（回灌响应阶段） | 改进点 + 待办事项（结构化） |
| 影响范围 | 当前回答质量 | 长期 Agent 进化 |
| 阻塞性 | 阻塞响应（增加延迟） | 非阻塞（fire-and-forget） |

## 1. 架构设计

```
app/evolution/
├── __init__.py          # 公开 API 导出
├── models.py            # 数据类型（ReflectResult / ImprovementPoint / ActionItem）
└── reflector.py         # Reflector：LLM 驱动的对话审查引擎

app/services/
└── chat_service.py      # 集成：chat() / chat_stream() 后触发 _maybe_reflect()
```

### 1.1 数据模型

**ImprovementPoint**（改进建议）：
| 字段 | 类型 | 说明 |
|------|------|------|
| severity | Severity | critical / high / medium / low |
| category | ImprovementCategory | accuracy / completeness / clarity / structure / safety / efficiency / skill / other |
| summary | str | 一句话摘要 |
| detail | str | 详细描述 |
| suggestion | str | 改进建议 |
| affected_skill | str? | 关联的技能名称 |

**ActionItem**（待办事项）：
| 字段 | 类型 | 说明 |
|------|------|------|
| description | str | 待办事项描述 |
| priority | str | high / medium / low |
| assignee_hint | str? | 隐含的负责人 |
| deadline_hint | str? | 隐含的截止时间 |

**ReflectResult**（反思产出）：
| 字段 | 类型 | 说明 |
|------|------|------|
| conversation_id | str | 关联会话 ID |
| summary | str | 整体反思摘要 |
| improvements | list[ImprovementPoint] | 改进点列表 |
| action_items | list[ActionItem] | 待办事项列表 |
| quality_score | float | 管线 QualityGate 评分 |
| revision_count | int | 自纠错轮数 |
| error | str? | 异常信息 |

### 1.2 反思流程

```
对话结束（chat / chat_stream 返回前）
    │
    ├─ EVOLUTION_ENABLED=false → 跳过
    │
    ├─ 构建对话文本（_build_reflect_conversation_text）
    │
    ├─ EVOLUTION_REFLECT_ASYNC=true → asyncio.create_task（fire-and-forget）
    │   │
    │   └─ Reflector.reflect(conversation_text)
    │       │
    │       ├─ LLM 调用（JSON 结构化输出）
    │       ├─ 解析 JSON（支持 markdown 包裹 / 裸 JSON）
    │       ├─ 提取改进点 → 按严重程度/分类标记
    │       ├─ 提取待办事项 → 按优先级标记
    │       └─ 记录日志（info 级别）
    │
    └─ 失败 → 仅记录日志，不影响对话响应
```

### 1.3 LLM 输出格式

```json
{
  "summary": "整体反思摘要（1-2 句话）",
  "improvements": [
    {
      "severity": "high",
      "category": "accuracy",
      "summary": "事实错误",
      "detail": "回答了错误的数据...",
      "suggestion": "核实数据来源"
    }
  ],
  "action_items": [
    {
      "description": "明天提交报告",
      "priority": "high",
      "assignee_hint": "用户",
      "deadline_hint": "明天"
    }
  ]
}
```

## 2. 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 与管线内反思的关系 | 互补，非替代 | 管线反思是同步质量保障，Reflect 是异步进化分析 |
| 触发时机 | 对话结束后立即触发 | 保证反思的上下文完整，不丢失关键信息 |
| 执行模式 | 异步 fire-and-forget | 不增加用户等待时间，反思是辅助功能 |
| 失败策略 | 静默失败，仅记录日志 | 反思不应阻塞对话或破坏用户体验 |
| JSON 解析容错 | 三层 fallback：裸 JSON → markdown 代码块 → regex 提取 | LLM 输出格式不稳定，需要鲁棒解析 |
| 默认关闭 | EVOLUTION_ENABLED=False | 进化系统是实验性功能，需用户显式开启 |
| 无 DB 持久化 | 骨架阶段仅内存+日志 | 先验证价值，再决定持久化策略 |

## 3. 集成点

| 模块 | 集成方式 | 状态 |
|------|----------|:--:|
| `app/services/chat_service.py` | chat() / chat_stream() 后调用 _maybe_reflect() | ✅ |
| `app/core/config.py` | 新增 EVOLUTION_ENABLED / REFLECT_ENABLED / REFLECT_ASYNC | ✅ |
| Skill 系统 | 改进点 → Skill manifest 更新 | 🔮 内核打磨 |
| Workflow 引擎 | Action Item → 工作流调度器 | 🔮 内核打磨 |
| 审计日志 | 反思结果写入审计日志 | 🔮 内核打磨 |

## 4. 测试覆盖

| 测试 | 说明 |
|------|------|
| Test 1 | 数据类型实例化 |
| Test 2 | ReflectResult 空状态 |
| Test 3 | Severity / ImprovementCategory 枚举完整性 |
| Test 4 | Reflector 有效 JSON 解析 |
| Test 5 | Reflector 空改进点 |
| Test 6 | Reflector markdown 包裹 JSON |
| Test 7 | Reflector 畸形 JSON 容错 |
| Test 8 | Reflector 缺失可选字段 |
| Test 9 | Reflector 无效枚举值容错 |

## 5. 后续规划

### 5.1 内核打磨（同分支后续）
- [ ] 改进点持久化（ReflectRecord SQLModel）
- [ ] 改进点 → Skill manifest 自动更新建议
- [ ] Action Item → Workflow 调度器写入
- [ ] 改进趋势追踪（时间序列分析）

### 5.2 后续骨架（新分支）
- [ ] Evolution 调度器（夜间 cron 调度）
- [ ] 记忆蒸馏（长期记忆压缩）
- [ ] Skill 效果评估与淘汰
- [ ] 跨会话模式识别