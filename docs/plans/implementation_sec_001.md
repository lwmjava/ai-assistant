# 实现说明：知识库上传按类型和大小拒绝

## 完成的行为

知识库单文件上传和批量上传都会先看扩展名，再看字节长度。类型不在配置里返回 400，正文列出当前允许的扩展名。超过上限返回 413，默认上限是 10485760 字节，正文是「文件超过 10MB 上限」。等于上限可以上传。

不合规的文件不会写入磁盘。批量请求在全部文件都通过检查之后才创建批次和导入任务。同一请求里按文件顺序返回第一个错误：前面是非法类型、后面超限时返回 400，不会留下批次、任务或源文件。

知识库页去掉了「只允许 txt 和 md」的本地拦截。页头写明默认可上传的扩展名和 10MB。上传失败时展示接口返回的说明。

## 代码位置

- `app/core/config.py`：`RAG_UPLOAD_MAX_BYTES`、`RAG_UPLOAD_ALLOWED_EXTENSIONS`。
- `app/rag/upload_limits.py`：扩展名解析和检查。
- `app/api/routes/rag.py`：`POST /api/rag/documents/upload` 与 `POST /api/rag/import-jobs/upload` 在写盘前调用检查。
- `frontend/src/pages/Knowledge.tsx`：去掉本地类型拦截，页头写明扩展名和 10MB。
- `.env.example`、`README.md`：两个配置项和默认值。

## 验证

```text
pytest tests/ -k upload -q
16 passed, 1 skipped, 439 deselected
cd frontend; npm run typecheck
exit 0
ruff check app/rag/upload_limits.py app/api/routes/rag.py app/core/config.py tests/test_upload_limits.py tests/test_rag.py tests/test_rag_import_jobs.py
All checks passed
```

控制台开发服务 `http://127.0.0.1:5173` 返回 200。没有在浏览器里登录后上传 `.bin` 和超过 10MB 的文件，因此页面 toast 文案尚未用点击确认。接口拒绝和未落盘由上面的 pytest 覆盖。

## 没做的事

没有修改 `app/rag/document_storage.py` 的扩展名集合。`doc`、`xls`、`ppt` 仍会以 `.bin` 落盘，解析继续使用原始文件名。没有做魔术数字、病毒扫描和 URL 导入限制。没有改切分、Embedding 和检索。SEC-002、SEC-003、SEC-004 未实现。
