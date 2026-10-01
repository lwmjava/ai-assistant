# 实现说明：租户与用户列表页

> 日期：2026-10-01
> 计划：`docs/plans/plan_admin_list_pages.md`
> 结果：系统管理员可以在租户和用户列表上筛选、分页，从新增页创建，并在行末查看详情、修改、停用或启用。租户行可以设置消息条数和源文件上限。

## 完成的行为

租户列表按名称和状态筛选，每页 20 条，分页在页面内完成。现有 `GET /api/admin/tenants` 仍一次返回列表，技能页和新增用户的租户下拉不会被截断。筛选下面左侧是「新增租户」，进入 `/tenants/new`。创建成功后回到列表并提示。每一行末尾有详情、修改、配额、停用或启用。

详情显示名称、编号、状态和两个上限。修改只改名称；已停用时保存按钮不可用，并说明须先启用。配额弹窗读取现有上限。两个上限留空表示不限制，`0` 表示不能再新增，原因必填。保存走原来的配额接口。

用户列表按用户名、角色、状态和租户做服务端筛选，每页 20 条。「新增用户」进入 `/users/new`，只能在未停用租户下创建成员。行末有详情、修改、停用或启用。修改只改角色。当前登录的管理员不能修改或停用自己，这两个按钮不可用。

`POST /api/admin/tenants/{tenant_id}/activate` 重新启用租户。已经启用返回 409。已有同名未停用租户时返回 409，状态不变。启用不恢复停用时作废的令牌。`POST /api/admin/users/{user_id}/enable` 重新启用用户。非管理员返回 403。再次启用返回 409。旧刷新令牌仍然无效，用原密码重新登录成功。列表增加 `tenant_id` 查询参数。

## 代码位置

- 启用租户：`app/services/tenant_admin.py`，`app/api/routes/admin_tenants.py`
- 启用用户和按租户筛选：`app/services/admin_users.py`，`app/api/routes/admin_users.py`
- 列表与弹窗：`frontend/src/pages/Tenants.tsx`，`frontend/src/pages/Users.tsx`
- 新增页：`frontend/src/pages/TenantCreate.tsx`，`frontend/src/pages/UserCreate.tsx`
- 路由：`frontend/src/App.tsx`
- 分页：`frontend/src/components/ui/Pager.tsx`
- 接口封装：`frontend/src/api/tenants.ts`，`frontend/src/api/users.ts`

## 验证

解释器是 conda 环境 `ai-assistant`：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`。

```text
python -m pytest tests/test_admin_tenant_lifecycle.py tests/test_admin_users.py -q --tb=line --basetemp data/pytest-tmp/admin-list-b
7 passed in 15.27s
exit 0
```

```text
python -m ruff check app/services/tenant_admin.py app/services/admin_users.py app/api/routes/admin_tenants.py app/api/routes/admin_users.py tests/test_admin_tenant_lifecycle.py tests/test_admin_users.py
All checks passed
exit 0
```

```text
cd frontend
npm run typecheck
exit 0
```

浏览器使用临时库 `data/pytest-tmp/ui-admin-list.db`，接口在 `127.0.0.1:8011`，页面在 `127.0.0.1:5173`。没有改正在 `8000` 上运行的服务，也没有改 `data/ai_assistant.db`。验证结束后这两个临时进程已停止。

页面上确认：新增租户后列表出现「已创建 验收租户」；按名称筛到这一行；配额保存为 3 条和 1024 字节，详情能读回；停用后按钮变为「启用」，并出现确认启用对话框。用户列表能按角色、状态、租户筛选，当前管理员的修改和停用不可用，详情显示用户名、角色和租户。新增用户页只列出未停用租户。

## 没做的事

- 没有在页面上提交新用户，也没有在页面上点下「确认启用」
- 没有改对话页和知识库页的超限提示
- 没有展示已用条数或已用字节
- 没有跑全量 pytest 或 `mypy app/`
