# 清洗链与到期删除补偿实现说明

任务：RAG-014。实现状态：已实现，待最终回归汇总与审查；不声明完成发布门禁。删除语义为用户已批准的软删保留 90 天。

## 实际行为

获授权的用户上传/导入文档后，清洗仅移除起始 BOM、规范化 CRLF/CR 为 LF，保留行内容、空格、缩进、代码围栏、表格与盒图。对规则产生的额外改写或异常，自动回退到原始解析文本与 blocks，再走既有切分/嵌入链。已由解析器损坏的内容不由本卡还原。

原始上传文件字节不覆盖。`rag_ingestion_snapshots` 保存原始解析文本和 blocks，另存规则版本、原始/派生 hash、回退原因、字符/行/表格/页码统计、版式信息和确定性质量分；质量分为乱码替换字符占比诊断，非模型打分、非检索质量，也不作为拒绝阈值。清洗报告进入 chunk metadata。重解析更新当前快照；不声称新增完整版本快照历史。

异步导入保留原始 hash/来源版本去重，并在去重前计算清洗与质量报告。同步上传新增同租户、同上传者、同来源、同原始 hash 的当前未删文档去重，显式策略/版本参数不合并；跨租户/不同上传者不合并。重复暂存文件仅在引用检查和同租户目录检查通过后删除，原有文件保留。

软删除行为不变：授权检查后设置 deleted_at，原分块/向量/文件保留，读路径排除。超过 90 天的清理先写 `rag_vector_cleanup_jobs` 意图并提交，然后尝试审计、向量和文件删除，最终文档删除与任务 done 同事务。失败时保留文档、快照和任务；记录异常类名，不保存异常正文。60/120 秒退避，最多 3 次，耗尽变 failed；done/failed 不再自动执行。目标向量配置漂移暂停任务，身份不匹配失败且不操作外部库。

原导入调度器每轮清理即消费补偿任务，须启用 `RAG_IMPORT_ENABLED`；关闭它时无后台补偿。单 worker 适用，不提供多实例抢占锁。

## 代码与证据位置

- `app/rag/cleaning.py`：保守清洗、逐字结构校验、原文回退及质量/元数据报告。
- `app/rag/service.py`、`app/rag/import_jobs.py`：摄取、去重、快照与 chunk metadata；未改策略算法、RRF、阈值或模型。
- `app/models/rag.py`：快照级联关系与持久补偿表。
- `app/rag/retention.py`：到期清理、退避、恢复、租户/目标校验。
- `alembic/versions/f9a014c6e001_add_rag_cleaning_and_cleanup_jobs.py`：附加两表；非空表禁止降级删除。
- `tests/test_rag_clean_compensation.py`、`tests/fixtures/rag_cleaning_v1.json`：确定性回归样本 v1（合成/Observed Regression，非人工 Gold）；失败后重开 session 成功、退避、目标漂移、越权、源文件失败、迁移保护、同步上传与去重。
- `tests/test_rag_import_jobs.py`：批量导入快照/规则版本断言。
- `tests/quota_downgrade_probe.py`：配额回滚固定到其父版本，避免新增迁移改变测试含义。

## 验证环境与过程

解释器：`D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`。早期公共测试库在原型建表后缺新增 vector_target 字段，错误为测试环境旧结构，不修改业务断言。随后显式 `DATABASE_URL=sqlite:///./data/test_ai_assistant_rag014.db` 隔离本卡测试库，临时目录均为 data/pytest-tmp 下全新目录；未删除旧库或访问开发库。

默认本机 Embedding 配置使第一次现有测试调用了 OpenAI 兼容端点；其后验证均显式 `EMBEDDING_PROVIDER=mock`（MockEmbeddingProvider，配置维度 1024，受控 service 样本为 8）。全部结果只证明功能与补偿流程，不宣称生产检索质量。未进行真实性能评测，不报告性能提升。

失败复现：初始化新模型/清洗接口后，尚未修改 retention 时，故障注入断言 `purge == 0` 得到 `1`，1 failed / 2 passed，退出码 1，证实失败后旧实现删除文档。最初接口缺失导致的 collection error 与测试 patch 指向错误不作为业务复现证据。修复后失败重开数据库会话再成功用例通过。

实际最终命令与汇总补记在下方。

### 实际命令与结果

项目解释器、上述隔离测试 DB 与 Mock 环境执行：

| 命令 | 退出码 | 结果 |
| --- | --- | --- |
| `-m pytest tests/test_rag_clean_compensation.py tests/test_rag_access.py tests/test_rag_import_jobs.py tests/test_quota.py::test_downgrade_drops_quota_columns_and_keeps_rows -q --basetemp=data/pytest-tmp/rag014-final-target-01` | 0 | 40 passed in 8.39s |
| `-m pytest tests/test_rag.py tests/test_rag_import_jobs.py tests/test_rag_backend.py tests/test_chunking.py -v --basetemp=data/pytest-tmp/rag014-regression-01` | 1 | 81 passed / 1 failed / 1 skipped；来源契约历史漂移 |
| `-m pytest -q --basetemp=data/pytest-tmp/rag014-full-final-01` | 1 | 558 passed / 11 failed / 2 skipped in 111.88s |
| `-m ruff check . --no-cache` | 1 | 128 errors，与修改前基线条数一致 |
| `-m mypy app/ --cache-dir data/mypy-rag014-final` | 1 | 47 errors in 22 files，基线为 49 errors in 23 files |
| `-m mypy app/rag/cleaning.py app/rag/retention.py app/models/rag.py app/rag/service.py app/rag/import_jobs.py --cache-dir data/mypy-rag014-target` | 0 | 5 source files 无问题 |
| `-m ruff check --no-cache` + 本次修改的业务/测试/迁移文件 | 0 | All checks passed |

原始输出在忽略目录 `data/rag014-{full-final,regression,ruff-baseline,ruff-final,mypy-baseline,mypy-final,head-repro}.txt`，汇总在本说明与评审页保留。第一次全量为 554 passed / 12 failed / 2 skipped；修复新增迁移引起的配额回滚目标漂移、补充用例后重跑为以上最终结果。

残余失败涉及 2 个注入评测、4 个对话异步队列、1 个旧立即删除向量断言、3 个 BM25 日志捕获及 1 个来源旧字段契约。`verify_rag014_baseline_20261005.py` 在独立进程加载 HEAD 服务定义，同环境选择这些范围复核，12 failed / 4 passed：复现其中 10 个同名失败，另 2 个同族 ChatService 用例失败。`test_chat_stream_returns_sse` 单独在该顺序下通过，说明存在顺序/事件循环因素，未证明完全隔离。脚本不替换全仓库为 HEAD，故不将此结果夸大成完整历史版本基线。

本次没有修改 sources 输出或 BM25/对话实现，不为使全量通过改写旧来源断言或放宽注入门禁。独立审查/人工最终验收未完成；任务进入 in_review，不标 done。已批准实施不等于已批准发布。

## 发布、恢复与回滚

发布前在临时库验证迁移；初次实现未执行真实库备份/迁移、生产删除。项目启动 create_all 可创建缺失表，但不会升级已有表。后续发现旧开发原型表缺 vector_target，已按用户明确授权备份并补齐当前 SQLite，详见下节；原有文档无需补采，新导入/重解析才写快照。该修复不删除或重建表。

pending 任务由调度器在 next_attempt_at 到期后消费。failed 任务需负责人核对租户、文档仍软删且到期、目标库指纹及实际外部删除状态后，由受控维护路径恢复；本卡未新增管理员公开重试 API，不建议直接盲改目标字段或批量重试。停用调度器可暂停消费。

代码回滚须保留两表和原始文件，不执行非空表 downgrade；不恢复旧版失败仍删文档的清理器。清洗代码回退仅影响后续导入，已建索引需单独授权重解析；已物理删除的数据不能通过回滚代码恢复。

残余边界：真实 Milvus 故障/迁移集成、跨进程竞争、版本发布原子性、快照存储配额、审计跨事务 outbox 尚未实现。源文件删除后 DB 提交失败可重试清理，但原文件不能因 rollback 再出现；文档/任务保留并记录故障，不声称跨系统 ACID。

## 运行库缺列修复（2026-10-05）

Observed Regression：用户后台每约 3 秒记录一次“RAG 导入调度器 tick 异常”。只读核查确认当前 SQLite 的修订为 f9a014c6e001，rag_vector_cleanup_jobs 仍缺 vector_target，任务数为 0。create_all/checkfirst 不会给旧表加列，修订已为 head 时升级命令也不会重放迁移。这是初次实现漏掉的原型表兼容路径。

修改位置：迁移 f9a014c6e001 增加幂等补列；docs/plans/repair_rag014_schema.py 提供显式 SQLite 修复入口；tests/test_rag_schema_repair.py 覆盖已标记 head 的旧表、保留旧任务、空目标不猜测、DELETE/WAL 模式备份、重复执行、未知修订/不存在文件拒绝、校验失败回滚。未修改通用 app/core 迁移器，也未新增自动猜测目标指纹的路径。

用户明确授权后，以项目解释器执行 `-m docs.plans.repair_rag014_schema --apply`，退出码 0，repair=applied。脚本在 BEGIN IMMEDIATE 写锁内通过独立只读连接在线备份，随后仅执行 `ALTER TABLE rag_vector_cleanup_jobs ADD COLUMN vector_target VARCHAR NOT NULL DEFAULT ''`；备份与修复库完整性检查均通过，各表行数保持一致，修订和旧任务状态不变。备份：`data/backups/rag014-before-vector-target-20261005T114252Z-520b4558.db`，已确认被 Git 忽略。

重复执行同一命令退出码 0、repair=already_complete，未新增备份。实际库只读 SQLModel 查询 Document(deleted_at 非空) 与 VectorCleanupJob(status=pending) 均通过，返回 10 个软删文档、0 个待处理任务；未在验证脚本中执行实际向量/源文件删除。该证据证明此次缺字段查询故障已消除；未声称已抓取用户后台重启后的日志。

验证采用 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`。pytest 使用独立 `sqlite:///./data/test_ai_assistant_rag014.db`、EMBEDDING_PROVIDER=mock；Mock 仅证明逻辑，非检索质量。

| 命令 | 退出码 | 结果 |
| --- | --- | --- |
| `-m pytest tests/test_rag_schema_repair.py -q --basetemp=data/pytest-tmp/rag014-schema-red-01`（修复前） | 1 | 3 failed，其中迁移查询确实复现 no such column: vector_target；另两项为修复脚本尚不存在 |
| `-m pytest tests/test_rag_schema_repair.py -q --basetemp=data/pytest-tmp/rag014-schema-green-02` | 0 | 5 passed |
| `-m pytest tests/test_rag_schema_repair.py tests/test_rag_clean_compensation.py tests/test_rag.py tests/test_rag_import_jobs.py tests/test_rag_backend.py tests/test_chunking.py -q --basetemp=data/pytest-tmp/rag014-schema-regression-01` | 1 | 97 passed / 1 failed / 1 skipped；原有来源字段契约失败 |
| `-m pytest -q --basetemp=data/pytest-tmp/rag014-schema-full-01` | 1 | 563 passed / 11 failed / 2 skipped；11 个同名历史残余失败未增加 |
| `-m ruff check . --no-cache` | 1 | 128 errors，与此前一致 |
| `-m mypy app/ --cache-dir data/mypy-rag014-schema-full` | 1 | 47 errors in 22 files，与此前一致 |
| `-m ruff check tests/test_rag_schema_repair.py docs/plans/repair_rag014_schema.py alembic/versions/f9a014c6e001_add_rag_cleaning_and_cleanup_jobs.py --no-cache` | 0 | All checks passed |
| `-m mypy docs/plans/repair_rag014_schema.py --cache-dir data/mypy-rag014-schema` | 0 | Success: no issues found in 1 source file |
| 项目解释器全量 YAML/ID/依赖/无环校验 | 0 | 182 卡，RAG-014 保持 in_review |

报告在忽略目录 data/rag014-schema-{regression,full,ruff,mypy}.txt。首次 YAML 核对的 shell 内引号转义导致 SyntaxError，未作为业务结论；改为 stdin 脚本后校验通过。

回滚：加列失败事务自动回滚并保留备份，已由故障注入验证。成功后通常保留新增兼容字段即可回退代码；若确需恢复整个数据库，应先停服务并评估备份之后的写入，按备份恢复流程另行执行，不直接覆盖运行库。本轮未恢复备份、未操作生产库，不将 RAG-014 改为 done。

## 后续全量检查整改

用户于同日批准修复剩余检查问题，单独归入QA-005。最新全量575 passed/2 skipped，规定RAG回归82 passed/1 skipped；Ruff128条和mypy47条已清零，命令退出码均0，证据见 implementation_qa_005_remediation_20261005.md。上文失败汇总作为当时记录保留，不再作为当前阻塞原因。本卡仍需独立审查与人工业务验收记录，不因其他卡整改自动关闭。

## 关闭与验收记录（2026-10-05）

用户提供豆包独立代码审查记录 `docs/reviews/2026-10-05-RAG014-023代码审查.md`，并重新上传《项目设计方案.md》核对盒图样本。按当前配置复现，第8个父块包含与原文件逐字一致的完整代码围栏/盒图，清洗保真已核查；页面普通字体错位与子块结构切分问题已明确不属于本卡新增验收要求。

在说明验收范围和后续任务归属后，用户明确表示“RAG-014可以关闭了”。据此记录本卡范围的人工验收通过，将tasks.yaml中RAG-014改为done并清除blocked_by。审查、16项清洗/补偿/修复测试及QA-005最新验证共同构成关闭证据；不补造用户未逐项报告的下载、重复上传或删除页面操作记录。

审查报告中的三项P2为旧观察：当前计划已有失败/回滚、完整验证命令及原始上传文件与派生文本区分。保留历史过程，最终关闭状态以本节和任务卡为准。

范围外事项仍保留：结构化页面显示属于RAG-020相关展示修复；结构保护及父子关联分别由RAG-021/022承接；真实Milvus、跨进程竞争等沿用原残余风险记录。两个全量skip为缺少LlamaIndex/MCP的模块级跳过，RAG回归的1个skip是重复统计；不计作已验证，不将本卡done表述成生产发布合格。不提交或发布代码。
