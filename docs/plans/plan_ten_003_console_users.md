# TEN-003：控制台租户页与用户页

> 状态：已实现（TEN-003 `done`）
> 来源：交付排期 B2；`tasks.yaml` TEN-003；`docs/plans/plan_b2_tenants.md`
> 日期：2026-09-25
> 截止：2026-11-07
> 依赖：TEN-002 已完成

系统管理员可以在控制台创建租户，再在选定租户下创建成员。成员的导航里没有这两页，直接打开地址会回到对话页。

## 已完成的行为

侧边栏对 `system_admin` 显示「租户」和「用户」。`/tenants` 列出未停用租户，提交名称后调用 `POST /api/admin/tenants`，成功后列表出现新租户。`/users` 用同一列表做下拉，提交用户名和密码后调用 `POST /api/admin/tenants/{id}/users`，成功后显示刚创建的用户名。失败时在表单旁显示后端说明。

其他已登录角色看不到这两个入口。直接打开 `/tenants` 或 `/users` 会回到 `/chat`。

浏览器里用系统管理员创建了租户「验收租户0925」和成员 `ada0925`。该成员登录后侧边栏没有这两项，直接访问两个地址都回到对话页。`npm run typecheck` 与 `npm run build` 通过。

## 目标

1. `system_admin` 在租户页看到未停用租户，并能新建一个。
2. `system_admin` 在用户页从这些租户里选一个，创建 `member`。
3. `member` 侧边栏不出现这两个入口；直接访问路由时进不了页面。

## 现状

- 控制台路由只有登录、对话、知识库、工具、工作流、审计。见 `frontend/src/App.tsx`。
- 侧边栏按 `frontend/src/lib/permissions.ts` 过滤入口。该文件里 `tenants` 的读权限包含 `system_viewer` 和 `tenant_admin`，写权限才只有 `system_admin`。
- 后端 `GET/POST /api/admin/tenants` 与 `POST /api/admin/tenants/{id}/users` 都只允许 `system_admin`。其他角色返回 403。见 `app/api/routes/admin_tenants.py`。
- 没有列出某租户成员的接口。用户页只需要创建，不需要成员列表。
- 无权页面目前用 `RequirePermission` 重定向到 `/chat`，没有单独的 403 页。六态全集属于 C3。

## 方案

新增两个页面，沿用现有布局，不改对话和知识库。

| 路由 | 谁能进 | 做什么 |
|---|---|---|
| `/tenants` | 仅 `system_admin` | 列出 `GET /api/admin/tenants`；表单提交名称，调用 `POST /api/admin/tenants` |
| `/users` | 仅 `system_admin` | 用同一列表做租户下拉；提交用户名、密码、可选邮箱，调用 `POST /api/admin/tenants/{id}/users` |

侧边栏增加「租户」「用户」两项，显示条件是 `role === 'system_admin'`。不用 `can(role, 'tenants', 'read')`，否则 `tenant_admin` 和 `system_viewer` 会看到入口，点进去却被接口拒绝。

路由守卫同样只放行 `system_admin`。其他已登录角色访问这两个地址时，重定向到 `/chat`，与现有无权路由一致。

创建成功后刷新租户列表，或在用户页显示刚创建的用户名。失败时在表单旁显示后端返回的说明（重名、停用、校验失败）。不做加载骨架、空态插画、独立 403 页和成功动画。

请求走现有 `frontend/src/lib/http.ts`。不新增列出成员的接口，不改权限矩阵里其他资源。

允许路径：`frontend/src/`、`docs/plans/plan_b2_tenants.md`、`docs/plans/plan_ten_003_console_users.md`、`tasks.yaml`、`AGENTS.md`。

## 非目标

不做 C3 的加载、空、错误、无权限、成功、失败六态全集。不改审计页和系统状态页。不改对话、知识库的业务行为。不停用租户，不修改角色，不停用成员。不实现邀请码和切换租户。

## 验收

- 系统管理员能在租户页创建租户并在列表中看到它，再在用户页选中该租户并创建成员。
- 成员登录后侧边栏没有「租户」「用户」。直接打开 `/tenants` 或 `/users` 会离开这两个页面。
- `cd frontend; npm run typecheck` 与 `npm run build` 通过。
- 在浏览器里走完「建租户 → 建成员」，并换成员账号确认入口不可见。

## 风险与回滚

L1。回滚时去掉这两个路由、页面和侧边栏入口。已创建的租户和用户留在数据库里。
