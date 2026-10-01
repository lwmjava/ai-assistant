# 实现说明：导出租户对话

> 日期：2026-10-01
> 计划：`docs/plans/plan_d3.md` 的 EXPORT-001 节
> 结果：当前租户成员关系上的租户管理员可以下载本租户对话原文。其他角色得到 403。审计写入成功之后才开始返回文件。超过条数或字节上限时不返回半份 JSON。

## 完成的行为

`POST /api/export/conversations` 只导出令牌上的租户。权限看该用户在这个租户的成员关系，角色必须是 `tenant_admin`。不看 `users.role`。成员、viewer、系统管理员，以及没有这条成员关系的人，得到 403，不写 `export_requested`。

用户在甲租户是租户管理员、在乙租户只是 member 时，`switch_tenant` 只改当前租户，不改 `users.role`。切到乙之后再导出，得到 403。响应和审计里都没有乙的对话。

请求体不用来选择租户。若仍传入 `tenant_id` 且与令牌不一致，返回 403，不导出。

成功时的 JSON 固定为 `tenant_id`、`exported_at`、`conversations[]`。每条会话有 `id`、`title`、`messages[]`，消息有 `role`、`content`、`created_at`。会话和消息按创建时间、主键排序，并按页读取。文件以附件 `conversations.json` 返回，内容是消息原文。

单次最多 5000 条消息，正文累计最多 32 MiB。超过条数、累计字节，或单条消息本身超过 32 MiB，返回 413，正文是 `{"code":"export_too_large"}`。不写成功审计，不返回已经读到的半份 JSON，日志不写消息正文。

未超限时先提交 `export_requested`。详情有操作者、租户、会话数和消息数，没有消息正文。这条记录表示服务端开始返回，不表示客户端已经收完。审计关闭或写入失败时返回 503，正文是 `{"code":"export_audit_failed"}`，里面没有对话。

## 代码位置

- 组装、上限和审计：`app/services/export_service.py`
- 路由：`app/api/routes/export.py`，挂在 `app/api/router.py`
- 审计动作：`app/audit/models.py` 的 `export_requested`
- 测试：`tests/test_export_conversations.py`

## 验证

解释器是 conda 环境 `ai-assistant`：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`（Python 3.12.0）。

```text
python -m pytest tests/test_export_conversations.py -q --tb=line --basetemp data/pytest-tmp/export-001d
6 passed in 5.56s
exit 0
```

同一套测试在 `data/pytest-tmp/export-001c` 也已通过。超限用例把条数或字节上限临时改小，避免往共享测试库写入 5001 条或 32 MiB 正文。代码里的默认上限另有断言：5000 条、32 MiB。拒绝响应和日志都不含用例里的正文标记。

```text
python -m ruff check app/services/export_service.py app/api/routes/export.py app/api/router.py app/audit/models.py tests/test_export_conversations.py
All checks passed!
exit 0
```

`ruff check app/` 退出码 1，共 56 条，都在本次未改的模块。没有用 `--fix` 去改那些文件。

测试使用 `data/test_ai_assistant.db`，没有使用 `data/ai_assistant.db`。

## 没做的事

- 没有训练格式，没有 Alpaca 或 ShareGPT
- 没有按时间或会话筛选，没有脱敏改写
- 没有导出页面，也没有做成 GET
- 没有记录客户端下载完成
- 没有跑全量 pytest、`mypy app/` 或前端构建
- 没有用浏览器验证；本批没有页面改动
