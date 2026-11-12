# 反思候选的查询与生效

当进化总开关和对话反思开关都打开时，一轮对话结束后的反思如果提取到技能建议或待办，会写成候选记录，状态为待审。默认关闭时不会生成候选，也不能把默认关闭当成已经完成。夜间蒸馏若提取到技能建议，同样只写成候选；因蒸馏可能跨租户汇总，这类不带租户的候选仅系统管理员可查阅。

候选本身不会改技能清单，也不会自动创建工作流或上线技能。

## 查询

- `GET /api/v1/evolution/candidates`
- `GET /api/v1/evolution/candidates/{id}`

可按 `kind`（`skill` / `todo`）、`status`、`conversation_id` 过滤。成员只能看到本租户且属于自己会话的候选，租户管理员可看到本租户候选。

## 审核路径

1. 拒绝候选：`POST /api/v1/evolution/candidates/{id}/reject`。记录被拒绝，不产生技能或待办副作用。
2. 接受待办：`POST /api/v1/evolution/candidates/{id}/accept`。只把待办标记为已接受，便于跟进；不会自动创建工作流或改系统行为。
3. 技能候选转草稿：`POST /api/v1/evolution/candidates/{id}/promote`。会创建一条未启用的技能草稿（`lifecycle=draft`，`enabled=false`），不会进入对话技能清单。
4. 草稿生效仍走既有接口：`POST /api/v1/skills/{skill_id}/confirm`。只有这一步之后，技能才会启用并参与对话。

未完成第 4 步之前，任何候选或草稿都不应被视为已上线。回滚时关闭进化开关，并停用误确认的技能版本。