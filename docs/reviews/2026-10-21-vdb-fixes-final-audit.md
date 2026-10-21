# VDB三卡独立最终审核

日期：2026-10-21。审核身份：`/root/final_audit`，未参与实现及前三轮代码审查。按 task-card-delivery 1.3.0 最终审核角色，从原始任务卡、计划、实现、实际源码及证据独立核对。本轮未修改业务源码、测试、依赖或任务卡，未读取宿主dotenv、未付费调用、未提交/推送Git。

## 当前结论

**最终审核尚不通过。** 原三个问题及独立前三轮追加问题的抽查通过，但本轮发现未知向量后端被健康状态冒称为local的P2；主Agent已收到反例，修复后须追加复核。冻结全量仍在运行，最终SHA Ubuntu CI也缺失，不得在本报告未更新前宣称整卡关闭。

## 独立抽查与版本核验

基线HEAD `c2fc1bf3e1f02327a6972539a0905371d5f4acef`、分支`milvus-default-vector-store`，候选包含未提交实现，不能用HEAD单独代表最终代码。

审核直接读取三张任务的交付/验收/非目标及allowed_paths，修复计划和实现说明、AGENTS/迭代指导/Harness治理/全局及RAG与测试规则、独立三轮报告、真实验证报告，并抽查runtime/main/health/index_registry/milvus/cache的实际校验位置。

指定解释器`D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`，独立库`sqlite:///./data/vdb-final-audit-20261021.db`，`PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`：

```text
python -m pytest tests/test_vdb_acceptance_fixes.py::test_startup_failure_keeps_non_retrieval_api_available tests/test_vdb_acceptance_fixes.py::test_same_mode_different_instance_rejects_read_and_write tests/test_vdb_acceptance_fixes.py::test_wrong_instance_write_resolution_and_activation_keep_old_active tests/test_vdb_acceptance_fixes.py::test_persisted_lite_cannot_bypass_current_restrictions tests/test_vdb_acceptance_fixes.py::test_lite_import_or_cleanup_failure_is_isolated_without_persisting -q --basetemp data/t/vdb_final_audit_20261021
exit=0
12 passed in 8.14s
```

输出：[audit-tests.log](vdb_fix_20261021/audit-tests.log)。实际验证lifespan非检索隔离、persisted限制、同模式不同实例读写拒绝、激活失败不改变旧active、import/cleanup失败不登记；不是仅认可实现者摘要。

实际计算SHA256并逐文件比较：全量冻结清单740项、容器真实运行清单259项、独立第三轮11项，在本轮初次抽查时全部`drift=[]`。结果：[audit-input-check.json](vdb_fix_20261021/audit-input-check.json)。该结论只适用于本次P2修复前输入，修复后需重验/重绑，不能沿用旧摘要冒领最终版本。

## P2：未知向量配置健康状态仍静默冒称local

位置：`app/api/routes/health.py::_configured_vector_backend`、`app/rag/index_identity.py::current_backend`、`app/rag/vectorstore/runtime.py::ensure_milvus_runtime`。

Settings仍接受任意字符串；factory已经拒绝非法后端，但health及current_backend仍使用“milvus否则local”的分支。独立在隔离环境设`RAG_VECTOR_STORE=unsupported-synthetic`，真实`health_check`返回HTTP200、status=ok、vector_store.backend=local。证据：[audit-unknown-config.log](vdb_fix_20261021/audit-unknown-config.log)。

该输出不是物理向量库实际发生local fallback的证据，但会误报配置和健康，与VDB-001“开发部署/健康状态与实际后端一致、未知配置不静默退local”不符。runtime启动对非法值直接returnNone也不会固定unavailable。建议严格归一化后端，在向量选择/身份入口拒绝非法值，并让健康明确error/unknown，同时保留普通API服务。补正式失败测试，不能把health的错误local断言当允许行为。

## 原始契约审核矩阵

| 任务 | 当前可接受的技术证据 | 最终关闭仍需 |
|---|---|---|
| VDB-001 | 默认字段milvus/factory拒绝、真实Standalone应用链、非检索隔离和故障区分、混合候选/回滚文档 | 修复未知配置健康误报；最终全量与同SHA Ubuntu必要CI |
| VDB-002 | persisted限制、完整实例摘要、各领域门禁、真实Lite多进程持久化/目录/丢文件/health，Linux63 | P2公共后端归一化影响重验；前置卡及最终全量/CI闭合 |
| VDB-003 | 真实双模式五段exit0，合成Embedding边界明确；Linux63、独立63与当前审核12通过 | 新P2修复并重绑；完整全量最终结果、最终CI、更新关闭对账 |

真实服务证据证明合成Embedding链路，不能证明真实Embedding质量或性能；Windows原生Lite不在已通过支持范围。真实Standalone删除分别证明软删不可检索及显式向量清理，未把软删冒称物理删除。Bounded一致性有限轮询已明确记录，不声称强一致。

用户已精确批准Swagger扩围；当前tasks allowed_paths包含`docs/swagger.json`，对应45条契约通过的日志可追溯。未发现本审核抽查范围内擅自生产变更/重要数据清除/模型外发证据。无Git提交推送授权，不能为了完成验收自行发布。

## 最终边界

即使本地技术复核全部通过，缺最终提交SHA及Ubuntu Backend/Frontend/Agent official baseline run/job URL时，三卡仍应为`in_review`或`blocked`。本报告不是生产发布或合并批准，不以本地Linux容器测试替代最终CI，也不以技能审核代理替代项目规则。
