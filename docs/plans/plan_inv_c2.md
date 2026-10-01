# INV-001～INV-003：邀请码、切换租户与控制台入口

> 状态：已实现
> 来源：`docs/plans/plan_remaining_delivery.md` 的 INV-001～INV-003；`tasks.yaml`
> 日期：2026-09-26
> 截止：2026-11-28
> 依赖：AUTH-001 已完成。INV-002 依赖 INV-001。INV-003 依赖 INV-002。

已注册用户凭邀请码加入另一个租户。加入后可以换成那个租户的会话。控制台能发码、填码，并在属于多个租户时从顶栏切换。

本文件只定实现边界。未实现前不改业务代码。

## 目标

1. 增加成员表。一名用户可以属于多个租户，`users.tenant_id` 仍表示当前会话租户。系统角色仍记在用户上。
2. 租户管理员或系统管理员能为指定租户生成邀请码。已登录用户凭码加入。重复加入、过期、用尽都拒绝。创建和接受写入审计。
3. 已是目标租户成员时，可以换发该租户的令牌，并把 `users.tenant_id` 改成目标租户。不是成员则 403，用户和令牌都不变。
4. 页面能生成邀请码、输入邀请码，并在成员关系多于一条时从顶栏切换。切换后对话列表属于新租户。

## 现状

- `users.tenant_id` 是唯一归属。公开注册、初始化向导和系统管理员建成员都只写这一列。没有成员表，没有邀请码。
- 权限矩阵读的是 `users.role`。对话、知识库和工作流列表用 `current_user.tenant_id`。对话列表在 `ChatService.list_conversations`。
- 角色里有 `tenant_admin`，但创建成员的接口把角色固定为 `member`。当前控制台里的管理员是 `system_admin`。
- 刷新令牌用 `token_version` 判断是否撤销。访问令牌在过期前不核对 `token_version`。业务代码读的是数据库里的 `user.tenant_id`，不是令牌里的声明。
- 顶栏只有用户菜单、主题和连通性。没有邀请页。租户页只允许系统管理员创建和列出未停用租户。
- 审计枚举没有邀请动作。`app/audit/models.py` 不在这三条任务的允许路径里。
- INV-003 的允许路径只有前端。列表、创建、接受和切换接口必须在 INV-001、INV-002 做完。

## 已定的取舍

产品需求写「切换后旧令牌立即失效」。访问令牌要立即失效，就得在每次请求核对版本号，那会改到 `app/api/deps.py`，不在 INV-002 的允许路径里。本计划不改访问令牌的核对方式。

切换成功时递增 `token_version` 并返回新的访问令牌和刷新令牌。旧刷新令牌随后失败。旧访问令牌在过期前仍然可用；因为它读的是用户行上的租户，过期前看到的也是切换后的租户。失败时不改 `tenant_id`，也不递增 `token_version`。

产品需求写由租户管理员发邀请码。现在没有途径把用户设成 `tenant_admin`，只有系统管理员能进管理页。因此发码允许两类人：该租户成员关系角色为 `tenant_admin` 的用户，以及 `system_admin`。系统管理员必须在请求体里给出租户。不在本计划里增加「把某人设为租户管理员」的接口，那是后面的用户管理。

成员关系上的角色先记下来，不替换现有权限矩阵。邀请加入写入的角色固定为 `member`，且不修改 `users.role`。切换也不修改 `users.role`。系统管理员切到自己已加入的租户后，仍然是系统管理员。

## 方案

### INV-001 成员表与邀请码

新表 `memberships`：`user_id`、`tenant_id`、`role`。同一用户在同一租户只有一条。`role` 只使用 `tenant_admin`、`member`、`viewer`。

新表 `invitations`：邀请码、`tenant_id`、固定角色 `member`、创建者、过期时间、`max_uses`、`use_count`。邀请码用随机字符串，入库后管理员仍能再次看到。默认 7 天、`max_uses` 为 1。请求体不能带角色或次数。

启动时沿用现有的初始管理员引导，补齐缺失的成员关系：

- 用户角色是 `member`、`viewer` 或 `tenant_admin` 时，按该角色补当前 `tenant_id` 的一条。
- 用户角色是 `system_admin` 或 `system_viewer` 时，补一条 `member`。用户上的系统角色不变。

注册、初始化向导和系统管理员创建成员时，同时写入对应的成员关系，不等到下次启动。

接口：

- `POST /api/invitations`：创建。租户管理员只能给自己的当前租户发码。系统管理员按请求体中的租户发码，租户必须存在且未停用。其他角色 403。
- `GET /api/invitations`：同上的人列出该租户未删除的邀请码，响应里包含邀请码本身。
- `POST /api/invitations/accept`：已登录用户提交邀请码。成功后只增加成员关系，不改 `users.tenant_id`，不换发令牌。

失败：

- 邀请码不存在：404。
- 已过期：400，文案说明已过期。
- 次数用尽：409。
- 已经是该租户成员：409，文案说明已在租户中。接受不增加 `use_count`。
- 目标租户已停用：409。

审计使用已有的 `other`。细节里用 `invite_create` 或 `invite_accept`，并带邀请记录编号和租户编号。不写入邀请码明文。

### INV-002 切换租户

- `GET /api/auth/memberships`：返回当前用户的成员关系，含租户编号、租户名称和成员角色。
- `POST /api/auth/switch-tenant`：请求体只有 `tenant_id`。不接受查询参数。缺少请求体时 422。

是目标租户的成员：把 `users.tenant_id` 改为目标租户，`token_version` 加一，返回新的双令牌。`users.role` 不变。

不是成员：403。`tenant_id` 与 `token_version` 保持原值，不返回新令牌。系统管理员不会因此自动成为所有租户的成员。

切换后，`list_conversations` 已按 `user.tenant_id` 过滤，新租户的会话列表不再包含原租户、且属于该用户的会话。不改检索、切分和对话过滤条件。

### INV-003 页面

新增 `/invitations`。已登录用户都能打开。

- 系统管理员，或当前成员角色为 `tenant_admin` 时，显示生成邀请码和已有邀请码。
- 所有已登录用户都能输入邀请码并接受。成功后提示已加入，不自动切换。
- 成员关系多于一条时，顶栏提供租户选择。只有一条时不显示选择器。
- 切换成功后保存新令牌，并让对话列表重新请求。停留在当前页。对话列表应只含新租户的会话。

页面显示接口返回的错误文案。不实现 C3 的六态全集，也不发邮件。

## 非目标

不做邮件。不实现关闭自动加入。不把邀请码当成注册入口。不改 `users.role`。不把成员角色接入整份权限矩阵。不让系统管理员凭系统角色切换到未加入的租户。不改访问令牌的有效期，也不在每个请求上核对 `token_version`。不物理删除租户或用户。不做六态全集。不改检索基线，不改 `.env`。

## 验收

- 凭码加入后，该用户与目标租户的成员关系存在，当前 `tenant_id` 仍是加入前的租户。
- 重复加入、过期、用尽分别被拒绝。审计有 `invite_create` 与 `invite_accept`，细节里没有邀请码。
- 成员切换后，新访问令牌的租户是目标租户，对话列表属于新租户。旧刷新令牌失败。
- 非成员切换得到 403，`tenant_id` 与 `token_version` 不变。
- 前端类型检查与构建通过。页面能生成邀请码、接受邀请码，并在多条成员关系时完成切换。

## 验证

- `pytest tests/ -k "invite or member" -v`
- `pytest tests/ -k "switch or tenant" -v`
- 改过的 Python 文件执行 `ruff check`
- `cd frontend; npm run typecheck`
- `cd frontend; npm run build`
- 浏览器里用已有账号生成邀请码，用另一个已注册账号接受，再从顶栏切换，确认对话列表换成新租户。

## 实现说明

谁可以做什么：

- 系统管理员调用 `POST /api/invitations` 时必须带租户编号，可为未停用租户生成 7 天、单次、角色为 member 的邀请码，并用 `GET /api/invitations?tenant_id=` 再次看到邀请码。
- 当前租户成员关系角色为 `tenant_admin` 的用户只能给自己的当前租户发码和看码。其他角色发码得到 403。
- 已登录用户 `POST /api/invitations/accept` 凭码加入。成功后新增成员关系，不改 `users.tenant_id` 和 `users.role`。邀请码不存在为 404，过期为 400，用尽或租户停用为 409，已是成员为 409 且不增加使用次数。
- 已加入的用户 `POST /api/auth/switch-tenant` 换发新令牌，当前租户改为目标租户，`token_version` 加一，旧刷新令牌失效。不是成员则 403，租户和令牌版本不变。系统角色不变。
- 控制台 `/invitations` 可填码加入。能发码的人看到生成区和邀请码。成员关系多于一条时，顶栏可以选择租户；切换后对话列表按新租户重新请求。

现有用户在应用启动的初始管理员引导里补一条当前租户的成员关系。新注册、初始管理员和系统管理员创建的成员也会立刻写入。

代码位置：`app/models/membership.py`、`app/services/membership.py`、`app/services/invitations.py`、`app/api/routes/invitations.py`、`app/api/routes/auth.py`、`frontend/src/pages/Invitations.tsx`、`frontend/src/components/layout/TopBar.tsx`。

接受邀请后，页面会同时刷新成员关系和当前租户的邀请码列表，使用次数不用手动重载。

验证：

- `python -m pytest tests/test_invitations.py tests/test_switch_tenant.py tests/test_auth_register.py tests/test_admin_members.py -q`：13 项通过。
- `python -m ruff check` 针对改动的 Python 文件：通过。
- `cd frontend; npm run typecheck`：退出码 0。`npm run build`：退出码 0。
- 浏览器：系统管理员登录后，`/invitations` 有填码区和租户编号。只有一条成员关系时顶栏没有切换器。为核对新建了租户「邀请核对」和「邀请核对-次数」，生成邀请码后在同一页接受。文案为「已加入该租户。当前会话没有切换。」，使用次数从 0/1 变为 1/1，当前租户仍是 default，系统角色仍是 system_admin。成员关系多于一条后顶栏出现切换器。切到「邀请核对」后对话列表变为「还没有会话」；切回 default 后原会话重新出现。

没做的事：邮件、用邀请码注册新用户、把成员角色接入整份权限矩阵、把某人设为租户管理员、访问令牌在过期前立即失效、六态全集。核对时留在开发库里的两个租户没有删除。
