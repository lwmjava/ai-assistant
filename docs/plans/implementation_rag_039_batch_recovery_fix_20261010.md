# RAG-039 批次汇总恢复修复说明

日期：2026-10-10。依据：用户明确要求修复最终审核 F03、补回归并复审。单实例边界沿用原卡和 2026-10-09 批准，不替代人工业务验收。

## 实际行为与变更

导入子任务已提交成功，而批次最后一次汇总提交失败时，调度启动、显式恢复和下一轮执行会从持久子任务重新核算批次。恢复不回放已成功或失败任务，不增加尝试次数，不再次 Embedding 或发布文档；返回值仍表示恢复的 running 子任务数量，因此仅修复批次时可返回 0。

`app/rag/import_jobs.py` 新增共享批次状态判定和单次 SQL 聚合对账。恢复 running 后先 flush，再统一对账并提交，移除恢复循环中会提前缩小声明总数的旧汇总调用。只有汇总漂移才修改批次。仍有 pending/running 时保持未完成；全成功、全失败与混合终态分别得到 success、failed、partial_success。

空批次、实存子任务少于声明总数、或子任务租户/用户与批次不一致时，保留批次并记录仅含批次 ID 与原因的诊断，不猜测缺失任务、不跨主体聚合。活动任务状态及 registry 保持原有执行语义。数据库仍不可提交时异常传播、事务回滚，后续恢复可重试，不无限吞错。

本轮只修改上述业务文件；新增 `tests/test_rag_039_batch_recovery.py` 17 项实现回归和 `tests/test_rag_039_batch_recovery_acceptance.py` 9 项独立验收；同步 ADR-0009、lifecycle-v1 Evaluation、计划/审查和 tasks.yaml。保留前次 RAG-039 与 QA-005 工作区修改，不归并为本轮新交付。

## 回归、复审与证据

指定解释器：`D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`。development、native/local、独立 SQLite 与临时源文件、空真实模型密钥及合成 Embedding。以下 E2 只证明生命周期，不证明检索质量或生产性能。

| 原始要求/失败边界 | 操作及最终观察 | 证据与结果 |
|---|---|---|
| 原卡恢复/幂等与批次实测；F03 | 真实重复上传、子任务成功后最后批次 commit 注入失败，再恢复/执行，用新 Session 查 success/2/2，唯一文档/块及尝试次数不变 | 实现者先红 6 failed/6 passed，缺任务 receipt 反例另 1 failed；实现后 13 passed，追加数值与查询边界后最终 17 项纳入 116 passed 领域组合。独立 9 passed 含相同故障及重解析成功凭据，E2 |
| 失败/部分成功/取消与重入 | 已持久 success/failed 混合及全 failed 对账，pending/active 仍未终态；恢复 commit 持续失败后回滚再恢复 | 两份新增正式回归与独立入口验证，E2 |
| 租户/身份/完整性 | tenant/user 错绑定、空批次、total2 仅 1 receipt 子任务 | 不跨主体计数、不缩声明 total、不伪造全成功；E2 |
| 重启/幂等 | 新 Python 进程恢复终态子任务对应的漂移批次、重复恢复 | 实现回归新进程及独立零重放断言，E2 |
| 原卡抢占/并发/旧读路径与前置 RAG-024 | 复跑既有 039、024、导入、重解析失败及日志路径 | 最终命令与复审结果见下文，不凭历史 done 推断 |
| 无队列/跨进程竞争；批准非目标 | 无新表、依赖、配置、API、模型或索引迁移 | 完整 diff 范围审查；跨进程竞争不适用，真实生产重启未授权 |
| 人工业务验收 | 本轮只授权修复、回归、复审 | 未验证，保持 in_review |

实际命令和最终结果如下；所有命令均使用指定 conda 解释器。

| 实际命令/执行者 | 结果 |
|---|---|
| 实现者 `pytest` 新增批次回归及原 039/024/导入/重解析失败/日志组合 | exit0，116 passed，70.30s，包含最终 17 个新增用例 |
| 验证 Agent `pytest tests/test_rag_039_batch_recovery_acceptance.py -q -rs` | exit0，9 passed，6.33s |
| 主 Agent `pytest tests/test_rag_039_batch_recovery.py tests/test_rag_039_batch_recovery_acceptance.py -q -rs --basetemp=data/t/r39bfnew1010` | exit0，26 passed，16.48s；DB `data/test_r39_batchfix_finalnew1010.db` |
| 独立审查者原 F03 自构脚本、连续恢复提交失败、错主体、receipt 缺子任务组合 | 最终 exit0；真实持久前后、文档/块 ID、attempt 与 embed 调用不变，详见复审报告 |
| 主 Agent 最终串行 `pytest -q -rs --basetemp=data/t/r39bfull21010` | exit0，1390 passed / 0 failed / 0 skipped，309.31s；DB `data/test_r39_batchfix_full2_1010.db`；日志 `data/rag039_batchfix_full2_20261010.log` |
| 主 Agent `ruff check . --no-cache` | exit0，All checks passed（新增测试稳定后再次核对） |
| 主 Agent `mypy app/` | exit0，188 source files；独立 `mypy app/rag/` exit0，73 源 |
| `git diff --check` | exit0 |

最终串行完整回归仅有原有 `ast.NameConstant` 弃用警告 1 条，没有新增 skip 或放宽断言。独立计划审查：`docs/reviews/2026-10-10-RAG-039批次恢复修复计划审查.md`；独立验收：`docs/reviews/2026-10-10-RAG-039批次恢复独立验收.md`；入口复审：`docs/reviews/2026-10-10-RAG-039批次恢复修复复审.md`。F03 已闭合；当前已审范围无未修复 P1/P2。人工业务验收未验证，因此整卡保持 in_review。

最终业务指纹 `app/rag/import_jobs.py`：`5CC3CCEC56FD1A1F483B5603A187A571C838BEECF2FA78BCF315331706302DEE`；实现回归：`1588DF2F9641DF25F7D189C7BD9AC8E418550E6EDCA6B07C4A8E2F821D59C956`；独立新增验收：`764D8EB80BFB13EC7C9C4DE1D4EC417255023F6D2175D1B8D07A0BEF44C13025`。验证后业务/测试未修改。

主 Agent 首次全仓回归 `python -m pytest -q -rs --basetemp=data/t/r39bfull1010`，独立 DB `data/test_r39_batchfix_full1010.db`、日志 `data/rag039_batchfix_full_20261010.log`：exit1，1389 passed / 1 failed / 0 skipped，300.74s。失败为既有 `test_thread_event_loops_share_actual_process_concurrency_cap` 的 SQLite `database is locked`；专项通过不能覆盖此失败。此记录保留为本轮中间失败；经下述调查、保持全部正式测试和 50ms 超时不变，串行最终 1390 passed / exit0，但不从最后通过推断数据库高负载争用已获解决。

锁争用调查采用 investigate：失败用例无批次，新聚合为空结果且无批次 UPDATE；独立 current/no-op 对照各 15 次均通过。进一步让真实 INSERT 持写锁 150ms，当前实现及仅关闭新对账的旧恢复路径均失败，证明现有 50ms SQLite 等待预算下的争用边界并非新批次 UPDATE 独有。这个对照失败点为 UPDATE，原全仓为 SELECT，不能声称已完全确定首轮精确原因或把首轮失败忽略为“偶发”。未修改正式测试超时/断言，也未增加忙重试；故意超过等待预算的临时诊断探针保留在 `data/rag039_sqlite_lock_probe_20261010.py`，属于负载诊断，不是本轮新增正式回归。停止其他 Agent 测试后串行重验完整通过；较长 SQLite 写锁争用的限制始终保留。

## Agent 与技能

`/root/rag039_implement` 落盘修复计划、失败回归与最小实现；`/root/rag039_verify` 独立构造正式领域验收；`/root/rag039_contract` 独立计划审查及最终入口复审；`/root` 整合范围、完整回归及文档/任务证据。沿用用户指定 task-card-delivery；独立审查使用 plan-eng-review 与 code-review-quality。计划先经独立审查，再实施；不存在用实现者自审替代独立审查。

## 限制与回滚

每次恢复仍需一次历史批次/子任务聚合扫描；无逐批 N+1 查询，但未证明大规模生产吞吐。异常绑定或缺任务的批次保留诊断供人工核查，不修补原始数据。真实 Milvus、付费模型、生产运行和多实例竞争不在本轮授权内；默认 local、Milvus Partial、资源 ACL Planned 不变。没有前端/API 变更，页面/前端构建不适用。

回滚前停导入调度并等待在途任务，仅回退本轮恢复聚合和测试/文档补记，保留前次实现、数据库、原文、任务及旧版本。无 schema 降级、数据删除、重要索引重建。本轮未提交、推送、合并或部署；回退后 F03 会重新存在，不把代码回退宣称为业务恢复。

## 后续深度审查修复证据

以上指纹及1390结果属于F03交付时证据。后续发现并按用户授权修复F07/F04/F05/F06；最终1463通过、0失败/0跳过，Ruff与完整mypy通过，独立复审通过，人工业务验收仍待完成。最新行为、旧F03验收同时间戳前置修正及最终指纹见 [深度修复实现说明](implementation_rag_039_deep_review_fixes_20261010.md)，不以本页历史“无未修复P1/P2”结论覆盖后续反例。

后续授权验收关闭补记（2026-10-10）：用户明确要求单独Agent代行人工验收，全新独立身份实际85项通过、0跳过，原卡全部必交付当前E2通过，无未决业务判断，任务已更新done。原文人工待验记录属于授权前历史；最新关闭对账见 [深度修复实现说明](implementation_rag_039_deep_review_fixes_20261010.md) 和 [独立业务操作验收](../reviews/2026-10-10-RAG-039独立业务操作验收.md)。业务/正式测试指纹未变，原1463全仓与Ruff/mypy门禁证据仍适用，无生产或Git发布。
