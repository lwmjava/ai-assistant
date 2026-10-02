# 实现说明：已注册接口出现在 OpenAPI 中

> 日期：2026-10-02
> 计划：`docs/plans/plan_d4.md` 的 REL-001
> 结果：进程里每个 `/api` 业务路由及其 HTTP 方法都出现在 OpenAPI 中，并且带有摘要或说明。README「API 端点」表里已有的方法加路径都能在这份文档里找到。`POST /api/chat/stream` 的响应媒体类型包含 `text/event-stream`。

## 完成的行为

覆盖测试从应用路由表收集路径以 `/api` 开头、且纳入文档的路由，跳过框架自动添加的 `HEAD` 和 `OPTIONS`。这些方法必须出现在 `app.openapi()` 里。任一 `/api` 路由若被排除在文档之外，测试失败。每个 `/api` 操作的摘要或说明至少有一项非空。

反向用例只比较一张假路由表：缺少 `GET /api/example` 时报告这一条，补上后不再报告。测试不会从正在运行的应用上拆路由。

README 表中一行写了多个方法时，每个方法单独核对。路径参数已改成路由上的真实名字，例如 `{user_id}`、`{tenant_id}`、`{conversation_id}`、`{document_id}`、`{workflow_id}`。表里没有、文档里有的路由没有被补进行里。

流式对话在文档的 200 响应中声明 `text/event-stream`。事件字段和实际返回的 SSE 内容没有改。配额用尽时仍返回 JSON。

## 代码位置

- `tests/test_openapi_coverage.py`
- `app/api/routes/chat.py` 的流式对话路由
- `README.md` 的「API 端点」

## 验证

解释器：conda 环境 `ai-assistant`，`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`（Python 3.12.0）。

- `python -m pytest tests/test_openapi_coverage.py -v`：4 passed，退出码 0。
- `python -m pytest tests/ -k openapi -q`：8 passed，2 skipped，513 deselected，退出码 0。
- `python -m ruff check tests/test_openapi_coverage.py app/api/routes/chat.py`：All checks passed，退出码 0。

未改 `.env`，未碰 `data/ai_assistant.db`。

## 没做的事

没有把 README 列成全部路由的第二份手册。没有改错误码信封、引用字段或 MCP 契约。没有把前端回退和静态资源写进文档。没有做备份演练、测试门禁记录或检索实验。
