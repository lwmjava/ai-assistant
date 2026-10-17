# RAG-024 导入新版本发布原子性 — 实现说明

批次：`docs/plans/plan_rag_batch_20261007.md`；分支 `rag-batch-20261007`；基线 `85bd20d`。
计划：`docs/plans/plan_rag_024_publish_atomicity_20261007.md`
独立审查：`docs/reviews/2026-10-07-RAG-024发布原子性独立审查.md`

## 1. 实际完成了什么

### 1.1 修掉的缺陷（已复现）

`app/rag/import_jobs.py` 原先在新版摄取**之前**先把旧版 `is_current` 置 False 并**独立提交**：

```python
previous.is_current = False
session.add(previous)
session.commit()          # ← 独立事务

doc = await rag.ingest_parsed_document(...)   # ← 之后才摄取，可能失败
```

新版摄取失败时异常分支的 `session.rollback()` 无法撤销已提交的降级，结果是
**旧版不再是当前版、新版又不存在，该来源知识整体不可检索**。

修复：删除该独立提交块，降级改由 `ingest_parsed_document` 与新版落在**同一个事务**内完成
（`_persist_document` 中的 `demote_other_current_versions` 在同 commit 前调用）。

### 1.2 外部索引补偿记录

独立审查发现首版实现**覆盖方向反了**：在从不触碰外部索引的新版发布路径上每次记录，
而真正发生外部写操作的重解析路径反而没有记录（F-01）。已按审查意见修正，当前覆盖：

| 路径 | 是否触碰外部索引 | 记录行为 |
|---|---|---|
| 新版发布成功（外部库） | 否（`VectorStore.add` 当前未接线） | 登记「新版分块未确认写入外部索引」 |
| 重解析成功（外部库） | **是**（`delete_by_document` 已不可逆执行） | 登记「已清理旧向量，新分块未确认写回」 |
| 重解析：外部删除后主库失败 | **是** | 登记「外部清理已发生，主库未提交」 |
| 重解析：外部删除本身失败 | 是（失败） | `service.py` 包装为 `ImportTraceError(stage="external_index_compensation")` |
| Local 模式 | 与主库同事务 | **不记录**，无噪音（有专门用例验证） |

实现要点：
- `app/rag/service.py`：`reindex_document_in_place` 的外部索引 `delete_by_document` 失败时，
  包装成 `ImportTraceError(stage="external_index_compensation")` 再抛出。
- `app/rag/import_jobs.py`：新增 `_uses_external_vector_store()` 与
  `_record_external_index_compensation()`。
- **补偿记录在异常路径必须先 `rollback` + `commit` 再抛出**：外层失败处理会
  `session.rollback()`，否则这条「外部索引已不可逆变更」的证据会被一并回滚掉
  （这一点是实测发现的，首版实现因此漏记）。
- **发布后的补偿登记只能尽力而为**：发布已提交且不可回滚，登记失败只告警，
  绝不能把一次成功的发布翻成失败任务（修掉了审查提出的 F-03「已发布却报失败」窗口）。

已知限制（审查 F-04）：补偿记录目前**没有消费方、不去重、不清理**，外部模式下每条成功
导入会留一条。补消费端点需改 `app/api/routes/`，不在本卡 allowed_paths，故如实记录为限制。

**对「新版发布路径不触碰外部索引，是否收敛为不发记录」的裁决（复审提出的唯一未决问题）：
保留，但把语义写精确。** 理由：该路径确实不触发外部写操作，但外部库模式下新版分块
**确实不在外部索引里**（`VectorStore.add` 当前未接线），这是真实且系统性的不一致状态；
若收敛为不发，外部库模式下「主库已发布、索引里查不到」将完全无痕迹，反而更糟。
因此保留记录，并用 `error_code` 把两类情形区分开，便于后续按类型处理：

| `error_code` | 含义 | 后续动作 |
|---|---|---|
| `external_index_missing_chunks` | 主库已提交，外部索引缺少对应向量 | 补齐（写入即可） |
| `external_index_deleted_main_db_failed` | 外部索引已不可逆删除，主库未提交 | 按主库状态重建 |

### 1.3 行为变化（如实记录，供裁决）

| 场景 | 变更前 | 变更后 |
|---|---|---|
| 新版发布成功后旧版 | `is_current=False`，`version_state` 仍为 `published` | `is_current=False`，`version_state="replaced"` |
| 新版摄取失败后旧版 | `is_current=False`（**错误，知识不可检索**） | `is_current=True`，仍可检索 |

第一行的变化理由：旧行为留下「不是当前版却仍标 published」的不一致状态；变更后与
同步上传路径（`_persist_document` → `demote_other_current_versions`）完全一致。
`can_read_document` 依赖 `is_current`，不受影响。此项已提交独立审查判断是否构成
non_goals 所禁止的「改版本产品语义」。

## 2. 代码位置

| 文件 | 位置 | 作用 |
|---|---|---|
| `app/rag/import_jobs.py` | `_process_job`（约 506 行起） | 去掉预降级独立提交，改由同一事务降级 |
| `app/rag/import_jobs.py` | `_record_external_index_compensation`（约 202 行起） | 登记外部索引待补偿 |
| `app/rag/service.py` | `reindex_document_in_place` | 外部索引清理失败转成可追踪错误 |
| `tests/test_rag_024_publish_atomicity.py` | 5 用例 | 本卡验收 |

## 3. 验证命令与结果

| 命令 | 结果 |
|---|---|
| `pytest tests/ -k "rag or chunk or context or embedding" -q`（独立 DATABASE_URL + 全新 basetemp） | **369 passed / 2 skipped / 0 failed** |
| `pytest tests/test_rag_024_publish_atomicity.py -q` | **5 passed** |
| `ruff check app/` | `All checks passed!` |
| `mypy app/rag/` | `Success: no issues found in 65 source files` |

解释器：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`（Python 3.12.0）。

### 失败用例有效性（变异测试）

| 变异 | 结果 |
|---|---|
| 恢复「预降级独立提交」 | `test_failed_new_version_keeps_previous_current_and_searchable`、`test_retry_after_failure_does_not_create_second_current` 转红（旧版 `is_current=False` 被实测捕获） |
| 去掉 `reindex_document_in_place` 的补偿包装 | `test_external_index_cleanup_failure_is_recorded` 转红 |
| 去掉「外部库发布后登记待补偿」 | `test_external_store_publish_records_pending_compensation` 转红 |

三次变异后均已还原，工作区无 `MUTATION` 残留，并复跑确认 5 passed。

### 行为变化实测（`.workbuddy/tmp/r24_state_probe.py`）

```
BEFORE old: 7f66f959 is_current=True  version_state=published
job: success
AFTER  old: 7f66f959 is_current=False version_state=replaced  version_number=1
AFTER  new: 0ad8189a is_current=True  version_state=published version_number=2
```

## 4. 明确没做的事

- **没有实现跨库事务**：SQL 与向量库仍不在同一事务，本卡只做到「可追踪、可补偿」，
  不宣称原子（ADR-0008 明确不宣称跨库事务已实现）。
- **没有新增外部索引写入**：当前代码库中 `VectorStore.add` 从未被调用，
  外部向量库的写入本身尚未接线（Milvus 支持仍为 `Partial`）。补齐写入属向量库治理范畴，
  本卡不越界；发布后登记待补偿正是为了让这一缺口显式可见。
- 未自动迁移旧库、未重建真实索引。
- 未改 `version_number` 规则与去重判定。

## 5. 回滚

恢复预降级块或回退提交即可；不新增表/字段，无需迁移。旧版本与原文保留，
重要数据重建需另行授权。
