# TEN-001：系统管理员创建并列出租户

> 状态：已实现（TEN-001 `done`）
> 来源：交付排期 B2；`tasks.yaml` TEN-001
> 日期：2026-09-25
> 截止：2026-11-07
> 依赖：INST-003 已完成

系统管理员可以新建租户，并看到尚未停用的租户。其他角色不能调用这两个接口。

## 目标

1. `system_admin` 可以创建租户，并列出未停用租户。
2. 未停用租户的名称唯一。已停用的同名租户不挡住新建，也不出现在列表里。
3. 创建写入 `tenant_create` 审计。`tenant_admin`、`member`、`system_viewer` 返回 403。

## 非目标

不创建用户，那是 TEN-002。不停用或编辑租户，那是 C3。不改 `users` / `tenants` 表结构。不做控制台页面，那是 TEN-003。不把列表开放给权限矩阵里可以读 `tenants` 的 `system_viewer` 和 `tenant_admin`；本接口的列表与创建一样，只给系统管理员。

## 行为

| 方法 | 路径 | 谁可以调用 | 结果 |
|---|---|---|---|
| `POST` | `/api/admin/tenants` | `system_admin` | 201，正文为 `id`、`name`、`is_active` |
| `GET` | `/api/admin/tenants` | `system_admin` | 200，未停用租户，按名称排序 |
| 两者 | 同上 | 其他已登录角色 | 403，`仅系统管理员可管理租户` |
| `POST` | `/api/admin/tenants` | `system_admin`，未停用租户已有同名 | 409，`租户名称已存在` |

请求体只有 `name`。首尾空白会去掉；去掉后为空则 422。创建成功后审计资源类型为 `tenant`，资源 ID 为新租户 ID，详情只记名称，不记密码或其他用户字段。

名称唯一只比较未停用记录。已停用租户可以与新建租户同名。列表不返回 `is_active=false` 的行。

## 代码位置

- 路由：`app/api/routes/admin_tenants.py`，在 `app/api/router.py` 挂到 `/api`。
- 规则：`app/services/tenant_admin.py` 的 `create_tenant`、`list_active_tenants`。
- 契约：`app/schemas/tenant.py` 的 `TenantCreate`、`TenantOut`。
- 测试：`tests/test_admin_tenants.py`。

## 验收

`pytest tests/test_admin_tenants.py -v`：4 项通过（非管理员 403、创建后列表可见且有审计、重名 409、已停用同名可再建且列表只含未停用）。

`ruff check` 覆盖上述路由、服务、契约、路由聚合和测试文件，通过。

## 风险与回滚

L1。回滚时去掉 `/api/admin/tenants` 路由及 `tenant_admin` 服务。已写入的租户行留在 `tenants` 表，不随代码回滚删除。
