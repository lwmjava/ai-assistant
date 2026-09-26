# ADM-001～ADM-004：用户、租户、系统状态与四页六态

> 状态：已实现
> 来源：`docs/plans/plan_remaining_delivery.md` 的 ADM-001～ADM-004；`tasks.yaml`
> 日期：2026-09-26
> 截止：2026-12-09
> 依赖：AUTH-002、TEN-001、TEN-003 已完成。ADM-004 依赖 ADM-001 与 ADM-003。

系统管理员能列出用户并改角色、停用用户，能改租户名称并停用租户，能看到数据库和向量库是否连通。用户、租户、审计、系统状态四页能区分加载、空、错误、无权限、成功和失败。

本文件写完后才改业务代码。

## 目标

1. 系统管理员分页看到用户，能修改角色，能停用用户。停用后该用户的访问和刷新都失败。不能停用自己，也不能改自己的角色。
2. 系统管理员能修改未停用租户的名称，能停用租户。停用后，当前就在该租户里的用户不能再访问。管理页能看到已停用租户，并显示租户编号。
3. 系统管理员打开系统状态页，看到数据库连通、向量库连通、应用版本和进程启动时间。
4. 上述四页补齐六态。无权限时页面说明原因，不跳去空白对话页。

## 现状

- `users.is_active` 和 `tenants.is_active` 已存在。`get_current_user` 会拒绝已停用用户，但不看租户是否停用。刷新会拒绝已停用用户，也不看租户。
- 用户页只能创建成员。租户页只能创建并列出未停用租户，列表不显示编号。
- `GET /api/health` 已探测数据库和向量库，但是公开接口，失败时返回 503。没有管理页。
- 审计页已有加载、空、错误和无权限说明，但路由在进入页面前就把非授权角色送回对话页，页内说明看不到。
- 审计枚举已有 `user_role_change`、`user_disable`、`tenant_update`、`tenant_deactivate`。不新增枚举。

## 已定的取舍

产品写用户被禁用后访问令牌立即失效。现有 `get_current_user` 已经拒绝 `is_active` 为假的用户，所以停用用户只要把这一列写成假，下一次请求就会 401，不必在每个请求上再核对 `token_version`。同时仍递增 `token_version`，使旧刷新令牌失效。改角色不递增 `token_version`。角色以数据库为准，同一条访问令牌的下一次请求就读到新角色。

不能停用自己，也不能修改自己的角色。避免唯一的系统管理员把自己锁在管理页外面。

把用户改成 `tenant_admin`、`member` 或 `viewer` 时，同步改他当前租户那一条成员关系的角色。这样租户管理员才能按已有规则发邀请码。改成 `system_admin` 或 `system_viewer` 时，不改成员关系上的角色。不改其他租户上的成员关系。

停用租户只递增「当前 `users.tenant_id` 就是该租户」的用户的 `token_version`。人在别的租户里时，不废除他那边的会话。他不能再切回已停用租户。

非系统管理员的新请求，若当前租户已停用，返回 403，文案「租户已停用」。这个判断必须放在 `get_current_user`，否则对话等接口仍会放行。该文件不在任务允许路径里，这是为满足「停用后不能继续访问」的最小例外。系统管理员不受这条拦截，避免他停用自己当前所在的租户后无法再打开管理页或切走。

不提供重新启用用户或租户。不物理删除。不统计在线人数或调用量。

租户页和用户页展示编号，管理员可以把租户编号抄到邀请页。邀请页本身仍手填编号，不在本批改成下拉框。

## 方案

### ADM-001 用户列表、改角色与停用

- `GET /api/admin/users`：仅系统管理员。查询参数 `page`（默认 1）、`page_size`（默认 20，最大 100）、可选 `username`（包含匹配）、`role`、`is_active`。响应 `{items, total, page, page_size}`。每条含用户编号、用户名、邮箱、角色、是否启用、租户编号、租户名称。含已停用用户。
- `PATCH /api/admin/users/{id}`：请求体只有 `role`。目标不存在 404。改自己 409「不能修改自己的角色」。角色非法 422。成功后 `/api/auth/me` 返回新角色。审计 `user_role_change`，细节含旧角色和新角色，不含密码。
- `POST /api/admin/users/{id}/disable`：`is_active` 改为假，`token_version` 加一。停用自己 409「不能停用自己」。已停用 409「用户已停用」。不存在 404。之后该用户访问 401，旧刷新失败。审计 `user_disable`。
- 其他角色调用以上接口 403。

### ADM-002 租户编辑与停用

- `PATCH /api/admin/tenants/{id}`：请求体只有 `name`。不存在 404。已停用 409「租户已停用」。与其他未停用租户重名 409「租户名称已存在」。审计 `tenant_update`。
- `POST /api/admin/tenants/{id}/deactivate`：把 `is_active` 改为假，并递增当前就在该租户的用户的 `token_version`。已停用 409。不存在 404。审计 `tenant_deactivate`。
- `GET /api/admin/tenants` 默认仍只返回未停用。`include_inactive=true` 时连已停用一起返回。创建成员的下拉仍只用未停用列表。
- 非系统管理员切到已停用租户得到 409，不改 `tenant_id` 和 `token_version`。刷新时当前租户已停用，且本人不是系统管理员，返回 403「租户已停用」。

### ADM-003 系统状态

- `GET /api/admin/system/status`：仅系统管理员，始终 200。字段为整体 `status`、`app`、`version`、`env`、`started_at`，以及 `checks.database.status`、`checks.vector_store.status` 和 `backend`。探测失败时对应项为 `error`，不返回 503，页面才能标出哪一项失败。
- 不返回在线人数、调用量或配额。公开的 `/api/health` 保持原样。

### ADM-004 四页六态

- 用户、租户、审计、系统状态：加载用骨架或「正在加载」；没有数据用空态；请求失败用错误态并可重试；角色不够时页内说明，不再静默送回对话。
- 改角色、停用用户、改名、停用租户：成功有一句结果，失败显示接口文案。停用用户和停用租户先确认。停用用户的确认写明将使该用户的令牌失效。停用租户的确认写明当前正在该租户中的成员将不能继续访问。
- 不改对话页和知识库页。

## 非目标

不物理删除用户或租户。不重新启用。不做租户管理员的成员管理页，不做租户设置页。不改邀请页的手填编号。不改访问令牌的 `token_version` 核对方式。不做监控大盘，不编造在线人数或调用量。不改检索基线，不改 `.env`。

## 验收

- 系统管理员能翻页看到用户，列表含用户编号和租户编号。改角色后同一访问令牌读到的角色已变。停用后该用户访问和旧刷新失败。停用自己、改自己的角色被拒绝。非系统管理员 403。
- 改名成功；未停用租户重名被拒绝。停用后该租户当前成员访问得到 403，管理列表仍能看到该租户和编号。
- 系统管理员能看到数据库、向量库、版本和启动时间。非系统管理员不能打开该接口。
- 四页能区分空、错误和无权限。破坏性操作有成功或失败反馈。前端类型检查与构建通过。

## 验证

- `pytest tests/test_admin_users.py tests/test_admin_tenant_lifecycle.py tests/test_admin_status.py tests/test_admin_members.py -q`
- 改过的 Python 文件执行 `ruff check`
- `cd frontend; npm run typecheck`
- `cd frontend; npm run build`
- 浏览器：系统管理员打开用户、租户、系统状态页，完成一次改角色或确认停用入口可见；用非管理员打开这些地址时看到无权限说明。

## 实现说明

谁可以做什么：

- 系统管理员 `GET /api/admin/users` 分页看到用户编号、用户名、角色、是否启用、租户名称和租户编号。`PATCH /api/admin/users/{id}` 只改别人的角色；改成租户管理员、成员或访客时，同步他当前租户的成员角色。`POST /api/admin/users/{id}/disable` 停用别人：该用户下一次访问 401，旧刷新失败。停用自己或改自己的角色得到 409。
- 系统管理员 `PATCH /api/admin/tenants/{id}` 修改未停用租户的名称，与其他未停用租户重名则 409。`POST /api/admin/tenants/{id}/deactivate` 停用租户，并废除当前就在该租户里的用户的刷新令牌。这些用户之后访问得到 403「租户已停用」。系统管理员不受这条拦截。列表默认仍只含未停用；`include_inactive=true` 时能看到已停用和编号。
- 系统管理员 `GET /api/admin/system/status` 看到数据库、向量库、版本、环境和本进程启动时间。探测失败时对应项为不通，接口仍返回 200。
- 用户、租户、审计、系统状态四页：加载、没有数据、请求失败、无权限都有说明。停用前要确认。成功或失败都有一句结果。

代码位置：`app/services/admin_users.py`、`app/services/tenant_admin.py`、`app/api/routes/admin_users.py`、`app/api/routes/admin_tenants.py`、`app/api/routes/admin_system.py`、`app/api/deps.py`、`frontend/src/pages/Users.tsx`、`frontend/src/pages/Tenants.tsx`、`frontend/src/pages/Status.tsx`。

验证：

- `python -m pytest tests/test_admin_users.py tests/test_admin_tenant_lifecycle.py tests/test_admin_status.py tests/test_admin_members.py -q`：10 项通过。
- `python -m ruff check` 针对改动的 Python 文件：通过。
- `cd frontend; npm run typecheck` 与 `npm run build`：退出码均为 0。
- 浏览器：系统管理员在用户页看到编号、角色和停用；自己的那一行没有停用。租户页显示编号、保存名称和停用。系统状态页为数据库连通、向量库连通 · local、版本 0.1.0。成员账号打开用户、租户、系统状态和审计时，看到对应的无权说明。

没做的事：重新启用用户或租户、物理删除、租户管理员的成员管理页、邀请页改成按名称选择、在线人数和调用量、监控大盘。核对时注册了成员 `admview`，留在开发库里。
