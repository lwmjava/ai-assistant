# 计划：Skill 系统（骨架阶段）

## 目标
引入 YAML 声明式技能系统，使用户可以通过编写 YAML 文件定义技能，Agent 根据用户输入自动匹配并激活对应技能，实现 Prompt 注入和工具绑定两种模式。

## 分支策略
- 基于 `agent-supervisor` 创建扁平分支 **`feat/skill-system`**
- 遵循 Conventional Commits：`feat(skill): 新增 YAML 声明式技能系统骨架`

## 与 PRD 的对应关系

| PRD 要求 | 实现状态 |
|----------|:--:|
| 用户创建 Skill manifest（AGT-04） | ✅ YAML 文件 + 动态 API 注册 |
| Agent 按条件匹配激活 | ✅ 关键词 / 正则 / 始终激活 |
| Prompt 注入模式 | ✅ 匹配时注入系统提示词 |
| 工具模式 | ✅ 绑定 ToolRegistry 中已注册工具 |
| Skill 可扩展（YAML manifest） | ✅ 声明式 YAML |
| Skill 系统正常（Phase 1 验收） | ✅ 双模式支持 |

## 1. 三种实现方式对比

| 方式 | 核心理念 | 优点 | 缺点 | 骨架选择 |
|------|----------|------|------|:--:|
| **YAML Manifest（声明式）** | 用户写 YAML 文件定义技能 | 零代码、用户友好、热加载、版本管理简单 | 灵活性受限 | ✅ 当前实现 |
| **Python Class（编程式）** | 实现 SkillProtocol 的 Python 类 | 极致灵活、可调用任意 API | 需编码能力 | 🔮 内核打磨 |
| **混合模式** | YAML 做声明层，Python Class 做执行层 | 兼顾易用性和灵活性 | 架构复杂度高 | 🔮 未来扩展 |

骨架阶段选择 YAML Manifest，接口层预留 Python Class 扩展点。

## 2. 架构设计

```
app/agents/skills/
├── __init__.py              # 公开 API 导出
├── base.py                  # 数据类型（SkillManifest / SkillMatch / SkillContext 等）
├── loader.py                # YAML 文件发现 + 解析 + 校验
├── manager.py               # SkillManager：加载 / 匹配 / 激活 / 注入
└── builtin/
    ├── code_review.yaml     # 内置技能：代码审查
    └── translator.yaml      # 内置技能：翻译助手

集成点：
  app/services/chat_service.py  ← 技能匹配 → 管线注入
  app/agents/pipeline.py        ← skill_prompt_injection 属性
  app/core/config.py            ← SKILL_ENABLED / SKILL_DIRS
```

### 数据流

```
用户输入 "帮我审查这段代码"
  └─ ChatService._match_skills(message)
       └─ SkillManager.match(user_input)
            ├─ 遍历所有技能 → SkillTrigger.matches()
            ├─ code_review: keyword "审查" hit → confidence=0.5
            └─ return [SkillMatch(skill=code_review, confidence=0.5)]
       └─ SkillManager.activate(matches)
            ├─ 合并 system_prompt → SkillContext.prompt_injection
            └─ return SkillContext

  └─ AgentPipeline.skill_prompt_injection = ctx.prompt_injection
       └─ _stage() 每次调用 LLM 时注入到 system prompt 末尾
```

## 3. 数据类型设计

### SkillMode 枚举
| 值 | 含义 |
|----|------|
| `prompt_injection` | 匹配时将技能指令注入系统提示词 |
| `tool` | 技能注册为 Agent 可调用的工具 |

### TriggerType 枚举
| 值 | 含义 |
|----|------|
| `keyword` | 用户消息包含指定关键词（不区分大小写） |
| `regex` | 用户消息匹配正则表达式 |
| `always` | 始终激活（不判断触发条件） |

### SkillManifest
```yaml
name: code_review          # 技能唯一标识（小写字母/数字/下划线/连字符）
version: "1.0"             # 语义化版本
description: "..."         # 技能描述
mode: prompt_injection     # 激活模式
trigger:                   # 触发条件
  type: keyword
  keywords: ["审查", "review"]
  min_confidence: 0.3
system_prompt: "..."       # 注入的提示词（Prompt 注入模式）
tools: []                  # 绑定的工具名称（Tool 模式）
enabled: true              # 是否启用
```

### 置信度算法
- 匹配 1 个关键词：0.5（基础分）
- 匹配 2 个关键词：0.65
- 匹配 3 个关键词：0.8
- 匹配 4+ 个关键词：0.95+
- 始终激活：1.0

## 4. API 接口

### SkillManager 公开方法

| 方法 | 签名 | 用途 |
|------|------|------|
| `load()` | `(directories?) -> int` | 从 YAML 目录加载技能 |
| `register()` | `(SkillManifest) -> None` | 动态注册技能 |
| `register_from_yaml()` | `(yaml_text, source) -> SkillManifest` | 从 YAML 字符串注册 |
| `unregister()` | `(name) -> bool` | 移除技能 |
| `get()` | `(name) -> SkillManifest \| None` | 按名称获取 |
| `list_all()` | `() -> list[SkillManifest]` | 列出所有技能 |
| `match()` | `(user_input, max_results, min_confidence) -> list[SkillMatch]` | 匹配用户输入 |
| `activate()` | `(matches) -> SkillContext` | 激活匹配的技能 |
| `inject_prompt()` | `(ctx, base_prompt) -> str` | 注入技能提示词 |
| `collect_tools()` | `(ctx, registry) -> list[Tool]` | 收集技能绑定的工具 |
| `describe()` | `() -> str` | 生成技能清单（供 LLM 阅读） |
| `reload()` | `() -> int` | 热加载技能 |

### 全局单例
```python
from app.agents.skills import get_skill_manager
mgr = get_skill_manager()  # 首次调用自动加载内置技能
```

## 5. 内置技能

| 技能 | 触发关键词 | 模式 |
|------|-----------|------|
| `code_review` | 审查, code review, 检查代码, review code, 代码审查, review | prompt_injection |
| `translator` | 翻译, translate, 翻译成, translate to, 用中文, 用英文 | prompt_injection |

## 6. 验证结果

```
Test 1: Loaded 2 skills                         ✅
Test 2: Match "帮我审查代码" → code_review       ✅
Test 3: No match "今天天气怎么样"                  ✅
Test 4: Activate translator                      ✅
Test 5: Inject prompt (28 → 269 chars)           ✅
Test 6: Describe skills                          ✅
Test 7: Dynamic register from YAML               ✅
Test 8: Unregister                               ✅
Test 9: Global singleton                         ✅
```

## 7. 文件清单

| 文件 | 操作 | 内容 |
|------|:--:|------|
| `app/agents/skills/__init__.py` | 新增 | 公开 API 导出 |
| `app/agents/skills/base.py` | 新增 | 数据类型（SkillManifest/SkillMatch/SkillContext 等） |
| `app/agents/skills/loader.py` | 新增 | YAML 文件发现 + 解析 + 校验 |
| `app/agents/skills/manager.py` | 新增 | SkillManager：加载/匹配/激活/注入 |
| `app/agents/skills/builtin/code_review.yaml` | 新增 | 内置技能：代码审查 |
| `app/agents/skills/builtin/translator.yaml` | 新增 | 内置技能：翻译助手 |
| `app/agents/pipeline.py` | 修改 | +skill_prompt_injection 属性 + _stage 注入 |
| `app/services/chat_service.py` | 修改 | +技能匹配 + 上下文注入管线 |
| `app/core/config.py` | 修改 | +SKILL_ENABLED / SKILL_DIRS |
| `tests/test_skill_smoke.py` | 新增 | 9 个冒烟测试 |

## 8. 骨架标记（SKELETON）— 内核打磨阶段待补充

| 标记 | 内容 | 优先级 |
|------|------|:--:|
| LLM 语义匹配 | 替代关键词/正则的粗粒度匹配，用 LLM 做意图→技能映射 | P0 |
| Python Class 模式 | 实现 SkillProtocol，支持编程式技能定义 | P1 |
| 技能热加载 | 文件监控（watchdog）+ 自动重载，无需重启 | P1 |
| 技能效果评估 | Phase 4 Reflect 闭环：评估技能效果 → 自动改进/淘汰 | P1 |
| 数据库持久化 | 技能存储到数据库，支持 CRUD API + Admin UI | P1 |
| 技能市场 | v2.5+ 社区技能市场（与 PRD §9.3 阶段四对齐） | P3 |
| 工具模式完善 | Tool 模式技能自动注册为 Agent 工具 | P1 |
| 技能版本管理 | 语义化版本 + 升级/降级/回滚 | P2 |
| 技能依赖 | 技能间依赖声明（A 依赖 B） | P2 |

## 9. 用户自定义技能示例

```yaml
# 放在 skills/ 目录下的 .yaml 文件即可自动加载
name: my_custom_skill
version: "1.0"
description: "我的自定义技能"
mode: prompt_injection
trigger:
  type: keyword
  keywords: ["我的关键词", "my keyword"]
  min_confidence: 0.3
system_prompt: |
  你是一个专业助手。当用户提问时：
  1. 首先分析问题
  2. 给出专业回答
  3. 提供进一步建议
tools: []
enabled: true
```

## 10. 后续演进

- **Phase 1 内核打磨**：LLM 语义匹配 + Python Class 模式
- **Phase 4 记忆与进化**：Reflect 异步反思 → Skill 改进闭环
- **Phase 5 管理后台**：技能 CRUD UI + 技能效果仪表板