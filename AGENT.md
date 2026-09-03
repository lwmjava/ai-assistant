# AGENT.md — ai-assistant AI 开发规则

> 本文档定义 **ai-assistant** 项目的 AI 开发规则。所有 AI Agent 在修改、扩展或重构本项目时，**必须严格遵守**以下约束。违反规则将导致代码审查不通过。

---

## 1. 项目定位与边界

### 1.1 项目定位
**ai-assistant** 是一个企业级开源 AI 助手平台后端，提供：
- **RAG 检索增强生成**（混合检索：向量 + BM25 + RRF 融合）
- **Agent 五阶段编排管线**（理解 → 规划 → 行动 → 反思 → 响应；可选 LangGraph Supervisor）
- **MCP 协议集成**（连接企业系统 ERP/CRM/DB/API）
- **工作流引擎**（cron 定时触发，经 Bridge 复用 ChatService）
- **多租户 RBAC 权限体系**（五级角色 + 权限矩阵）
- **横切能力**（均有独立开关）：记忆压缩、内容安全治理、审计日志、进化（Reflect/Distill）、调试 Trace

### 1.2 项目边界
- **当前范围**：后端 API 服务（FastAPI），无前端代码
- **默认 LLM**：DeepSeek（`deepseek-chat`），兼容所有 OpenAI 协议接口
- **文档解析**：仅支持 `.txt` / `.md` 文本文件，不支持 PDF/Word/Excel/PPTX
- **Agent 引擎**：默认自研五阶段管线（`AGENT_ORCHESTRATION=self`），**不把 LangGraph/LangChain 作为主路径依赖**；可选 `langgraph` 仅覆盖 Supervisor 多 Worker 协作，且为可选依赖
- **工具调用**：提示词驱动的 `<tool_call>` 信封，而非 LLM 原生 Function Calling API
- **骨架模块**：`memory/`、`security/`、`audit/`、`evolution/`、`debug/` 已落地并可开关，部分能力仍为骨架（如记忆不跨会话持久化、安全默认多告警少阻断）。**不可再声称这些模块「未实现」**，也不可把骨架写成生产完备
- **JWT 鉴权 ≠ 内容安全**：鉴权/RBAC 在 `app/core/security.py`；输入输出过滤、注入检测在 `app/security/`

---

## 2. 模块架构与职责边界

### 2.1 模块分层（严格遵循，不可越界）

```
app/
├── core/              # 基础设施：配置、数据库、JWT/RBAC（纯逻辑，不依赖 FastAPI）
├── models/            # 数据模型层（SQLModel 表定义，不含业务逻辑）
├── schemas/           # 请求/响应 Pydantic 模型（API 契约）
├── api/               # HTTP 路由层（薄层，仅做参数校验、调用 service、返回响应）
│   ├── deps.py        # 依赖注入（get_db, get_current_user, require_permission）
│   └── routes/        # 按领域拆分：health / auth / chat / rag / mcp / workflow / audit
├── services/          # 业务逻辑层（编排 Agent、持久化、权限校验）
├── agents/            # Agent 编排引擎（管线 + Supervisor + 提示词）
│   ├── tools/         # 工具抽象、内置工具、代码沙箱
│   └── skills/        # YAML 声明式技能（匹配后注入系统提示）
├── llm/               # LLM 抽象层（接口 + 工厂 + 多提供商实现）
├── rag/               # RAG 检索（摄取 + 嵌入 + 向量库 + 检索器）
│   ├── embeddings/    # 嵌入模型抽象与实现
│   ├── vectorstore/   # 向量库抽象与实现
│   └── backend/       # 切分/检索策略（默认 native；langchain / llamaindex 后续接入）
├── mcp/               # MCP 协议集成（客户端 + 管理器 + 适配器）
├── workflow/          # 工作流引擎（cron 调度 + 执行引擎 + ChatService 桥）
├── memory/            # 对话窗口裁剪与 LLM 压缩（当前不跨会话持久化）
├── security/          # 内容安全治理（输入/输出过滤、注入检测、脱敏、限流）
├── audit/             # 审计日志（写入器 + 模型；Admin 路由在 api/routes）
├── evolution/         # 进化：Reflect 异步反思 + Distill 夜间蒸馏
├── debug/             # Agent 执行 Trace（内存环形缓冲）
└── channels/          # Channel 抽象层（多入口扩展点；HTTP 流量仍走 FastAPI）
```

分层目录（`core` / `models` / `schemas` / `api` / `services`）保持稳定。能力模块（`agents` / `llm` / `rag` / `mcp` / `workflow` 等）按可复用技术域平铺，**不要**为业务场景（面试官、质检）或基础设施切片（网关、鉴权、缓存、录音、录屏）各开一个顶层包。

### 2.2 层级规则

| 规则 | 说明 |
|------|------|
| **路由层不写业务逻辑** | `api/routes/*.py` 只能做：参数校验、调用 service、格式化响应 |
| **Service 层持有业务逻辑** | 所有编排、持久化、权限判断在 `services/` 中完成 |
| **Model 层不写业务逻辑** | `models/*.py` 仅定义表结构，不放任何业务方法 |
| **core 层不依赖 FastAPI** | `core/` 模块保持纯 Python，可在测试中独立使用 |
| **抽象层定义接口** | 每个外部依赖（LLM、嵌入、向量库、Channel）必须先定义抽象基类，再提供实现 |

### 2.3 跨模块依赖规则

```
允许的依赖方向（单向，不可逆）：
  api/routes → services → core / models / agents / rag / llm / mcp / workflow / memory / security / audit
  agents → llm / rag / tools / skills
  workflow → services（经 Bridge 调用 ChatService，禁止再造一套 Agent 运行时）
  rag → embeddings / vectorstore / models / backend
  mcp → agents/tools / core
  memory / security / audit / evolution / debug → llm / core / models（按需）
  channels → core

禁止的依赖方向：
  core → api（基础设施不依赖路由层）
  models → services（数据模型不依赖业务层）
  llm → agents（LLM 抽象不依赖 Agent 编排）
  rag / mcp / llm → api（能力层不依赖路由）
```

### 2.4 何时新开顶层目录

**只有独立技术能力域**才在 `app/` 下新增包，需同时大致满足：可被多个上层复用、有自己的生命周期或可替换实现、塞进现有包会越界、需要独立开关与测试。

| 应新开（能力域） | 不应新开（放现有位置） |
|------------------|------------------------|
| 可替换运行时：ASR、对象存储+转码、新协议客户端 | 业务场景：面试官、质检、陪练 → `services/` + `api/routes/` |
| 独立调度/引擎（类似 `workflow/`） | 网关 / JWT 鉴权 / 缓存 → `core/` + `api/`（`core` 变厚则在 `core/` 内分子包） |
| | 同一种媒体的不同形态：录音、录屏、视频生成 → 至多一个 `media/`，不要三个包 |
| | 新工具、新 Skill、新向量库实现 → 放进 `agents/tools`、`agents/skills`、`rag/vectorstore` |

目录按「可 import 的零件」命名，不按「用户菜单上的功能」命名。

---

## 3. 代码风格与命名规范

### 3.1 通用规范

- **语言**：注释与文档字符串使用**中文**；代码标识符（变量、函数、类名）使用**英文**
- **行长**：最大 **120 字符**（`pyproject.toml` 中 `line-length = 120`）
- **缩进**：4 空格
- **类型注解**：所有公共函数/方法必须包含完整类型注解
- **文档字符串**：每个模块、类、公共方法必须有文档字符串（中文），说明职责与参数

### 3.2 命名约定

| 类型 | 约定 | 示例 |
|------|------|------|
| 模块文件 | `snake_case` | `chat_service.py`, `openai_compatible.py` |
| 类 | `PascalCase` | `AgentPipeline`, `ChatService`, `RAGService` |
| 函数/方法 | `snake_case` | `get_llm_provider()`, `create_access_token()` |
| 私有方法 | `_` 前缀 | `_build_understand()`, `_can_access()` |
| 常量 | `UPPER_SNAKE_CASE` | `JWT_ALGORITHM`, `ROLE_PERMISSIONS` |
| 枚举值 | `lower_snake_case` | `Role.SYSTEM_ADMIN`, `ChannelType.API_HTTP` |
| 测试文件 | `test_*.py` | `test_agent.py`, `test_chat.py` |

### 3.3 导入顺序

按 `ruff` 的 `I` 规则（isort）：
1. `from __future__ import annotations`（如需要）
2. 标准库
3. 第三方库
4. 项目内部模块（`app.*`）

---

## 4. 架构模式（必须遵循）

### 4.1 抽象基类 + 工厂模式

所有可替换的外部依赖必须遵循此模式：

```python
# 1. 定义抽象基类 (base.py)
class SomeProvider(ABC):
    @abstractmethod
    async def do_something(self) -> str: ...

# 2. 提供工厂函数 (factory.py)，支持全局覆盖
_override: SomeProvider | None = None

def set_some_override(provider: SomeProvider | None) -> None:
    global _override
    _override = provider

def get_some_provider() -> SomeProvider:
    if _override is not None:
        return _override
    # 按配置选择实现...
```

**已应用此模式的模块**：`llm/`、`rag/embeddings/`、`rag/vectorstore/`、`channels/`；MCP / Skill / Memory / Audit 等使用进程级单例工厂（`get_*` / `set_*_override` 或 `reset_*`）。

### 4.2 配置管理

- 所有配置集中在 `app/core/config.py` 的 `Settings` 类中
- 字段命名使用 `UPPER_SNAKE_CASE`，自动映射环境变量
- **密钥类配置**（API Key、JWT Secret）**禁止**写默认值，必须通过环境变量注入
- 新增配置项时，同步更新 `.env.example` 文件

### 4.3 多租户隔离

- 所有数据表必须包含 `tenant_id` 字段
- 所有查询必须按 `tenant_id` 过滤
- 系统管理员可见同租户全部数据，普通用户仅可见自身数据

### 4.4 错误处理

```python
# 可恢复的异常：捕获并记录日志，不阻断主流程
try:
    ...
except Exception:  # noqa: BLE001 — 单点故障不应阻断整体
    logger.exception("描述性消息")

# 不可恢复的异常：向调用方抛出明确异常
raise HTTPException(status_code=400, detail="具体错误描述")
```

- 路由层使用 `HTTPException` 返回标准错误
- Service 层使用 `ValueError` / 自定义异常描述业务错误
- 管线级兜底使用 `BLE001` 注释标记已知风险

### 4.5 模型主键

所有表主键使用**十六进制 UUID 字符串**：

```python
def _uuid() -> str:
    return uuid.uuid4().hex
```

---

## 5. API 设计规范

### 5.1 路由组织

- 每个领域一个路由文件：`app/api/routes/<domain>.py`
- 路由前缀在 `app/api/router.py` 中统一聚合
- 生成式路由注册到 `api_router`，再由 `main.py` 以 `/api` 前缀挂载

### 5.2 请求/响应模型

- 请求体使用 Pydantic `BaseModel`，定义在 `schemas/` 或路由文件内
- 响应模型使用 Pydantic `BaseModel`，**不直接暴露** SQLModel 实体
- 对外输出字段使用 `model_config = {"from_attributes": True}` 从 ORM 对象映射

### 5.3 依赖注入

```python
# 数据库会话
session: Session = Depends(get_db)

# 当前用户（认证）
current_user: User = Depends(get_current_user)

# 权限守卫
current_user: User = Depends(require_permission("knowledge_bases", "write"))
```

### 5.4 流式响应

- 使用 `sse_starlette.EventSourceResponse` 实现 SSE
- 事件格式：`{"event": type, "data": json_string}`
- 事件类型：`stage`, `token`, `tool`, `error`, `done`

---

## 6. 安全规范（红线，不可违反）

### 6.1 绝对禁止

| 禁止行为 | 说明 |
|----------|------|
| 提交密钥/密码/Token | API Key、JWT Secret、密码等**严禁**出现在代码中 |
| 默认密钥用于生产 | `JWT_SECRET_KEY` 默认值在生产环境会拒绝启动 |
| SQL 拼接 | 所有查询必须使用 ORM（SQLModel/SQLAlchemy），禁止原始 SQL 拼接 |
| 硬编码权限 | 权限判断必须通过 `check_permission()` 或 `require_permission()` |
| 绕过租户隔离 | 所有数据操作必须包含 `tenant_id` 过滤 |

### 6.2 密码处理

- 使用 `bcrypt` 哈希（`hash_password` / `verify_password`）
- 密码最小长度：8 字符（`UserCreate` 中 `min_length=8`）
- 用户信息对外输出**绝不**包含 `hashed_password` 字段

### 6.3 JWT

- 双令牌机制：`access_token`（短期）+ `refresh_token`（长期，支持撤销）
- `refresh_token` 携带 `token_version`，每次登出/改密自增
- 生产环境必须修改 `JWT_SECRET_KEY`，否则启动拒绝

---

## 7. 测试规范

### 7.1 测试约定

- 测试目录：`tests/`
- 测试框架：`pytest` + `pytest-asyncio`
- 测试数据库：`data/test_ai_assistant.db`（独立于开发数据库）
- 环境变量在 `tests/conftest.py` 中统一设置

### 7.2 测试要求

- 新增功能必须附带测试
- 修复 Bug 必须补充回归测试
- 使用 Mock 提供商（`MockLLMProvider` / `MockEmbeddingProvider`）隔离外部依赖
- 测试文件命名：`test_<模块名>.py`

### 7.3 运行测试

```bash
pytest                          # 全部测试
pytest tests/test_agent.py      # 指定模块
ruff check .                    # 代码检查
mypy app/                       # 类型检查
```

---

## 8. 开发工作流

### 8.1 提交规范

遵循 [Conventional Commits](https://www.conventionalcommits.org/)：

```
<type>(<scope>): <subject>

类型：feat / fix / docs / refactor / test / chore / perf
范围：rag / agent / auth / chat / mcp / llm / embedding / vectorstore / workflow / memory / security / audit / evolution / debug / skill / core / api / config
```

### 8.2 新增功能检查清单

- [ ] 配置项已添加到 `config.py` 的 `Settings` 类
- [ ] 配置项已同步到 `.env.example`
- [ ] 数据模型遵循多租户隔离（含 `tenant_id`）
- [ ] API 路由有完整的请求/响应 Pydantic 模型
- [ ] 公共函数有类型注解和文档字符串
- [ ] 新增依赖已添加到 `requirements.txt` 或 `pyproject.toml`
- [ ] 已编写测试（至少覆盖核心路径）
- [ ] 代码通过 `ruff check` 和 `mypy`
- [ ] 敏感信息未出现在代码中

### 8.3 修改现有功能检查清单

- [ ] 理解并遵循现有模块的架构模式
- [ ] 未破坏现有 API 契约（如有必要，通过新增版本化端点处理）
- [ ] 未破坏多租户隔离
- [ ] 现有测试全部通过
- [ ] 如行为变更，已更新对应测试

---

## 9. 常见错误与禁止模式

### 9.1 禁止模式

```python
# ❌ 路由层直接操作数据库
@router.post("/chat")
async def chat(req: ChatRequest, session: Session = Depends(get_db)):
    conv = Conversation(...)             # 错误：应在 service 中处理
    session.add(conv)
    ...

# ✅ 正确：委托给 service
@router.post("/chat")
async def chat(req: ChatRequest, session: Session = Depends(get_db), user=Depends(get_current_user)):
    return await _service.chat(session, user, req.message)
```

```python
# ❌ 硬编码配置值
timeout = 60.0                           # 错误：应使用 settings

# ✅ 正确：从配置读取
from app.core.config import settings
timeout = settings.LLM_TIMEOUT
```

```python
# ❌ 绕过租户隔离
stmt = select(Document).where(Document.user_id == user.id)  # 缺少 tenant_id 过滤

# ✅ 正确：始终包含 tenant_id
stmt = select(Document).where(
    Document.tenant_id == user.tenant_id,
    Document.user_id == user.id,
)
```

### 9.2 常见错误

| 错误 | 正确做法 |
|------|----------|
| 在 `models/` 中添加业务方法 | 业务逻辑放在 `services/` |
| 在 `core/` 中导入 FastAPI 模块 | `core/` 保持纯 Python |
| 直接返回 SQLModel 对象给 API | 使用 Pydantic Schema 转换 |
| 忘记 `noqa: BLE001` 注释 | 对已知的宽泛异常捕获添加注释说明原因 |
| 修改 `main.py` 的启动流程导致破坏 | 理解 `lifespan`：建库 → 引导管理员 → JWT 校验 → 登记 Channel → 启动 Workflow/Evolution 调度器，关闭时停止调度器 |

---

## 10. 环境与依赖

### 10.1 运行环境

- Python：`>=3.11, <3.14`
- Shell：PowerShell（Windows），使用 `;` 而非 `&&` 连接命令

### 10.2 依赖管理

- 核心依赖：`requirements.txt`（版本锁定）
- 可选依赖：`pyproject.toml` 的 `[project.optional-dependencies]`
  - `dev`：pytest, ruff, mypy
  - `milvus`：pymilvus
  - `mcp`：mcp>=1.2.0
  - `workflow`：croniter
- 新增依赖时同步更新 `requirements.txt` 和 `pyproject.toml`

### 10.3 数据库

- 默认：SQLite（`sqlite:///./data/ai_assistant.db`）
- 生产：PostgreSQL 16
- 数据库操作通过 `SQLModel` / `SQLAlchemy` ORM

---

## 11. 文档同步规则

当修改以下内容时，必须同步更新对应文档：

| 修改内容 | 需更新的文档 |
|----------|-------------|
| 新增/修改 API 端点 | `README.md` 的 API 端点表 + 运行 `python docs/export_swagger.py` 重新生成 `swagger.json` |
| 新增/修改配置项 | `.env.example` + `README.md` 的关键配置项表 |
| 新增/修改 Pydantic Schema | 运行 `python docs/export_swagger.py` 重新生成 `swagger.json` |
| 新增模块 | `README.md` 的项目结构说明 + 本 `AGENT.md` 的模块分层 |
| 修改架构/模式 | 本 `AGENT.md` |
| 新增顶层包 | 先对照 §2.4；同步 `README.md` 项目结构与本节目录树 |

---

*最后更新：2026-09-03*