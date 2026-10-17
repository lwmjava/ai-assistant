# 实现说明：技能管理页

> 日期：2026-10-01
> 计划：`docs/plans/plan_d2.md` 的 SKILL-002 节
> 结果：技能页是通栏表格。筛选和新增在表格上方，详情、修改、启用、停用和删除在每行最后一列。

## 完成的行为

1. 侧栏「技能」进入 `/skills`。有读权限的角色都能打开。
2. 表格上方可以按名称、范围和状态筛选。系统管理员用租户下拉筛选。系统只读角色填写租户编号，因为租户列表接口不对这个角色开放。
3. 「新增技能」在表格上方，只对有写权限的人显示。创建和修改走弹窗，不把整张表单堆在列表上面。
4. 每行最后一列是操作。内置技能只有详情。创建者可以修改、启用或停用、删除自己的私有技能。租户管理员可以启用或停用本租户私有技能。系统管理员对私有技能和系统全局技能都有修改、启用、停用和删除。系统管理员新增时可以选择「系统全局」。
5. 表格不展示约束、技能说明和示例。详情弹窗才展示。
6. 访客看不到新增和写操作。用户页和租户页没有改。

## 代码位置

- `frontend/src/pages/Skills.tsx`
- `frontend/src/api/skills.ts`
- `frontend/src/lib/permissions.ts`
- `frontend/src/App.tsx`、`frontend/src/components/layout/Sidebar.tsx`

## 验证

```text
cd frontend
npm run typecheck
npm run build
```

两条命令退出码都是 0。

## 没做的事

- 浏览器打开了登录页。继续填写测试账号密码时被拦住，所以没有在页面上点完筛选、新增和行末操作。接口行为由 `tests/test_skill_selection.py` 覆盖。`npm run typecheck` 和 `npm run build` 已通过。
- 没有新增前端测试运行器。
- 没有重排用户管理和租户管理。
