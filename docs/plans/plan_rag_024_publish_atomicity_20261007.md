# RAG-024 导入新版本发布原子性 — 实施计划

批次：`docs/plans/plan_rag_batch_20261007.md`；分支 `rag-batch-20261007`。
契约出处：`tasks.yaml` RAG-024；前置 RAG-013 已 done。
已批准决定：不重复裁决；沿用 RAG-022 已完成的 Local 同库重解析保护。

## 1. 目标

新版导入的发布必须原子：准备完成后才切换；失败保留旧版可检索；重试幂等；
外部索引（非 SQL 的向量库）写入失败有补偿记录。

验收（原文）：Embedding/落库失败后旧版仍可检索；成功只一个 current；外部索引补偿有记录。
交付（原文）：准备完成后原子切换；失败保留旧版，重试幂等。
非目标（原文）：不改版本产品语义、不自动迁移旧库。

## 2. 现状（开工实测代码事实）

### 2.1 已复现的缺陷：旧版先退出 current 的独立提交

`app/rag/import_jobs.py:507-513`：

```python
previous_id = versioning["previous_document_id"]
if previous_id:
    previous = session.get(Document, previous_id)
    if previous is not None:
        previous.is_current = False
        session.add(previous)
        session.commit()      # ← 独立提交，旧版此刻已不再是 current

rag = RAGService(session, job.tenant_id)
doc = await rag.ingest_parsed_document(...)   # ← 之后才摄取新版，可能失败
```

失败路径（`import_jobs.py:529-557`）执行 `session.rollback()`，但降级已在上一个事务提交，
回滚无法撤销 → **旧版不再是 current，新版又不存在，该来源知识整体不可检索**。

### 2.2 已有原子路径（对比）

`app/rag/service.py:429-431`：

```python
if document.is_current:
    demote_other_current_versions(self.session, document.version_group_id, keep_id=document.id)
self.session.commit()
```

`_persist_document` 把「降级同组其他当前版」与「写入新版」放在**同一个事务**里；
`except Exception: self.session.rollback()` 可整体撤销。同步上传路径因此不受本缺陷影响。

### 2.3 版本组关系（支持直接去掉独立提交）

`_dedupe_or_version_existing`（`import_jobs.py:404-410`）返回：
`version_group_id = current.version_group_id`、`previous_document_id = current.id`。

新版以同一 `version_group_id` 创建，`ingest_parsed_document` 默认 `is_current=True`，
因此 `_persist_document` 内的 `demote_other_current_versions` **已经会降级旧版**，
且顺带把旧版 `version_state` 置为 `replaced`（独立提交只改了 `is_current`，语义更不完整）。

### 2.4 外部索引补偿：当前无记录机制

- `grep -rn "补偿|compensat" app/` → **零命中**，没有任何外部索引补偿记录机制。
- `app/rag/import_trace.py` 只有失败追踪（`ImportJobTrace`：stage/error_code/exception_type/message）。
- 向量写入发生在 `_persist_document` 提交之前的 embed 阶段（SQL 与向量不在同一事务）。

## 3. 方案

### 3.1 去掉预降级独立提交（最小修复）

删除 `import_jobs.py:507-513` 的独立提交块，改由 `_persist_document` 在同一事务内降级。
补充一句注释说明降级已由版本组统一处理，避免后人再加回来。

### 3.2 失败保留旧版可检索（回归约束）

删除后：新版摄取在提交前失败 → 整个事务回滚 → 旧版仍 `is_current=True` → 仍可检索。

### 3.3 外部索引补偿记录

向量库写入与 SQL 提交跨系统，无法保证跨库原子（ADR-0008 明确不宣称跨库事务已实现）。
本卡做的是**显式记录**而不是假装原子：

- 新版发布成功后，若外部向量写入失败或未完成，把该事实持久化到任务的追踪记录
  （复用 `ImportJobTrace`，`stage="external_index_compensation"`，记录 document_id / 影响范围），
  使后续可补偿；不静默当成发布成功。
- 失败时不留下「新版已发布但无向量」的静默状态。

### 3.4 重试幂等

- 旧版保留后重试：`_dedupe_or_version_existing` 用 `content_hash` 判定，重试会命中去重分支，
  不会无限累版本号。
- 需要显式覆盖：失败重试不得产生第二个 current（验收「成功只一个 current」）。

## 4. 非目标

- 不改版本产品语义（`version_state` 含义、`version_number` 规则保持）。
- 不自动迁移旧库、不重建真实索引。
- 不宣称实现跨库事务；只做到可追踪与可补偿。
- 不引入队列或新的外部状态存储（RAG-039 才处理多实例）。

## 5. 文件

| 文件 | 改动 |
|---|---|
| `app/rag/import_jobs.py` | 去掉预降级独立提交；补外部索引补偿记录 |
| `app/rag/service.py` | 仅在需要时暴露「外部索引写入结果」供调用方记录（不改发布语义） |
| `tests/test_rag_024_publish_atomicity.py` | 新增失败用例：Embedding/落库失败后旧版仍可检索、成功只一个 current、重试不产生第二个 current、补偿有记录 |

## 6. 验收与证据

失败用例先红（注入 Embedding/落库失败 → 旧版变为不可检索），实现后转绿。
- 命令：批次统一验证命令 + `ruff check app/rag/ app/services/` + `mypy app/rag/`
- 用变异测试确认用例确实依赖本卡行为。

## 7. 风险与回滚

- L2，副作用面：版本发布。回滚：恢复预降级块或回退提交；旧版本与原文保留，
  重要数据重建另获人工授权。
- 本卡不触碰真实索引重建。
