# 实现说明：租户消息条数与源文件配额

> 日期：2026-10-01
> 计划：`docs/plans/plan_d3.md` 的 QUOTA-001 节
> 结果：系统管理员可以设置租户的消息条数上限和源文件字节上限。超限时不新增用户消息，也不留下新的源文件。同一 SQLite 上的并发写入不超过上限。临时库降级后三列消失，已有对话和文档仍在。

## 完成的行为

系统管理员通过 `GET` 和 `PATCH /api/admin/tenants/{tenant_id}/quota` 读取和修改上限。两个上限都要出现在请求里，可以是空值。空值表示不限制，0 表示该维度不能再新增，负数得到 422。`reason` 去掉空白后必须有内容，最长 200 字。缺少原因或原因为空白时得到 422，上限不变。

修改上限和一条 `quota_update` 审计在同一个事务里提交。审计详情有操作者、目标租户、新旧两个上限和原因，没有对话正文。审计关闭，或审计对象写不进去时，返回 503，上限保持原值。这条路径不使用会吞掉异常的 `audit_event`。

消息用量是该租户现存的 `role=user` 消息条数。助手回复不计入。检查和写入用户消息在锁住该租户行的同一个事务里完成。已经等于上限时再发一条，会话数和用户消息数都不变。非流式和流式都在开始 SSE 之前返回 429，响应体就是：

```json
{"code":"quota_exceeded","limit_type":"messages","used":1,"limit":1}
```

`limit_type` 为 `source_bytes` 时结构相同。删除会话会级联删除消息，额度随之恢复。工作流执行走 `ChatService.chat`，同一套消息上限对它生效。上限为空，或请求里的租户在 `tenants` 表中没有行时，不限制，原有对话测试仍可完成。

源文件用量只统计还在磁盘上的源文件，同一相对路径只计一次。软删除不删除文件，用量不下降。文件被物理删除后不再计入。路径在但读取大小失败时，上传返回 503，不写新文件。单文件上传、批量上传，以及 URL 或带远程地址的重解析，都在 `save_source_file` 之前判断。批量会先把整批字节加总，超限则一个新文件都不写。纯文本摄取不写源文件，源文件上限为 0 时仍可摄取。向量和分块不计入。

未超限但解析或摄取失败时，删掉这一次新写的源文件。删除失败则请求失败，不报成功。文档行提交成功时写入 `source_bytes`，值等于当时的文件大小。

429 体没有包在 `detail` 里，也不含其他租户的信息。速率限制的 429 形状没有改。

已有库如果只是因为导入平台表存在就被标到 head，不会再跳过这次加列。缺列时会补上可空整数列。

## 代码位置

- 检查、锁和审计：`app/services/quota.py`
- 对话两处入口：`app/services/chat_service.py`；429 响应在 `app/api/routes/chat.py`
- 上传和批量：`app/api/routes/rag.py`
- URL 与远程重解析：`app/rag/import_jobs.py`
- 文档提交时写入字节数：`app/rag/service.py` 的 `_persist_document`
- 设置接口：`app/api/routes/admin_tenants.py`，契约在 `app/schemas/quota.py`
- 列：`app/models/user.py` 的 `Tenant`，`app/models/rag.py` 的 `Document`
- 迁移：`alembic/versions/e4b7a2c85d01_add_tenant_quota_columns.py`，父修订 `d7c2a91e4b18`
- 已有库补列：`app/core/migration.py` 的 `_ensure_quota_columns`，以及不再把缺配额列的库直接标到 head
- 审计动作：`app/audit/models.py` 的 `quota_update`
- 测试：`tests/test_quota.py`，降级探针 `tests/quota_downgrade_probe.py`

## 验证

解释器是 conda 环境 `ai-assistant`：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`（Python 3.12.0）。

```text
python -m pytest tests/test_quota.py -v --tb=line --basetemp data/pytest-tmp/quota-001c
10 passed in 7.90s
exit 0
```

```text
python -m ruff check app/services/quota.py app/api/routes/chat.py app/api/routes/rag.py app/api/routes/admin_tenants.py app/rag/import_jobs.py app/rag/service.py app/schemas/quota.py app/models/user.py app/models/rag.py app/audit/models.py app/core/migration.py alembic/versions/e4b7a2c85d01_add_tenant_quota_columns.py tests/test_quota.py tests/quota_downgrade_probe.py
All checks passed!
exit 0
```

同一解释器下，`tests/test_chat.py` 与 `tests/test_rag.py` 里名称含 upload、ingest、chat_returns、empty_message、conversation_lifecycle 的用例，加上纯文本零上限这条，共 18 passed，34 deselected，退出码 0。临时目录是 `data/pytest-tmp/quota-reg`。

`ruff check app/` 退出码 1，共 56 条。其中对话服务里 6 条 `F821` 在改动前的同一文件里已经存在。其余报错在本次未改的模块。没有用 `--fix` 去改那些文件。

降级用例在子进程里使用自己的临时 SQLite，没有降级 `data/test_ai_assistant.db` 或 `data/ai_assistant.db`。测试库在收集阶段曾被标到新修订但当时缺列；下一次启动补上了这三列。

## 没做的事

- 没有计费、用户级配额、Token 计量或按月重置
- 没有改租户页，也没有做物理删除产品功能或后台清扫
- 没有给纯文本摄取或向量索引设闸
- 没有做多副本锁，也没有做 PostgreSQL 演练
- 没有实现限流提示或对话导出
- 没有跑全量 pytest、`mypy app/` 或前端构建
- 没有用浏览器验证；本批没有页面改动
