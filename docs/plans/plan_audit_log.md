# 计划：审计日志（骨架阶段）

## 目标
引入审计日志系统，记录所有可审计事件（登录/CRUD/危险操作），支持 Admin API 查询/过滤/分页，满足 PRD §5.3.2 权限矩阵中"审计日志可查询"的 P0 要求。

## 分支策略
- 基于 `agent-supervisor` 创建扁平分支 **`feat/audit-log`**
- 遵循 Conventional Commits：`feat(audit): 新增审计日志系统骨架`

## 与 PRD 的对应关系

| PRD 要求 | 实现状态 |
|----------|:--:|
| 审计日志可查询（P0） | ✅ `GET /api/admin/audit-logs` + 多条件过滤 + 分页 |
| 日志保留 ≥90 天（P0） | ✅ `AUDIT_RETENTION_DAYS=90` + `cleanup_old_logs()` |
| 仅 system_admin / system_viewer 可查看 | ✅ 权限守卫 `_require_system_admin_or_viewer` |
| 所有危险操作写入审计日志 | ✅ 22 种 `AuditAction` 事件类型 |
| DB 写入失败不丢数据 | ✅ structlog 降级机制 |
| 合规报表导出 | 🔮 Phase 5 内核打磨 |
| 数据保留策略可配置 | 🔮 Phase 5 内核打磨 |

## 1. 架构设计

```
app/audit/
├── __init__.py          # 公开 API 导出
├── models.py            # AuditAction 枚举 + AuditLog SQLModel
└── logger.py            # AuditLogger（DB 持久化 + structlog 降级）

app/api/routes/
└── audit.py             # Admin API：GET /api/admin/audit-logs
```

### 1.1 数据模型

**AuditLog**（audit_logs 表）：
| 字段 | 类型 | 说明 |
|------|------|------|
| id | str (UUID) | 主键 |
| user_id | str? | 操作者 ID（索引） |
| tenant_id | str? | 所属租户 ID（索引） |
| action | str | 事件类型（索引） |
| resource_type | str? | 资源类型（索引） |
| resource_id | str? | 资源 ID |
| details | JSON? | 详细上下文（灵活扩展） |
| ip_address | str? | 客户端 IP |
| user_agent | str? | 客户端 UA |
| created_at | datetime | 创建时间（审计时间线） |
| updated_at | datetime | 更新时间（原则上不可变） |

**AuditAction**（22 种事件类型）：
- 认证：USER_LOGIN, USER_LOGOUT, USER_TOKEN_REFRESH
- 用户管理：USER_CREATE, USER_UPDATE, USER_DELETE, USER_DISABLE, USER_ENABLE, USER_PASSWORD_RESET, USER_ROLE_CHANGE
- 租户管理：TENANT_CREATE, TENANT_UPDATE, TENANT_DELETE, TENANT_ACTIVATE, TENANT_DEACTIVATE
- 对话：CONVERSATION_CREATE, CONVERSATION_DELETE
- 知识库：KNOWLEDGE_BASE_UPLOAD, KNOWLEDGE_BASE_DELETE, KNOWLEDGE_BASE_REINDEX
- 工作流：WORKFLOW_CREATE, WORKFLOW_UPDATE, WORKFLOW_DELETE, WORKFLOW_EXECUTE
- 系统配置：SYSTEM_CONFIG_UPDATE, FEATURE_FLAG_TOGGLE
- CLI：CLI_DANGEROUS_OP
- 其他：OTHER

### 1.2 写入流程

```
业务代码调用 audit.log(action, user_id, ...)
    │
    ├─ 成功 → 写入 audit_logs 表 → 返回 AuditLog 对象
    │
    └─ 失败 → structlog.warning("审计日志写入 DB 失败，降级到 structlog", extra={...})
              → 返回 None（不抛异常，不阻塞业务）
```

### 1.3 Admin API

```
GET /api/admin/audit-logs
  ?action=user_login          # 按事件类型过滤
  &user_id=xxx                # 按操作者过滤
  &tenant_id=xxx              # 按租户过滤
  &resource_type=user         # 按资源类型过滤
  &resource_id=xxx            # 按资源 ID 过滤
  &since=2026-08-01T00:00:00  # 时间范围（起）
  &until=2026-08-25T23:59:59  # 时间范围（止）
  &page=1&page_size=50        # 分页
```

权限守卫：仅 `system_admin` / `system_viewer` 可访问。

## 2. 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| details 字段类型 | JSON（SQLModel Column） | 避免字段爆炸，灵活扩展上下文 |
| 写入失败策略 | structlog 降级，不抛异常 | 审计是辅助功能，不应阻塞业务 |
| API 权限 | system_admin + system_viewer | 符合 PRD §5.3.2 权限矩阵 |
| 排序方式 | created_at DESC | 最近事件优先 |
| 分页上限 | 200 条/页 | 防止大列表内存溢出 |
| 无 FastAPI 依赖 | AuditLogger 独立于 FastAPI | 可在 CLI / 定时任务中复用 |

## 3. 集成点

| 模块 | 集成方式 | 状态 |
|------|----------|:--:|
| `app/api/router.py` | 注册 audit.router | ✅ |
| `app/core/database.py` | init_db 导入 audit.models | ✅ |
| `app/core/config.py` | 新增 AUDIT_ENABLED / AUDIT_RETENTION_DAYS | ✅ |
| `app/services/auth_service.py` | 登录/登出时调用 audit.log() | 🔮 内核打磨 |
| `app/services/chat_service.py` | 对话创建/删除时调用 audit.log() | 🔮 内核打磨 |
| CLI | 危险操作时调用 audit.log() | 🔮 内核打磨 |

## 4. 测试覆盖

| 测试 | 说明 |
|------|------|
| Test 1 | AuditAction 枚举值正确性 |
| Test 2 | AuditLog 模型实例化 |
| Test 3 | AuditLogger 单例模式 |
| Test 4 | log() 写入 DB 并验证 |
| Test 5 | log() 字符串 action 支持 |
| Test 6 | log() 最小字段（None 安全） |
| Test 7 | cleanup_old_logs() 功能 |
| Test 8 | 表结构完整性 |
| Test 9 | 批量写入多条日志 |

## 5. 后续规划（内核打磨）

- [ ] 在 auth_service 中集成登录/登出审计
- [ ] 在 chat_service 中集成对话创建审计
- [ ] 在 CLI 中集成危险操作审计
- [ ] 批量写入缓冲（减少 DB 往返）
- [ ] 合规报表导出（CSV / JSON）
- [ ] 数据保留策略可视化配置
- [ ] 审计日志定时清理（cron 调度）