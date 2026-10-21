# VDB-001 前置当前 HEAD 独立准入复核

日期：2026-10-21。独立复核身份：`/root/vdb_dependency_audit`，未参与业务实现。只写本报告、日志与指纹，未修改业务代码、任务状态或原始反例。依据 AGENTS.md、task-card-delivery v1.3.0 的当前 HEAD 前置复核及入口级反例门禁。

## 结论

**当前不能证明 VDB-001 前置缺口闭合；存在稳定复现的 P1，禁止按已完成准入或关闭。** 常规专项回归 225 通过，但真实领域入口的独立历史反例在当前 HEAD 仍有 5 个失败。尤其 Milvus 候选 SQL 回查缺租户复核，以及激活门禁绕开远端和完整性验证。应按 AGENTS 第 12 节暂停受影响实施，先由主 Agent 报告冲突与修复归属，不在本次只读审计中修业务，也不擅自改历史 done。

基线与候选 HEAD 均为 `ac494b386bccd00e5e0f66f1ec214664cdb3216d`；本报告没有业务候选改动。相关源码、测试、反例及日志 SHA256 见同目录 `2026-10-21-vdb-001-dependency-fingerprints.json`。后续相关输入变化使本报告对应运行结论失效。

## 契约、事实与缺口

| 前置 | 原始契约关联 | 当前证据 | 准入结论 |
|---|---|---|---|
| RAG-015 | local/Milvus 切换、摄取及回退路径、写入归一化 | 当前正式专项通过；2026-10-08 写入修复审查有采纳记录，但之后另有集中 Review。未在当前 HEAD 重做真实服务生命周期 | 部分复核通过，不能将旧服务证据当当前完整通过 |
| RAG-032 | 索引身份、完整新 collection 重建、激活与失败保留旧读路径 | 当前正式专项通过；3 个激活领域反例仍失败 | 未决 P1/P2，前置不闭合 |
| RAG-036 | 混合语义、真实服务切片证据及模型身份 | 当前正式专项通过；跨租户候选与响应模型反例仍失败 | 未决 P1/P2，前置不闭合 |
| V1C-001 | 按请求延迟构建检索依赖、无关依赖失败隔离、检索身份/状态 | 当前 lifecycle 与 review 专项通过。2026-10-19 V1C-005 准入报告记录的全量/CI 属其他 SHA | 本次覆盖路径通过；不推导当前全量或真实服务通过 |

任务契约来源为 `tasks.yaml` 对应卡。历史报告 `2026-10-08-RAG-032修复独立复审.md` 和实现说明追加整改涉及活动目标重建、取消补偿等更早问题，不能覆盖之后集中 Review 的激活缺陷；本次通过直接运行而非仅比较文档判断其仍存在。`2026-10-08-RAG-032当前提交集中Review.md` 与 `2026-10-08-RAG-036当前提交集中Review.md` 的以下缺陷当前仍可复现。

| 级别 | 当前领域入口反例 | 结果与原始契约关系 |
|---|---|---|
| P1 | `test_remote_unavailable_cannot_pass_activation_gate` | 远端连接被设为失败，激活仍成功；当前激活探测只走 SQL/NumPy，未验证目标 Milvus 可读，违反切换可用性门禁 |
| P1 | `test_incomplete_rebuild_cannot_retire_full_old_index` | 新目标只有旧索引两篇文档中的一篇仍可激活并退役旧索引，违反完整重建后切换契约 |
| P2 | `test_activation_uses_supplied_session_database` | 调用 Session 有数据而全局 engine 指向另一空库时错误拒绝；探测忽略传入 Session 的数据库绑定 |
| P1 | `test_sql_recheck_rejects_foreign_tenant_id_from_remote` | 远端边界返回外租户合成 chunk ID 时，SQL 回查返回 `FOREIGN_EVIDENCE`；未做 Document/Chunk 租户约束。该事实是隔离合成反例，不是线上数据泄露声明 |
| P2 | `test_real_embedding_rejects_unexpected_response_model` | 合法向量与 usage 配未批准响应模型仍被评测脚本接受，削弱真实 Embedding 模型身份证据 |

代码定位：`app/rag/index_registry.py` 的 `activate_index`、`probe_retrieval_hits`、`_probe_retrieval_hits`；`app/rag/vectorstore/milvus.py` 的候选 SQL 回查；`scripts/rag_hybrid_embedding_check.py` 的 `embed`。回查虽有远端 tenant filter，不能代替收到外部候选后的本地租户验证。

## 实际执行

平台 Windows 11 22621，Python 3.12.0（conda-forge），pytest 8.4.2、SQLModel 0.0.22、pymilvus 2.5.11。解释器固定为 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`。主 Agent 已同意独占测试库与临时目录；未读 `.env`，未调用付费模型或真实 Milvus，未覆盖向量库默认配置。conftest 清空模型凭据并禁止外网，保留开发环境默认 local。仅数据层为独占合成 SQLite，远端候选/HTTP 边界注入，不能证明检索质量或生产可用性。

```powershell
$env:DATABASE_URL='sqlite:///./data/vdb001-dependency-audit.db'
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
& 'D:/DepTooL/anaconda3/envs/ai-assistant/python.exe' -m pytest tests/test_rag_015_milvus_switch.py tests/test_rag_032_index_identity.py tests/test_rag_032_remote_rebuild.py tests/test_rag_032_rebuild_cli.py tests/test_rag_036_hybrid_semantics.py tests/test_chat_retrieval_lifecycle.py tests/test_chat_retrieval_lifecycle_review.py -q -rs --basetemp=data/pytest-tmp/vdb001-dependency-audit
```

退出码 **0**，225 passed / 1 warning，122.05 秒。警告是 sandbox 的 ast.NameConstant 弃用，不是业务失败。完整日志：`2026-10-21-vdb-001-dependency-tests.txt`。该测试集合混有单元/Mock 测试，不能整体定为 E1/E2 必交付证明。

```powershell
$env:DATABASE_URL='sqlite:///./data/vdb001-dependency-boundary.db'
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
& 'D:/DepTooL/anaconda3/envs/ai-assistant/python.exe' -m pytest -p tests.conftest .workbuddy/tmp/review032_current/test_activation_gate.py .workbuddy/tmp/review036_current/test_review_boundaries.py -q --tb=short --basetemp=data/pytest-tmp/vdb001-dependency-boundary
```

退出码 **1**，5 failed，5.33 秒。完整日志：`2026-10-21-vdb-001-dependency-boundaries.txt`。这些是实际 Service/激活/SQL 入口加合成远端边界的隔离反例；其中持久化和领域操作属于 E2 隔离集成，远端被替代的部分不构成 E1。失败均为业务断言，不是依赖缺失或环境崩溃。原始临时反例文件已逐个纳入指纹，不应因未在正式 tests 下而忽略；修复时应转为正式回归，当前未代为迁移。

## 后续建议与未执行项

1. 对照上述原始契约和报告，先明确承接修复的卡与批准边界，补正式领域反例，再修复并独立复核。不得以测试数据可弃为由放过租户边界与切换完整性。
2. 修复后先用同一隔离环境重跑反例和 225 项；全局配置变更仍需主 Agent 执行完整 pytest、ruff、mypy，Windows 结果不能替代要求的 Linux/最终 Ubuntu CI。
3. 再运行真实 Milvus 的摄取、查询、删除、重解析、失败切换和回滚，记录服务版本/collection/索引身份/最终源码指纹。真实 Embedding 费用与模型身份需对应授权和证据。本报告未执行真实服务或收费验证，不声明质量收益。

本审计没有新增业务实现、没有运行共享测试库、没有修改 `.env`、没有改变历史任务 done，也不替代 VDB-001 的最终验收和独立人工审核。
