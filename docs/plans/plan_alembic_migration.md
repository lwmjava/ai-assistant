# 计划：Alembic 数据库迁移（骨架阶段）

## 目标
引入 Alembic 管理数据库 schema 版本演进，替代当前 `SQLModel.metadata.create_all()` 的粗放方式，实现可追溯、可回滚、可审计的数据库变更。

## 分支策略
- 基于 `agent-supervisor` 创建扁平分支 **`feat/alembic-migration`**
- 遵循 Conventional Commits：`feat(migration): 新增 Alembic 数据库迁移骨架`

## 与 PRD 的对应关系

| PRD 要求 | 实现状态 |
|----------|:--:|
| 引入 Alembic（§6.7 P0） | ✅ |
| schema 版本号与应用版本绑定 | ✅ `alembic_version` 表 |
| 启动时自动迁移 | ✅ `auto_migrate()` |
| CLI `migrate` 封装 | ✅ `python -m app.core.migration migrate` |
| `--check` dry-run | ✅ `python -m app.core.migration check` |
| `--yes` 非交互环境 | ✅ `--yes` / `-y` 参数 |
| 迁移项清单 + y/N 确认 | ✅ 交互式确认 |

## 1. 架构设计

```
alembic.ini                     # Alembic 配置（DB URL 由 env.py 注入）
alembic/
├── env.py                      # 迁移环境：SQLModel metadata + 双模式（在线/离线）
├── script.py.mako              # 迁移脚本模板
└── versions/
    └── 6f8d055bdc4a_initial_schema.py  # 初始迁移（7 张表）

app/core/
├── database.py  (修改)         # init_db() 新增 auto_migrate 参数
└── migration.py (新增)         # 迁移运行器：auto_migrate / upgrade / downgrade / stamp / CLI
```

### 数据流

```
应用启动
  └─ main.py lifespan
       └─ init_db(auto_migrate=True)
            ├─ 1. SQLModel.metadata.create_all(engine)  ← 幂等建表（兜底）
            └─ 2. migration.auto_migrate()
                 ├─ get_pending_migrations() → [revision_list]
                 ├─ 无待迁移 → 跳过
                 ├─ 有待迁移 → upgrade("head")
                 └─ 失败 → 开发环境警告 / 生产环境拒绝启动
```

## 2. 关键设计决策

| 决策 | 理由 |
|------|------|
| **create_all + Alembic 双轨** | `create_all` 保底兼容（没装 alembic 也能跑），Alembic 提供版本追踪 |
| **DB URL 由 env.py 注入** | 不硬编码在 alembic.ini，从应用配置动态读取，支持多环境 |
| **render_as_batch（SQLite）** | SQLite 不支持 ALTER TABLE，batch 模式通过 CREATE TABLE AS 实现 |
| **compare_type=True** | autogenerate 自动检测列类型变更，减少手动维护 |
| **开发环境失败不阻塞** | 骨架阶段友好——迁移失败记录警告，不阻塞启动 |
| **生产环境失败拒绝启动** | 安全优先——schema 不一致可能导致数据损坏 |
| **stamp() 首次部署** | 已有数据库直接标记当前版本，避免重复执行 DDL |

## 3. 初始迁移覆盖的 7 张表

| 表名 | 模型 | 关键字段 |
|------|------|----------|
| `tenants` | `Tenant` | id, name, is_active |
| `users` | `User` | id, tenant_id(FK), username(unique), email(unique), hashed_password, role, token_version |
| `conversations` | `Conversation` | id, tenant_id, user_id, title |
| `messages` | `Message` | id, conversation_id(FK), role, content, model |
| `rag_documents` | `Document` | id, tenant_id, user_id, title, source, chunk_count |
| `rag_document_chunks` | `DocumentChunk` | id, tenant_id, document_id(FK), chunk_index, content, embedding, tokens |
| `workflows` | `Workflow` | id, tenant_id, owner_id, name, cron_expr, prompt_template, enabled |
| `workflow_executions` | `WorkflowExecution` | id, workflow_id(FK), tenant_id, triggered_by, status, duration_ms |

## 4. API 接口

### `app/core/migration.py` 公开函数

| 函数 | 签名 | 用途 |
|------|------|------|
| `auto_migrate()` | `() -> bool` | 启动时自动迁移，失败返回 False |
| `upgrade()` | `(target="head", sql=False)` | 升级到目标版本 |
| `downgrade()` | `(target: str)` | 降级到目标版本 |
| `stamp()` | `(target="head")` | 标记版本（不执行 DDL） |
| `get_current_revision()` | `() -> str \| None` | 当前数据库版本 |
| `get_head_revision()` | `() -> str` | 迁移链最新版本 |
| `get_pending_migrations()` | `() -> list[str]` | 待迁移版本列表 |
| `get_migration_history()` | `() -> list[dict]` | 迁移历史 |

### CLI 子命令

```bash
python -m app.core.migration check     # 检查待迁移版本
python -m app.core.migration migrate   # 执行迁移（交互确认）
python -m app.core.migration migrate --yes  # 跳过确认
python -m app.core.migration history   # 查看迁移历史
```

## 5. 迁移开发工作流

```bash
# 1. 修改模型（app/models/*.py）
# 2. 生成迁移脚本
alembic revision --autogenerate -m "add_skill_table"

# 3. 检查生成的迁移脚本（人工 review）
# 4. 执行迁移
python -m app.core.migration migrate --yes

# 5. 回滚（如需要）
python -c "from app.core.migration import downgrade; downgrade('-1')"
```

## 6. 验证结果

```
$ python -m app.core.migration check
Database URL: sqlite:///./data/ai_assistant.db
Current revision: 6f8d055bdc4a
Head revision:    6f8d055bdc4a
Database is up to date.

$ python -m app.core.migration history
Migration history (1 revisions):
  6f8d055bdc4a -> base: initial_schema

$ python -c "from app.core.migration import auto_migrate; print(auto_migrate())"
True
```

## 7. 文件清单

| 文件 | 操作 | 内容 |
|------|:--:|------|
| `alembic.ini` | 新增 | Alembic 配置文件 |
| `alembic/env.py` | 新增 | 迁移环境（SQLModel metadata + 在线/离线模式） |
| `alembic/script.py.mako` | 新增 | 迁移脚本模板 |
| `alembic/versions/6f8d055bdc4a_initial_schema.py` | 新增 | 初始迁移（7 张表 + 外键 + 索引） |
| `app/core/migration.py` | 新增 | 迁移运行器 + CLI |
| `app/core/database.py` | 修改 | `init_db()` 新增 `auto_migrate` 参数 |
| `requirements.txt` | 修改 | +`alembic==1.14.1` |

## 8. 骨架标记（SKELETON）— 内核打磨阶段待补充

| 标记 | 内容 | 优先级 |
|------|------|:--:|
| CLI 框架 | 从 `python -m` 迁移到 click/typer + `ai-assistant migrate` 入口 | P0 |
| 彩色输出 | 使用 rich 库输出彩色表格（status 命令） | P1 |
| PostgreSQL 支持 | 移除 `render_as_batch`，使用原生 ALTER TABLE | P1 |
| 迁移测试 | CI 中自动测试 upgrade → downgrade → upgrade 往返 | P1 |
| 迁移前备份 | 自动备份 SQLite 文件或 PostgreSQL dump | P1 |
| 审计日志 | 迁移操作写入审计日志（谁、何时、执行了什么） | P1 |
| 迁移锁 | 多实例部署时防止并发迁移（PostgreSQL advisory lock） | P2 |
| 数据迁移 | 支持 Python 数据迁移（非纯 DDL），如列重命名 + 数据转换 | P2 |
| 种子数据 | 迁移脚本中嵌入初始数据（如默认角色） | P2 |
| Docker 集成 | Dockerfile 中自动执行迁移 | P1 |

## 9. 风险与注意

- **SQLite ALTER 限制**：`render_as_batch` 通过重建表实现 ALTER，复杂迁移可能丢失约束。生产环境建议 PostgreSQL。
- **create_all 与 Alembic 共存**：`create_all` 在 Alembic 之前执行，确保首次部署时表已存在，Alembic 仅标记版本。后续模型变更仅通过 Alembic 迁移脚本执行。
- **autogenerate 局限性**：列重命名、表重命名无法自动检测，需手动编辑迁移脚本。建议生成后人工 review。
- **迁移脚本不可变**：已合入主分支的迁移脚本不得修改，只能新增迁移脚本修复。

## 10. 后续演进

- **Phase 0 内核打磨**：补齐 SKELETON 标记的 P0/P1 项（CLI 框架、PostgreSQL 支持、CI 测试）
- **Phase 2 RAG**：新增向量索引表、文档状态机表等迁移脚本
- **Phase 4 记忆**：新增记忆表、对话摘要表等迁移脚本
- **Phase 5 管理后台**：新增审计日志表、Feature Flag 表等迁移脚本