# 单实例导入任务恢复与幂等实现说明

日期：2026-10-10。任务：RAG-039。基准 HEAD：`ed438538dd923e3126bbb6fc5e39628566a6e10f`，交付为工作区修改，未提交、推送、合并或部署。

## 功能与范围

按照 `tasks.yaml` 原卡及 `plan_tasks_single_instance_rescope_20261009.md` 的批准范围实现。沿用现有导入创建、重试和调度入口；已有权限允许的用户可以继续上传、URL 导入和重解析。批次大小 `RAG_IMPORT_BATCH_SIZE` 与执行并发 `RAG_IMPORT_MAX_CONCURRENCY` 独立，默认均为 2，合法范围 1–1000；无效数值、布尔、NaN/Infinity 在配置或执行入口拒绝，未抢占任务。

同一进程、同一 Engine 的 runner 共享容量和活跃任务登记，包含多个线程/事件循环。条件更新原子抢占 pending→running 并增加尝试次数；同租户同来源串行，复用现有内容版本去重。不同 Engine 登记隔离。每次执行使用独立 Session，配额和 URL 快照采用短事务，避免持有 SQLite 写锁跨越 Embedding await。

调度器启动和 runner 扫描恢复不在活跃登记中的 running 任务。已有合法发布凭据补记 success，不再次摄取；尚未发布且未超尝试上限的任务回到 pending；耗尽尝试、错误身份/租户/源路径、来源绑定冲突或已软删的发布凭据变为 failed。后两者不能解释为“没有发布”后重放。取消向调用者传播，释放当前轮登记；发布已经成功时保留成功凭据，未发布时可在后续扫描恢复。failed 任务不自动无限重试，显式重试也受上限约束。

URL 已保存快照在中断恢复时复用；重解析 URL 仍抓取新快照。重解析最终 Document 更新、源快照及 ImportJob 成功凭据在同一次数据库提交中完成；Embedding、计划校验、数据库和外部清理失败时旧文档、块和计划保留，真实检索仍可读。批次全为待执行任务时保持 pending；开始执行或已有完成项且仍有未结束任务时保持 running，全部结束才计算 success/failed/partial_success。失败日志只记录任务 ID 和异常类型，Trace 保持既有错误记录行为。

未新增数据库表、迁移、队列、分布式锁或跨进程租约。不同进程同时执行、远端 Milvus 与数据库之间的全局 exactly-once、生产性能及真实检索质量不属于此次证明；新进程单独接续中断任务已经实测，不代表跨进程并发支持。

## 实际修改文件与任务映射

| 文件 | 实际交付 |
|---|---|
| `app/rag/import_jobs.py` | 容量/来源登记、原子抢占、恢复/重试/取消、短事务、绑定校验、批次终态、失败脱敏日志 |
| `app/rag/import_scheduler.py` | 调度启动前恢复 |
| `app/rag/service.py` | 可选 import_job_id 与重解析最终提交凭据，保留既有调用兼容 |
| `app/core/config.py`、`.env.example`、`README.md` | 独立批次配置、数值校验、运行行为与回滚说明 |
| `tests/test_rag_039_import_recovery.py` | 实现回归20例，含真实新 Python 进程恢复 |
| `tests/test_rag_039_acceptance.py` | 独立验收18例，真实领域入口/持久化/检索 |
| `tests/test_rag_039_independent_review.py` | 独立入口反例7例，含 F01/F02、active 排除和跨 Engine 隔离 |
| `tests/test_rag_039_configuration.py` | 独立配置及非法预算17例 |
| `tests/test_rag_reparse_failure_safety.py`、`tests/test_rag_log_minimization.py` | 7个旧用例改为真实租户快照与 runner 入口，保留失败安全、日志敏感文本和尝试次数断言 |
| `docs/adr/0009-single-instance-import-recovery.md`、`docs/adr/README.md` | 已批准单实例边界内技术决策与索引 |
| `docs/plans/plan_rag_039_single_instance_20261010.md` | 实施前计划、失败边界、独立审查修复计划 |
| `docs/reviews/2026-10-10-RAG-039实施计划审查.md`、`docs/reviews/2026-10-10-RAG-039入口独立审查.md`、`docs/reviews/2026-10-10-RAG-039独立验收.md` | 独立身份、红绿证据、反例、最终源码指纹 |
| `docs/evaluations/rag039-import-lifecycle-v1.md`、`tasks.yaml` | 版本化生命周期评价与任务状态对账 |

## Agent、技能与修复回路

主 Agent `/root` 使用用户指定的 task-card-delivery v1.2.0，负责规则/范围核对、配置、既有回归迁移、扩大验证和交付文档。`/root/rag039_implement` 负责业务和实现回归；`/root/rag039_contract` 使用 plan-eng-review 与 code-review-quality 独立审查计划、完整 diff 和真实入口反例；`/root/rag039_verify` 从原卡独立构造并运行领域验收。审查和验收身份未参与业务实现。

F01（P2）：第二条 claim 提交异常会泄漏前一条尚未启动的 active 登记，until_idle 无法退出。先落失败反例与修复计划，再将整个 claim/launch 纳入当前轮清理；独立复验通过。

F02（P2）：失效或已删除发布凭据被当成没有发布，导致重放。先用错误来源及真实 delete_document 反例复现，再区分不存在凭据和无效凭据，后者 fail closed；独立复验通过。另恢复重构遗漏的脱敏失败日志，7 个旧失败路径入口回归全部通过。没有删除/忽略测试制造通过。

## 验证环境与实际命令

解释器固定 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`，development、native/local、Mock Embedding；真实接口不外呼。主 Agent 设置所有模型密钥为空、LLM_PROVIDER=openai（只为了旧工厂警告用例，测试自身注入合成 key/provider）；独立 Agent 使用 mock LLM。测试库与源目录隔离，未读 .env 或重要数据。Mock 只支撑 E2 生命周期，不证明质量。

| 实际命令（均用上述解释器） | 退出码及结果 |
|---|---|
| `python -m pytest tests/test_rag_024_publish_atomicity.py tests/test_rag_import_jobs.py -q --basetemp=data/t/r39b1010`，改前基线 | 0，30 passed，5.71s |
| `python -m pytest tests/test_rag_039_independent_review.py tests/test_rag_024_publish_atomicity.py tests/test_rag_039_import_recovery.py::test_fresh_python_process_recovers_persisted_running_import -q`，独立审查 | 0，18 passed，8.16s；最终业务指纹在审查报告 |
| `python -m pytest tests/test_rag_039_acceptance.py tests/test_rag_039_import_recovery.py::test_fresh_python_process_recovers_persisted_running_import -q --basetemp=data/t/r39vff2c8` | 0，19 passed，12.01s，独立DB `test_r39_verify_final_f2c8.db` |
| `python -m pytest tests/test_rag_reparse_failure_safety.py tests/test_rag_log_minimization.py::test_import_failure_logs_type_not_exception_and_keeps_failed_job -q --basetemp=data/t/r39fix1010a` | 0，7 passed，2.11s |
| `python -m pytest tests/test_rag_039_configuration.py -q` | 0，17 passed，1.60s |
| `python -m pytest tests/ -k 'rag or chunk or context or embedding' -q --basetemp=data/t/r39f21010`，最终扩大回归 | 1，920 passed / 2 failed / 4 skipped / 415 deselected，132.91s；DB `test_r39_final2_1010.db`，报告 `data/rag039_final2_20261010.log` |
| `python -m ruff check app/rag/ app/core/config.py tests/test_rag_039*.py tests/test_rag_reparse_failure_safety.py tests/test_rag_log_minimization.py --no-cache` | 0，All checks passed |
| `python -m mypy app/` | 0，188 source files |
| `python -m ruff check . --no-cache` | 1，6条既有错误，详情见下文 |
| `git diff --check` | 0 |

最终扩大回归的2失败与改前基线一致：`test_rag_033_parse_quality.py::test_report_file_name_carries_gate_version` 用运行日期20261010对比历史报告20261007；`test_rag_log_minimization.py::test_milvus_delete_failure_retains_diagnostics_without_exception_body` 的旧 `_connect` Mock 不接受 index 关键字。后者虽与本次日志回归位于同一测试文件，但该用例及Milvus业务源未改。4 skipped 为缺 mcp、缺 llamaindex（collection和适配器各1）、未配置 cloud OCR 凭据；仅跳过相关未满足依赖的用例，不将其视为通过。主Agent另运行两组 `-rs` 条件用例复核：1 passed/2 skipped（1.75s）与2 passed/2 skipped（1.81s），均exit0。全仓 pytest 未运行；执行了原卡要求的扩大RAG子集。

前一次改前隔离回归为857 passed/4 failed/3 skipped；其中2项是 LLM_PROVIDER=mock 导致警告测试配方错误，改为 openai 后专项2 passed，不能当作业务缺陷。其余2项是上述已存在的日期/Mock签名失败。中间扩大回归捕获 F01 和7个旧夹具/日志失败，均已修复重验，不能引用中间失败结果为最终通过。

全项目 Ruff 既有错误：`scripts/milvus_switch_check.py:884` E501；`tests/test_rag_030_dialog_parent_assembly.py:22` F401；`tests/test_rag_031_llm_boundaries.py` 两项 F401、一项 F841、一项 E731。上述文件不在本卡 diff，未擅自扩卡修复。全项目检查尚未全绿，不宣称可发布。没有前端修改，页面/typecheck/build 不适用；没有实际生产重启、重要数据重建、付费模型调用或 Milvus 外部集成。

## 原始契约关闭对账

| 原始要求/批准出处 | 实际操作与观察 | 证据类别/位置 | 结果 |
|---|---|---|---|
| 区分批次/并发；原卡 deliverables | 5来源，limit3/concurrency2，处理3、峰值2，余2 pending；多线程共享容量 | E2，独立验收 batch/thread 两例及最终指纹 | 通过 |
| 单实例原子抢占/幂等；原卡 deliverables/acceptance | 多个重叠 runner、线程、同来源任务，attempt1，唯一文档/当前版本；取消与提交异常后重入 | E2，独立验收同源/版本/取消与独立审查 F01 | 通过 |
| running 重启恢复；原卡 deliverables/acceptance | 父进程 commit running，新 Python 进程空 registry 调真实 until_idle，再新 Session 查 success/attempt2/唯一文档及块；合法 receipt 不重复 Embed | E2，实现新进程例由独立验收者亲自复跑，独立验收 receipt 例 | 通过 |
| 失败/身份/租户/回滚；项目规则及计划失败边界 | 错误User、跨租户文件/重解析、无效/软删凭据拒绝；新版本失败后实际 search 旧知识并 retry | E2，独立验收/审查与7个旧入口回归 | 通过 |
| 无队列/跨进程并发；原卡 non_goals/单实例调整 | 复用调度器，无依赖或schema新增，完整 diff 审查 | 范围审查，不以源码审查代替上述业务E2 | 通过；跨进程并发不适用 |
| 人工业务验收；AGENTS.md及技能 Constraints | 用户只授权技能实施，未授权替代人工判断 | 未验证 | 待项目Owner验收，状态 in_review |

前置 RAG-024 在当前代码复验10项通过，原发布原子性路径未退化；独立报告记录前置审查限制，没有把 done 字段当作证据。本卡未新增未决P1/P2。整卡保留 `in_review`：人工验收未完成；全仓检查仍有既有失败，未降低门禁或写成发布合格。

## 回滚

后续证据补记（2026-10-10）：用户明确授权QA-005修复本说明记录的2个既有RAG失败、4个skip及6条全仓Ruff错误。该整改最终完整pytest **1364 passed/0 failed/0 skipped**、全仓Ruff/mypy/pipcheck均exit0，见 `implementation_qa_005_followup_20261010.md`。本说明此前920/2/4与Ruff6错保留为当时的历史证据，不再表示当前未清项。RAG-039原3业务源及配置未被这轮整改修改，恢复/幂等用例纳入最后全量通过；人工业务验收仍待完成。

再次审核及修复补记（2026-10-10）：独立任务卡最终审核发现 F03/P2：子任务已成功、批次最后汇总提交失败后无法恢复，因此本说明前述“无未决P1/P2”仅代表首次交付的已审边界。用户随后明确授权修复、补正式回归并复审；本轮恢复入口新增安全批次对账及 26 个正式回归/独立验收。最新结果与 SQLite 争用诊断、最终验证、未验证项以 [批次恢复修复说明](implementation_rag_039_batch_recovery_fix_20261010.md) 和独立修复复审为准，不用本页历史结论覆盖最新证据。

维护窗口先关闭 `RAG_IMPORT_ENABLED`，停调度并等待/取消在途执行；保留数据库、源文件和旧版本。回退本卡业务、配置及文档 diff，去掉新批次项，恢复旧 runner 行为。没有schema迁移；不要删除持久化任务/发布凭据或重建真实重要数据。退回旧逻辑后不宣称仍具备新恢复/并发保证。默认向量库仍 local，Milvus 仍 Partial，资源 ACL 状态不变。

深度审查修复补记（2026-10-10）：F07/F04/F05/F06已按用户授权先红后修复，新增73个正式回归/独立验收；最终冻结版本完整1463通过、0失败/0跳过，全仓Ruff/完整mypy通过，独立复审通过。最新行为、指纹与关闭对账见 [深度修复实现说明](implementation_rag_039_deep_review_fixes_20261010.md)。前述数值与指纹保留为历史，人工业务验收仍待完成。

后续授权验收关闭补记（2026-10-10）：用户明确要求单独Agent代行人工验收，全新独立身份实际85项通过、0跳过，原卡全部必交付当前E2通过，无未决业务判断，任务已更新done。原文人工待验记录属于授权前历史；最新关闭对账见 [深度修复实现说明](implementation_rag_039_deep_review_fixes_20261010.md) 和 [独立业务操作验收](../reviews/2026-10-10-RAG-039独立业务操作验收.md)。业务/正式测试指纹未变，原1463全仓与Ruff/mypy门禁证据仍适用，无生产或Git发布。
