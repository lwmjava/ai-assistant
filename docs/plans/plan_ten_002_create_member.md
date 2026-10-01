# TEN-002：在指定租户下创建成员

> 状态：已实现（TEN-002 `done`）
> 来源：交付排期 B2；`tasks.yaml` TEN-002；`docs/plans/plan_b2_tenants.md`
> 日期：2026-09-25
> 截止：2026-11-07
> 依赖：TEN-001 已完成

系统管理员可以向一个已有且未停用的租户创建成员。该成员登录后，令牌里的租户就是这个租户，列会话和知识库时看不到别的租户。

## 已完成的行为

`POST /api/admin/tenants/{tenant_id}/users` 只对 `system_admin` 开放。请求体是 `username`、`password`、可选 `email`，多传 `role` 会返回 422。创建出的用户角色固定为 `member`。

租户不存在返回 404。租户已停用、用户名已占用、邮箱已占用返回 409。成功返回 201 和 `UserInfo`，正文没有密码字段。审计 `user_create` 只记用户名、租户 ID 和角色。

成员登录后的 access token 含所属 `tenant_id` 与 `role=member`。用该令牌调用 `GET /api/chat/conversations` 和 `GET /api/rag/documents`，结果里没有另一租户的会话或文档。

代码在 `app/api/routes/admin_tenants.py`、`app/services/tenant_admin.py` 的 `create_member`、`app/schemas/tenant.py` 的 `MemberCreate`。测试是 `tests/test_admin_members.py`。

`pytest tests/test_admin_members.py tests/test_admin_tenants.py`：7 项通过。上述文件的 `ruff check` 通过。

## 目标

1. 只有 `system_admin` 能在指定租户下创建用户，角色固定为 `member`。
2. 用户名全局唯一，密码只存哈希。响应和审计不出现明文密码或哈希。
3. 该用户登录后，access token 的 `tenant_id` 等于所属租户。
4. 用这个成员身份列会话、列知识库时，看不到另一租户的数据。

## 现状

- `create_user` 已能在指定 `tenant_id` 下写入用户并哈希密码，但没有 HTTP 接口。见 `app/services/auth_service.py`。
- 用户通过 `users.tenant_id` 归属一个租户。用户名、邮箱在表上全局唯一。见 `app/models/user.py`。
- 登录签发的 access token 带有该用户的 `tenant_id` 和角色。见 `app/api/routes/auth.py`、`app/core/security.py`。
- 会话列表按 `user.tenant_id` 过滤；非系统管理员还要匹配 `user_id`。见 `app/services/chat_service.py`。
- 知识库列表：`member` 只看自己租户、自己上传的当前版。`system_admin` 的列表不按租户过滤。见 `app/rag/service.py` 的 `list_documents`。因此跨租户断言必须用新成员的令牌，不能用系统管理员的令牌。
- 审计动作已有 `user_create`。创建租户的管理员校验在 `app/api/routes/admin_tenants.py`，只放行 `system_admin`。
- `UserCreate` 允许调用方传入任意角色。本任务不能把这个字段直接暴露给管理接口，否则可以借此创建 `system_admin`。

## 方案

新增 `POST /api/admin/tenants/{tenant_id}/users`。沿用租户接口的系统管理员校验。

请求体：`username`、`password`（至少 8 位）、可选 `email`。不接受 `role`。服务层固定写入 `member`。

拒绝：

| 情况 | 结果 |
|---|---|
| 非系统管理员 | 403 |
| 租户不存在 | 404 |
| 租户已停用 | 409 |
| 用户名已被占用 | 409 |
| 邮箱已被占用 | 409 |

成功返回 `UserInfo`（`id`、`tenant_id`、`username`、`email`、`role`、`is_active`），状态 201。不返回 `hashed_password`。

审计 `user_create`：资源类型 `user`，资源 ID 为新用户 ID，详情只记用户名、租户 ID 和角色 `member`。

不改会话和知识库的现有过滤。测试里先在租户 A、B 各放一条会话或文档，再用租户 A 的新成员列接口，断言结果里没有租户 B 的记录。

允许路径：`app/api/routes/`、`app/services/`、`app/schemas/`、`tests/`、`docs/plans/plan_b2_tenants.md`、`docs/plans/plan_ten_002_create_member.md`、`tasks.yaml`、`AGENTS.md`、`README.md`（接口表）。

## 非目标

不实现注册、`/setup`、邀请码、`switch_tenant`。不实现 `tenant_admin` 的成员管理。不停用用户，不递增 `token_version`。不改成多租户归属。不做控制台页面。不改 `system_admin` 知识库列表的跨租户行为。

## 验收

- 成员可登录，解码后的 access token 中 `tenant_id` 等于所属租户，`role` 为 `member`。
- 该成员列会话、列知识库时看不到另一租户的数据。
- 响应正文和审计 `details` 不含明文密码或密码哈希。
- 向不存在的租户创建返回 404，向已停用租户创建返回 409。
- `pytest tests/test_admin_tenants.py` 及本任务新增用例通过，`ruff check` 通过。

## 风险与回滚

L1。回滚时去掉该创建接口。已写入的用户行留在 `users` 表，不随代码回滚删除。
