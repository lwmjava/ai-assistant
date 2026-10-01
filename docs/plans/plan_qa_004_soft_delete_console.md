# QA-004：知识库上传、状态、重建、删除与软删除一致

> 状态：已实现（`tasks.yaml` QA-004 `done`）
> 来源：交付排期 B3；`docs/plans/plan_remaining_delivery.md` 的 QA-004
> 日期：2026-09-26
> 截止：2026-11-13
> 依赖：QA-001 已完成

知识库页的上传、状态、重建和删除与已经落地的软删除一致。删除后成员看不到，对话也不再命中。管理员仍能看到已删除标记。重建不会把已删文档救回当前版。

## 目标

1. 上传或摄取成功后，文档出现在列表里，并显示版本状态和分块数。失败只提示原因，不当成已入库。
2. 有写入权限的人可以对未删除文档发起重建。重建使用已有的重解析任务。进行中能看到「重建中」，结束后提示成功或失败。
3. 删除确认说明软删除的结果：成员列表和对话检索不再包含它，管理员打开「显示已删除」仍能看到「已删除」。已删除的行不再提供删除和重建。
4. 已删除文档不能新建重解析任务。已经排队的任务在执行时若发现目标已删除，任务失败，不清除 `deleted_at`，也不把 `is_current` 改回 true。

## 现状

- 同步上传和摄取会返回文档并刷新列表。行上已有版本徽章和分块数。
- 删除写入 `deleted_at`。成员列表默认不含已删文档。管理员有「显示已删除」。检索已排除已删文档。
- 删除确认写成不可撤销，并说会移除分块和向量。已删除的行仍显示删除按钮。
- 重解析接口已存在，页面没有重建。创建任务时已拒绝已删文档。任务若在删除前已经排队，执行时仍会就地替换分块。

## 方案

1. 页面改正删除确认文案。已删除的文档不显示删除和重建。未删除且有写权限时显示重建，确认后调用重解析，并轮询任务状态。上传和摄取成功的提示带上版本状态。不放宽文件类型。
2. 重解析执行前如果目标已删除，任务记为失败，不调用就地重建。就地重建本身也拒绝已删除文档，且不改删除标记和当前版。
3. 补测试：删除后再请求重建被拒绝；先排队再删除，执行后文档仍是已删除，当前版标志不变。

## 非目标

不做切分细节可视化。不改保留期天数。不做物理清理。不扩大上传格式。不改检索过滤、切分、Embedding 或 RRF。不改本机 `.env`。

## 验收

- 成员删除后列表不可见，提问不再命中。
- 管理员能看到已删除标记。
- 重建不恢复已删文档。
- 前端类型检查通过。

## 验证

```powershell
pytest tests/test_rag.py::test_reparse_deleted_document_is_rejected tests/test_rag_import_jobs.py::test_reparse_does_not_restore_deleted_document -v
cd frontend
npm run typecheck
```

未在本规划里执行这些命令。实现后再记录退出码，并在浏览器里核对删除说明、列表状态和重建反馈。

## 实现说明

有写入权限的人可以摄取或上传文档。成功后列表出现该文档，并显示版本状态和分块数；失败只提示原因。未删除的文档可以重建：确认后创建重解析任务，行上显示「重建中」，结束后提示成功或失败。没有源文件时任务失败，页面显示失败原因。

删除确认说明：成员列表和对话检索不再包含这份文档，管理员打开「显示已删除」仍能看到「已删除」。已删除的行没有删除和重建。重建不会清除删除标记，也不会把 `is_current` 改回去。系统管理员可以查看其他租户的重解析任务状态，否则跨租户重建无法看到结果。

### 代码位置

- 页面：`frontend/src/pages/Knowledge.tsx`。任务轮询在同文件的 `waitForImportJob`。
- 接口调用：`frontend/src/api/rag.ts`。
- 已删除文档拒绝重建：`app/rag/import_jobs.py`、`app/rag/service.py` 的 `reindex_document_in_place`。
- 系统管理员读取导入任务：`app/rag/access.py` 的 `can_read_import`。

### 验证结果

```text
pytest tests/test_rag.py::test_reparse_deleted_document_is_rejected tests/test_rag_import_jobs.py::test_reparse_does_not_restore_deleted_document tests/test_rag_access.py::test_kb03_member_cannot_delete_peer_admin_can tests/test_rag_access.py::test_cross_tenant_read_and_write_denied -q
通过，退出码 0

ruff check app/rag/import_jobs.py app/rag/service.py tests/test_rag.py tests/test_rag_import_jobs.py
通过，退出码 0

cd frontend; npm run typecheck
通过，退出码 0
```

浏览器以系统管理员打开知识库。摄取「QA004页面验收」后，列表显示「已发布」和 1 个分块。删除确认写明软删除的结果。确认后默认列表不再显示它；打开「显示已删除」后看到「已删除」，且没有删除和重建。对带源文件的 `qa004-rebuild` 发起重建，行上先出现「重建中」，随后出现「重建完成」。检索「QA004PAGE」没有命中已删除正文。

### 没做的事

没有做切分可视化、保留期调整和物理清理。没有放宽上传文件类型。没有改检索过滤、切分、Embedding 或 RRF。没有改本机 `.env`。
