# RAG-039 深度审查四项修复实现说明

日期：2026-10-10。依据用户“将这些问题依次修复，修复时不能引发新问题”的明确授权，按 F07 → F04 → F05 → F06 实施。四项修复、独立复审及最终完整后端/静态门禁均通过，最终1463 passed、0 failed、0 skipped。用户随后明确“启用一个单独的Agent做人工验收”，新独立 Agent 已代行本卡可复现业务操作验收，85项通过；原卡必交付无未验证项，任务更新为 `done`。该授权不包含生产启用或主观模型质量批准。

## 功能实际变化

| 缺陷 | 修复后的成功和失败行为 | 实际位置 |
|---|---|---|
| F07/P1 同名上传越权继承版本组 | 成员上传有效同源文档时，必须具备该当前文档的既有写权限；未经授权的同内容去重、不同内容升级都明确失败，发生在 Embedding、块发布及旧版降级之前。原作者和合法管理员仍可去重、升级；tenant/uploader 读范围不授予写权限。 | import_jobs 的来源候选判定，复用现有 can_write_document |
| F04/P2 普通汇总绕过保护 | 文件和 URL 创建任务在新增、提交前核对批次存在及租户/用户绑定；普通完成、重试聚合保留声明 total 下界。缺子任务不提前终态，空批次保持 pending；已有错主体 child 时整批拒绝汇总并记录安全诊断。commit=False 仍只 flush，整批事务由调用方提交。 | _validate_creation_batch、_recompute_batch、_batch_status，已有 finish/retry 调用 |
| F05/P2 复用软删文档 | 版本/去重候选排除 deleted_at 非空文档。真实删除后同源同内容或不同内容再次导入产生新的有效知识，旧文档、原块和删除状态保留；list/search 可读取新文档。 | 候选查询 deleted_at IS NULL |
| F06/P2 已提交成功被确认异常改失败 | rollback/refresh 后先重新核对绑定。除了当前任务的合法发布 marker，还读取数据库真实 success/document_id/content_hash，并校验文档租户、来源、hash、删除状态、主体授权和重解析目标。合法旧 marker 去重成功保持 success/attempt1；提交前故障仍失败并可重试，无效凭据不因 success 字样被认可。取消继续向上传播，已持久成功仍保留。 | _committed_success_receipt 与 _process_job 异常分支 |

业务仅改 `app/rag/import_jobs.py`。`service.py`、scheduler、config 本轮指纹不变；此前这些文件的 RAG-039 改动保留。没有新 ACL、schema、队列、依赖、配置或跨进程保证。默认 local、Milvus Partial、资源 ACL Planned 不变。

新增实现回归 `tests/test_rag_039_deep_review_regressions.py` 42 例、独立验收 `tests/test_rag_039_deep_review_acceptance.py` 31 例。同步四项计划、ADR-0009、lifecycle-v1 Evaluation、独立计划审查/复审/验收；tasks.yaml 由主 Agent 整合。

## 有效失败、修复与旧测试调查

| 顺序 | 实际修复前失败 | 修复后证据 |
|---|---|---|
| F07 | 8 failed / 4 passed，6.80s，exit1；file/URL、tenant/uploader、同/异内容均误允许其他成员 | 40 passed，15.73s，exit0，含本轮12+前置02410+旧验收18 |
| F04 | 11 failed，5.86s，exit1；声明 total 缩小、无效批次挂接持久化、错主体计数、空批次成功 | 最终67领域组合含本轮及修正版F03验收全部通过，后续186与独立65再次覆盖 |
| F05 | 4 failed，3.63s，exit1；真实软删后 file/URL 同/异内容新导入无法得到有效成功结果 | 34 passed，17.80s，exit0；新 Session 和真实 search 验证新知识，旧 deleted 保留 |
| F06 | 2 failed / 5 passed，4.98s，exit1；去重真实 commit 后 OSError 改 failed、CancelledError 改 running | 67 passed，30.92s，exit0，另追加8项管理员旧 marker 与非法凭据边界，最终42例通过 |

上述命令和隔离 DB/basetemp 逐轮位置完整记在 `plan_rag_039_deep_review_fixes_20261010.md` 实施证据章节，不把复跑成功冒充未发生失败。独立验收另有阶段23例的 8 failed / 15 passed，exit1；当时 F07 已修，不能虚构独立 F07 红测。

F04 首次扩大回归 68 passed / 1 failed，30.00s，exit1。失败测试原先固定取 ids[1] 当活动子任务，实际 pending-1 与 pending-2 的 created_at 同为 `2026-10-10 08:54:11.337080`，调度按 created_at、UUID 升序合法执行另一个任务。失败库 `data/t/r39df04g85d3/test_reconciliation_keeps_pend0/case.db` 保留。主 Agent 仅修改旧 `test_rag_039_batch_recovery_acceptance.py` 该例，主动构造相同时间戳，新 Session 查唯一真实 running child 并确认属于本批次，记录实际 ID，继续断言恢复前后 running、attempt 和批次非终态不变。独立审查认可并实际复跑；没有改业务排序、放宽超时、删除断言或新增 skip。

新增测试一次 Ruff E501 已换行修正；独立新文件首次未用 import/长行已清理，没有降低配置。这些测试组织问题不计为业务缺陷。

## 原始契约与关闭对账

证据环境：指定 conda Python、development、native/local、隔离 SQLite 和临时源文件、空真实模型密钥及合成 Embedding。HTTP 保留真实路由、权限守卫，仅替换隔离认证主体及数据库依赖；不是生产 JWT/SSO 证据。所有持久结果用新 Session 读取。

| 原始要求/批准边界 | 本轮当前操作与可观察结果 | 证据/结论 |
|---|---|---|
| 单实例原子抢占、批次与并发分离 | 复跑原039预算、协程/线程抢占、同源串行、Engine隔离与批次实测；本轮普通汇总声明数/绑定保护 | 当前实现与独审各186专项、独立65，E2通过；数值helper为E3补充 |
| running 重启恢复及幂等 | 原039新Python进程恢复、合法/无效/非current receipt、取消恢复及有限次数；F06真实成功commit后确认异常保持成功，无重发、无重复Embedding | 当前专项实际执行，独审另复跑非current receipt，E2通过 |
| 前置 RAG-024 与旧知识失败安全 | 当前02410例复验、重解析失败安全、合法版本升级、原文块/版本回滚和重试 | 当前两轮186含024与失败安全，E2通过，不仅凭 done 字段 |
| 权限、身份、失败边界 | 成员file/URL同/异内容拒绝，原owner list/search保持；原作者与两类管理员成功对照；跨租户/他人retry拒绝；批次缺失/错误绑定；receipt非法租户/来源/hash/owner/删除/marker拒绝 | 新42+独立31及20入口组、额外3组，E2通过 |
| 无队列、不引入跨实例租约 | 业务单文件最小修复，无schema/新依赖/新顶层框架 | diff与独立审查通过；跨进程竞争为2026-10-09批准非目标 |
| 文档/版本可追踪 | 本计划、实现说明、ADR-0009、Observed Regression Evaluation、独立审查与任务记录同步 | 实际文件与下述SHA256，不宣称质量提升 |
| 授权代行业务操作验收 | 用户后续明确要求独立Agent做人工验收；新独立身份重建6组HTTP/领域操作、两次新Python进程及前置02410例，并复验69当前入口用例 | 85 passed，exit0，E2通过；原卡没有需自然人裁决的主观判断，解除原待验阻塞 |

## 实际命令和最终结果

解释器：`D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`。全部 pytest 使用独立数据库和短 basetemp，真实 API key 为空。实现与审查的11文件完整命令分别在计划和复审报告。

| 执行者/实际命令 | 退出码与结果 |
|---|---|
| implement，11文件 pytest：本轮42+原039/024/导入/失败安全/日志144 | exit0，186 passed，92.37s |
| fresh_review，同一当前11文件领域组合 | exit0，186 passed，84.76s |
| fresh_review，`data/rag039_deep_fix_review_20261010.py` 自构20组入口；另原探针非current receipt/重解析竞争/retry权限3组 | exit0，全部正确业务断言通过 |
| verify，`pytest tests/test_rag_039_deep_review_acceptance.py tests/test_rag_039_acceptance.py tests/test_rag_039_batch_recovery_acceptance.py tests/test_rag_039_independent_review.py -q --basetemp=data/t/r39vdgb02e --tb=short` | exit0，65 passed，41.74s，0 skipped |
| root，`pytest -q -rs --basetemp=data/t/r39dfull1010` | exit0，1463 passed / 0 failed / 0 skipped，285.78s；DB data/test_r39_deep_full1010.db，日志 data/rag039_deepfix_full_20261010.log |
| root，`ruff check . --no-cache` | exit0，All checks passed |
| root，`mypy app/` | exit0，188 source files，无类型错误 |
| root，`git diff --check` | exit0 |

业务/测试已冻结，其他 Agent 停止测试后才启动 root 全仓。独立65执行时业务字节C57C…，之后仅helper周边空行整理；实现186后仅测试长行换行。最终完整回归已针对下列冻结指纹执行，新增42+31共73项均纳入1463全仓；不将格式前专项证据冒认最终字节复跑。全仓仅1条既有 ast.NameConstant 弃用警告，无新增skip或降低门禁，未观察到新增回归失败。

| 最终文件 | SHA256 |
|---|---|
| app/rag/import_jobs.py | A0F0A2E80570959339BFE77FEEE22ED15D2F9CD4ABF7BDB85B2383DFF620F686 |
| tests/test_rag_039_deep_review_regressions.py | 7980C92F879BBCBD63CAC57E16761DCA8EBC19D49094BB897B9CFECB322E3299 |
| tests/test_rag_039_deep_review_acceptance.py | 2AD613AE6FA66DE5BABA75CEE4FB86513CFCF76BAC7BB570B2B166FE326F27C1 |
| tests/test_rag_039_batch_recovery_acceptance.py | 1C24575CD46E3122FE7CD09E7B3A948D3688175C1E9751824264FE8C50BFA373 |
| app/rag/service.py（本轮不变） | BD5DD7197A9C59CDBDE41A6C900713DD9D8A0C4FA6DF5573EB47CFE7C5B1CD59 |

## 独立分工、范围和回滚

使用用户指定 task-card-delivery v1.2.0；fresh_review 使用 plan-eng-review、code-review-quality 做独立计划及入口复审。implement 独占业务/42正式回归/ADR/Evaluation；verify 独立写31入口验收；root 修正经实证的旧验收前置、运行全量并整合任务/实现说明。计划审查、独立验收与最终复审均在 `docs/reviews/2026-10-10-RAG-039深度修复*.md`。本轮已审范围无未修复P1/P2，不声称任意输入/时序都无问题。

未验证且不属于原卡必交付：真实Milvus/PostgreSQL、生产身份、付费模型质量与全部断电指令窗口；跨实例竞争不适用。原SQLite50ms等待与150ms持写锁争用限制保持，前次首次全仓锁冲突精确原因未全部确定，不能写成高负载争用已修复。本轮不放宽正式测试或增加忙重试。无前端变更，前端构建/页面视觉验收不适用。

回滚前停导入调度并等待在途任务，只退本轮四项候选/普通聚合/持久凭据保护和相应回归文档，保留原039/F01–F03/QA005、数据库、原文和旧版本。不迁移或删除重要数据；回退会重现四项风险，不能称为安全业务恢复。未提交、推送、合并或部署。

## 后续独立业务操作验收及最终关闭对账

用户后续明确授权“启用一个单独的Agent做人工验收”。依 task-card-delivery Constraints 的明确代行条款，启用新独立身份 `/root/rag039_business_acceptance`，未参与实现或前次技术审查。该 Agent 自构隔离 HTTP/runner 操作并用新Session、新Python进程核对结果，承担原卡确定性业务操作验收，不称其为自然人、不扩大为生产或模型质量批准。

正式报告：[独立业务操作验收](../reviews/2026-10-10-RAG-039独立业务操作验收.md)。新增探针 `data/rag039_business_acceptance_probe_20261010.py`，SHA256 `83D203AFB5211BBF1FB427B7B17903E61612D305F6FBC4BC852C484E8F8F5C6F`；不纳入默认tests收集，原1463全仓数量不变。

| 独立Agent本人实际命令（指定conda解释器） | 结果/实际日志 |
|---|---|
| `pytest data/rag039_business_acceptance_probe_20261010.py -q -s --tb=short --basetemp=data/t/r39biz1010` | 首轮exit1，5passed/1failed，10.69s；本人探针将两次拒绝之间实际search的查询Embedding错误计入第二次上传比较，日志business_acceptance_20261010保留。仅修探针基线至每次上传前，拒绝时零新增摄取和真实search断言保留，业务不改。 |
| `pytest data/rag039_business_acceptance_probe_20261010.py tests/test_rag_024_publish_atomicity.py -q -s --tb=short --basetemp=data/t/r39biz21010` | exit0，16passed，17.09s，0skipped；日志 `data/rag039_business_acceptance2_20261010.log`，独立DB `data/test_r39_business2_1010.db`。 |
| `pytest tests/test_rag_039_import_recovery.py tests/test_rag_039_acceptance.py tests/test_rag_039_deep_review_acceptance.py -q --tb=short --basetemp=data/t/r39bizreg1010` | exit0，69passed，39.77s，0skipped；日志 `data/rag039_business_acceptance_reg_20261010.log`，独立DB `data/test_r39_business_reg_1010.db`。 |

六组自构操作亲自验证批次5/单轮3/实际峰值2、同来源并发唯一发布、成员同名拒绝与管理员升级、真实HTTP删除再导入可list/search、缺child及错主体、真实commit后ack故障、重解析失败旧读保留和显式retry、两次fresh Python进程恢复。69当前用例补充真实线程、URL、取消、系统管理员和原039兼容边界。合计本人85通过，无新增P1/P2，前置02410例当前亲自通过；不冒称该Agent重跑root的1463全仓。

关闭时上表各原始必交付均有当前E2证据，独立复审已通过、门禁通过、源码及正式测试指纹仍与冻结全仓版本一致。没有剩余原卡必交付失败、未验证项或未决主观判断；先前人工待验项已由后续明确授权及实际操作解除，tasks.yaml 标 `done`，依赖仍仅RAG-024，非目标和生产限制保持。没有业务、schema、旧测试或依赖变更，不额外重跑未变业务全仓；仅核对文档/YAML/diff及指纹。未提交、推送、合并或部署。
