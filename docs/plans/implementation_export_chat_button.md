# 实现说明：对话页导出本租户对话

> 日期：2026-10-02
> 计划：`docs/plans/plan_export_chat_button.md`
> 结果：租户管理员和系统管理员能在对话页看到「导出本租户对话」。租户管理员点击后开始下载。系统管理员点击后看到无权导出。

## 完成的行为

按钮在对话页顶栏。`tenant_admin` 和 `system_admin` 可见。请求是带访问令牌的 `POST /api/export/conversations`，正文为空。成功时浏览器下载 `conversations.json`，按钮会短暂显示「正在导出」。403 时页面写明只有当前租户的租户管理员可以下载。413 写明导出内容过大、没有开始下载。503 写明审计没有写入、没有开始下载。提示里没有对话正文。

审计筛选项增加「导出对话」，对应 `export_requested`。导出 JSON、条数上限、字节上限和先写审计再返回文件的顺序没有改。

## 代码位置

- `frontend/src/api/export.ts`
- `frontend/src/lib/export-message.ts`
- `frontend/src/pages/Chat.tsx`
- `frontend/src/pages/Audit.tsx`

## 验证

`cd frontend; npm run typecheck` 退出码 0。

浏览器使用临时库 `data/pytest-tmp/limit-ui.db`。租户管理员点击后，临时接口日志为 `POST /api/export/conversations` 200，页面没有无权或失败提示。系统管理员点击后，页面出现「无权导出」。验证进程已停，没有改 `data/ai_assistant.db`。没有在页面上把导出内容堆到 5000 条或 32 MiB，413 和 503 仍以既有接口测试为准。

## 没做的事

不按单条会话筛选。不改训练格式。不在知识库页放导出。系统管理员不能下载。
