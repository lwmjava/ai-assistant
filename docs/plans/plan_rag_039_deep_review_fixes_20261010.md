# RAG-039 深度审查四项修复计划

日期：2026-10-10。状态：独立计划审查通过，报告 docs/reviews/2026-10-10-RAG-039深度修复计划审查.md。依据用户本轮明确授权依次修复深度报告全部四项；本轮严格按 F07/P1 → F04/P2 → F05/P2 → F06/P2，业务不并行修改。主 Agent 管理 tasks.yaml 与实现说明，implement 独占业务及本轮正式回归，fresh_review 独立计划及入口复审，verify 独立验收。保留全部已有工作区改动。

## 原始契约与当前事实

原始 tasks.yaml RAG-039：单实例进程内原子抢占，批次/并发分离、running重启恢复与幂等、并发不重复发布、并发与批次实测；依赖 RAG-024 发布同事务降级与旧知识失败安全。单实例批准与 ADR-0009 不授予共享来源额外写权限、不保证跨进程或外部库 exactly-once。AGENTS 规定所有写访问核对主体/租户/资源/动作；access.can_write_document 已规定成员仅自己的当前未删文档，管理员按现有租户/系统规则管理。tenant/uploader 是读配置，不是共享写授权。资源 ACL 仍 Planned。

当前指纹与深度报告相同：import_jobs 5CC3CCEC56FD1A1F483B5603A187A571C838BEECF2FA78BCF315331706302DEE；service BD5DD7197A9C59CDBDE41A6C900713DD9D8A0C4FA6DF5573EB47CFE7C5B1CD59。深度报告 docs/reviews/2026-10-10-RAG-039深度独立审查.md 及 data/rag039_deep_probe_20261010.py、data/rag039_deep_extra_20261010.py 已实际证明四项缺陷。前次1390全绿是历史证据，不能覆盖新反例；前置024须当前专项复核。SQLite50ms与150ms持写锁对照限制保留，本轮不调整超时/引入busy重试。

| 原要求/来源 | 当前根因 | 目标可观察结果 |
|---|---|---|
| 权限/旧知识失败安全；F07 | 同tenant/source候选无can_write判断，继承他人version_group并自动降级 | 同名普通成员未经授权明确failed，旧owner current/列表/search不变；同主体与合法管理员仍可升级/去重 |
| 批次真实状态；F04 | 普通创建/finish/retry _recompute覆盖声明total且聚合错主体 | 构建分阶段期间声明total不缩；未全部完成不能success；错主体不进入统计，不污染原批次 |
| 新任务成功可检索；F05 | 去重current候选含softdeleted | 新上传不会去重到软删doc；创建合法新current知识并可search，删除旧数据保持删除 |
| 幂等持久成功；F06 | 去重job真实success提交后ack异常只认当前job的doc marker，反写failed | 验证持久job+合法doc证据后保留success/attempt1，不重embed、不failure trace；无效receipt仍拒绝 |

## 逐项方案和失败边界

### F07 首先修现有写权限边界

_dedupe_or_version_existing 接收鉴权主体（通过 job.user_id 的持久User），按现有 can_write_document 判断所选同租户来源候选，拒绝未授权候选，不为用户隐式创建共享可写版本组，也不默认新建独立来源来改变产品来源身份。明确拒绝覆盖同/不同内容、file/URL及tenant/uploader；失败在降级/Embedding前发生。管理员沿can_write且候选同job tenant；历史无持久actor内部任务兼容按原契约保留，但不得由普通API绕过。必要service变更须先说明；当前判断可仅import_jobs内完成，不改读配置/权限helper。

反例：A和C真实同租户member、A原doc已current；C同名file/URL任务不得继承group或dedupe A。新Session核对A current、来源/group/块不变，并由A真实search/list读取；跨tenant同源可各自新建。同主体、tenant_admin、system_admin合法升级及同内容幂等对照保持。HTTP入口由独立verify覆盖，领域入口本轮正式回归覆盖。

### F04 其次修批次普通路径

正常 _recompute_batch 执行统一tenant/user绑定校验，声明total为下界，不以当前len缩小；空批次不判success，缺child仅根据合法已存在子任务计数及声明total判pending/running，不能complete。恢复入口现有严格空/缺children不变，普通路径可以显示部分完成计数，但同样保留声明total及非终态。创建阶段先校验batch存在/tenant/user，禁止错误任务挂接原batch；commit=False仍flush不commit，整批配额事务由原调用方提交。URL创建不能先commit错误挂接任务再验证。_finish_batch/retry均复用安全普通规则，不纳入错主体；异常既有脏关联不擅改job归属。

反例：声明2先创建1（commitFalse和常规逐次），first执行成功后batch仍running/total2，再追加second后success2；tenant/user錯child加合法pending执行与retry都不能改变受害batch；空批次、极大声明total不缩。恢复F03合法receipt/缺child、计数漂移及一次GROUPBY成本维持。

### F05 再排除软删候选

候选SQL加 deleted_at IS NULL；不恢复软删旧document/块/版本状态。同源softdelete后新任务同/不同内容必须成为有效current知识，合法新版本身份依据未删候选规则；保留旧删状态。F07鉴权仅适用于有效来源候选；软删历史不作为可写current。反例用真实delete_document，再同内容上传/URL导入，检查task成功、新doc不为被删id、真实search与旧deleted_at；对照未删同内容仍去重。

### F06 最后修已提交成功的确认异常

在异常rollback/refresh后额外验证持久ImportJob.status=success、document_id绑定的合法Document：tenant、适用来源种类/ref、未软删及内容hash；普通dedupe文档允许旧job marker但主体必须是job上传者或经现有管理员权限合法访问；reparse必须target=id并满足适用权限。不只凭内存success或任意doc存在推断完成。新发布当前marker仍按原合法绑定恢复，非current合法旧发布receipt不能回放或误失效。F02有marker但tenant/user/source/deleted不匹配仍明确拒绝，不允许新的success分支覆盖无效marker。可新增专门持久成功证据helper，调用前现有绑定guard及相关异常路径需核对。

反例：去重success真实commit后OSError/CancelledError注入，独立Session先证实已持久，最终success/attempt1、唯一doc、无failure trace/重复Embedding。commit前异常仍failed；任意running或failed+document_id不可当success；成功证据错tenant/source/hash/softdelete拒绝；新发布与reparse ack异常及合法非current receipt对照保持。

## allowed_paths / 非目标 / 风险

本轮业务写 app/rag/import_jobs.py；如证明必要的service边界修改先报告，app/rag/service.py属于卡allowed范围但需明确归属与审核。独占新增 tests/test_rag_039_deep_review_regressions.py、本计划、ADR-0009技术补记和docs/evaluations/rag039-import-lifecycle-v1.md同步。root拥有tasks/实现说明，fresh_review独占reviews，verify独占其新测试。禁止业务并行修改、既有断言降级/skip/删测试、.env/生产/付费模型/Git发布/依赖安装、schema/新ACL/共享写授权/队列/跨进程及忙重试扩展。

主要风险：拒绝同名未授权候选是故障修复后的可见行为，必须有现有can_write依据；创建与执行统计时机不同，commitFalse不得提前commit；删除后候选过滤不能复活旧版本；success凭据不能盲信手工状态或牺牲F02。本计划独立审核通过后才改业务，每项先有效失败再最小实现，领域回归确认后推进下一项。

## 验证与回滚

解释器 D:/DepTooL/anaconda3/envs/ai-assistant/python.exe，独立data/test_rag039_deepfix_<shortuuid>.db、短data/t/r39df<uuid>、fixture临时源root，ENV development、native/local/mock、全部真实key空，无网络。每项保存实际红/绿命令与退出码；最后原039、024、API/权限、批次、日志、失败安全回归；mypy app/rag、ruff相关、diffcheck及独立入口验收，最终files通知root后静止，再由root串行全仓。

Evaluation分类Observed Regression（合成隔离复现），证据E2生命周期/权限/持久业务结果，不证明质量或生产性能。未验证真实Milvus/PostgreSQL/生产身份/付费模型/断电全部指令窗口；本轮自动化/独立技术验收不替代人工业务验收；任务保持 in_review，待用户验收。

回滚先停调度，仅回退本轮候选鉴权/软删过滤、安全普通批次聚合、持久success确认及相应文档；保留原039/F01-F03/QA005/所有原文与重要数据，不schema迁移/删除。回退会重新暴露四缺口，不得称业务安全恢复。


## 实施阶段证据和扩大回归调查

F07 正式领域红：tests/test_rag_039_deep_review_regressions.py，8failed/4passed，6.80s，exit1；同名同/异内容、file/URL、tenant/uploader均错误success。加入现有can_write候选判断后，本轮12+前置02410+独立acceptance18共40passed，15.73s，exit0。无service修改，成员拒绝在Embedding和降级前，旧owner实际list/search保留。

F04 正式红：本轮文件 -k batch，11failed，5.86s，exit1，确认创建阶段声明total缩小/错误batch绑定已持久/普通finish错主体计数/0空批次success。加入创建前绑定检查、普通聚合声明total下界与错主体拒聚合、total0 pending后，扩大首次为68passed/1failed，30.00s，exit1，不能冒认通过。

扩大失败是旧test_reconciliation_keeps_pending_and_active_children_nonterminal的固定ids[1]前提。隔离库 data/t/r39df04g85d3/test_reconciliation_keeps_pend0/case.db：pending-1 id8bf724e9fe704437bfd3f9dfc1901e20、pending-2 id3409556b392e4748b0c8048fb6cad81a，两created_at完全相同为2026-10-10 08:54:11.337080。runner明确按created_at asc,id asc，合法执行pending-2，pending-1 attempt0维持pending；测试以创建数组第二项认定活动任务不成立。单例复跑1passed/1.97s只能辅助，不能覆盖失败证据。root修正该测试，主动构造相同created_at并从新Session选唯一实际running child，继续原recover前后status/attempt/batch非终态断言；没有放宽超时或删除断言。最终67领域组合已包含修正版9个batch_acceptance全部通过。

F05 正式红：本轮文件 -k soft_delete，4failed/3.63s exit1（F07已正确阻止软删候选复用，但新合法导入需要排除该候选）。候选加deleted_at IS NULL后，本轮27+F02独立review7共34passed/17.80s exit0，真实delete后file/URL同/异内容新current可search，旧softdeleted和原文保持。

F06 正式红：本轮文件 -k 'commit_ack or uncommitted'，2failed/5passed/4.98s exit1，真实success commit后去重被OSError改failed、CancelledError改running；新发布/重解析及提交前失败对照符合预期。rollback/refresh后重核binding并读取持久job成功+合法doc证据，初绿本轮34+F02review7+F03batch17+batch_acceptance9共67passed/30.92s exit0。已追加8个合法管理员去重旧marker/tenant-source-hash-deleted-owner-marker不合法证据正式回归，待最终组合验证；不以手工success直接认可完成。

实际解释器与key隔离沿前述。各轮 DATABASE_URL 分别 data/test_rag039_deepfix_red07_54b7.db、green07_67c2.db、red04_72b1.db、green04_85d3.db、red05_a127.db、green05_b232.db、red06_c467.db、green06_d576.db；basetemp短路径分别r39df07r54b7、07g67c2、04r72b1、04g85d3、05ra127、05gb232、06rc467、06gd576。前置024本轮独立10passed/2.69s/exit0。最终验收和指纹以后续正式完整记录为准。


## 最终实现专项验证与冻结

最终命令（指定conda Python，独立 DATABASE_URL sqlite:///./data/test_rag039_deepfix_final_e687.db，ENV development、EMBEDDING_PROVIDER mock、OPENAI_API_KEY/DASHSCOPE_API_KEY为空、RAG_IMPORT_ENABLED false）：

`python -m pytest tests/test_rag_039_deep_review_regressions.py tests/test_rag_039_import_recovery.py tests/test_rag_039_acceptance.py tests/test_rag_039_independent_review.py tests/test_rag_039_configuration.py tests/test_rag_039_batch_recovery.py tests/test_rag_039_batch_recovery_acceptance.py tests/test_rag_024_publish_atomicity.py tests/test_rag_import_jobs.py tests/test_rag_reparse_failure_safety.py tests/test_rag_log_minimization.py -q --tb=short --basetemp=data/t/r39dffinale687`

结果：186passed（本轮42+原144），92.37s，exit0；无失败/跳过。F04修正后的同timestamp活动子任务测试、F02非法marker、F03批次恢复、024旧知识安全均在这轮实际通过。最终新增8项合法admin旧marker/非法成功证据通过，不只采用helper绿。其后仅将测试一行URL创建调用换行满足E501，语义未变。

`python -m mypy app/rag/`：73 source files，exit0。`python -m ruff check app/rag/import_jobs.py tests/test_rag_039_deep_review_regressions.py --no-cache`：最终All checks passed，exit0；前次新增测试124字符长行触发E501/exit1已换行修复，没有降低规则。`git diff --check`：exit0。

最终业务 SHA256 A0F0A2E80570959339BFE77FEEE22ED15D2F9CD4ABF7BDB85B2383DFF620F686；本轮正式实现测试7980C92F879BBCBD63CAC57E16761DCA8EBC19D49094BB897B9CFECB322E3299。service BD5DD7197A9C59CDBDE41A6C900713DD9D8A0C4FA6DF5573EB47CFE7C5B1CD59未修改；scheduler/配置本轮未修改。较先前独立执行C57C源指纹仅helper周边空行变化，无语义差异。业务/测试最终冻结，已通知root/独审/验收，无测试在途，随后root串行全仓证据写实际实现说明。本轮ADR0009与lifecycle-v1 Evaluation技术补记已同步；独立复审报告仍归fresh_review；任务仍in_review，待用户人工业务验收。

后续授权验收关闭补记（2026-10-10）：用户明确要求单独Agent代行人工验收，全新独立身份实际85项通过、0跳过，原卡全部必交付当前E2通过，无未决业务判断，任务已更新done。原文人工待验记录属于授权前历史；最新关闭对账见 [深度修复实现说明](implementation_rag_039_deep_review_fixes_20261010.md) 和 [独立业务操作验收](../reviews/2026-10-10-RAG-039独立业务操作验收.md)。业务/正式测试指纹未变，原1463全仓与Ruff/mypy门禁证据仍适用，无生产或Git发布。
