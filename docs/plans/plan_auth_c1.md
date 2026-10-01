# AUTH-001～AUTH-004：注册、撤销令牌、首次向导与 README

> 状态：已实现
> 来源：`docs/plans/plan_remaining_delivery.md` 的 AUTH-001～AUTH-004；`tasks.yaml`
> 日期：2026-09-26
> 截止：2026-11-21
> 依赖：TEN-003 已完成。AUTH-002、AUTH-003 依赖 AUTH-001。AUTH-004 依赖 AUTH-003。

公开注册进入默认租户。系统管理员可以让某用户已发出的刷新令牌失效。没有系统管理员时，控制台进入一次性 `/setup`。README 的第一次使用按这些页面来写。

## 目标

1. `POST /api/auth/register` 创建默认租户下的 member，并返回当前租户的访问令牌和刷新令牌。用户名全局唯一。响应里没有密码。
2. 系统管理员 `POST /api/auth/users/{user_id}/revoke-tokens` 将该用户的 `token_version` 加一。旧刷新令牌失败，重新登录成功。非管理员 403。操作写入审计。
3. 没有 `system_admin` 时，控制台进入 `/setup` 并创建首个系统管理员。已有系统管理员时，向导接口返回 404，页面不再进入向导。注册页注册成功后进入对话。
4. README 按填写环境、启动、建管理员、注册或登录、发送第一条消息书写，每步写明失败时看哪里。

## 现状

- 认证路由只有登录、刷新和当前用户。`create_user` 已能按租户建用户，没有公开注册。
- 名为 `default` 的租户只在环境变量引导首个管理员时创建。变量是 `INITIAL_ADMIN_USERNAME` 与 `INITIAL_ADMIN_PASSWORD`。
- 刷新令牌载荷里的 `tv` 会和用户的 `token_version` 比较。没有递增它的管理接口。访问令牌在过期前不核对 `token_version`。
- 控制台只有 `/login`。
- README 快速开始停在启动和打开文档，没有注册、向导和第一条消息。

## 方案

1. 注册与向导都先取名为 `default` 且仍启用的租户，没有就创建。该租户已停用时返回 409，不另建同名租户。注册角色固定为 member。向导只在库中没有任何 `system_admin` 时创建系统管理员，成功后返回令牌。请求体禁止额外字段，不能借此提交角色。用户名或邮箱已占用时返回 409。密码至少 8 位。审计使用已有的 `user_create`，细节里不写密码。
2. 撤销放在现有认证路由上，避免新增未登记的路由模块。只允许系统管理员。用户不存在返回 404。审计使用已有的 `user_update`，细节里写 `revoke_tokens`。不改访问令牌的 15 分钟有效期，因此已发出的访问令牌在过期前仍然可用。
3. `GET /api/auth/setup-status` 公开返回 `needs_setup`。控制台在未登录时先问这个接口：需要初始化则进入 `/setup`，否则 `/setup` 回到登录。注册页在 `/register`。两处成功后都保存令牌并进入 `/chat`。
4. README 的本地第一次使用改成上述顺序。不写 `ai-assistant init`。Docker 段仍只负责把服务拉起来，第一次建号指向同一套页面。

## 非目标

不做邀请码，不建成员表，不实现关闭自动加入，不实现 CLI，不强制首次登录改密，不停用用户，不把撤销按钮放进用户管理页。不改 `.env`。不改检索基线。

## 验收

- 注册后的访问令牌租户是 `default`，角色是 member；重复用户名 409；响应和审计细节都不含明文密码。
- 撤销后旧刷新令牌 401，新登录 200；成员调用撤销 403；审计有 `revoke_tokens`。
- 空库 `needs_setup` 为真，向导可创建管理员；再次调用向导为 404，`needs_setup` 为假。
- 前端类型检查与构建通过。已有管理员时打开 `/setup` 进不了向导。
- README 中的 `/login`、`/register`、`/setup`、`/chat` 在控制台路由里存在。

## 实现说明

谁可以做什么：

- 任何人可以 `POST /api/auth/register`。成功后成为名为 `default` 的租户的 member，响应是访问令牌和刷新令牌。用户名或邮箱已占用时 409。请求里带角色时 422。默认租户已停用时 409。
- 任何人可以 `GET /api/auth/setup-status`。没有 `system_admin` 时 `needs_setup` 为 true。
- 没有系统管理员时，`POST /api/auth/setup` 在 `default` 租户创建首个系统管理员并返回令牌。已有系统管理员时 404，页面打开 `/setup` 会离开向导。
- 系统管理员可以 `POST /api/auth/users/{user_id}/revoke-tokens`。该用户的 `token_version` 加一，旧刷新令牌 401，重新登录后的新刷新令牌仍可刷新。成员调用返回 403。用户不存在返回 404。已发出的访问令牌在过期前仍然可用。
- 控制台未登录且需要初始化时进入 `/setup`。`/register` 注册成功后进入 `/chat`。`/login` 有注册入口。

代码位置：`app/services/auth_service.py`、`app/schemas/auth.py`、`app/api/routes/auth.py`、`frontend/src/App.tsx`、`frontend/src/pages/Register.tsx`、`frontend/src/pages/Setup.tsx`、`README.md`。

验证：

- `python -m pytest tests/test_auth_register.py tests/test_auth_revoke.py tests/test_api.py::TestRefreshToken -q`：12 passed。同一批登录、刷新和成员创建用例在修正前已通过，失败的只有撤销审计细节的读取，已改成按 JSON 字符串解析后纳入这 12 项。
- `python -m ruff check` 针对改过的认证文件：通过。
- `cd frontend; npm run typecheck`：退出码 0。`npm run build`：退出码 0。
- 运行中的 API `GET /api/auth/setup-status` 返回 `{"needs_setup":false}`。浏览器打开 `http://localhost:5173/register` 能看到注册表单；打开 `/setup` 后地址变为 `/chat`。已登录会话未退出，因此没有在页面上再走一遍空库向导。

没做的事：邀请码、成员表、关闭自动加入、CLI、首次登录改密、停用用户、用户页上的撤销按钮。空库向导只在测试库验证，没有对当前开发库执行。访问令牌过期前不会因为撤销而立即失效。
