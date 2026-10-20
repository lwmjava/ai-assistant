# 工作流并发与周期幂等最终独立技术审核（第一轮）

日期：2026-10-20（Asia/Shanghai）。实际身份：独立子Agent `/root/final_audit`。仅参与过前置PWFL-002最终审核，未参与PWFL-003实现、计划审查或既有入口审查；本轮使用task-card-delivery 1.3.0步骤10，独立复核本卡原始契约，不代行用户业务接受。仅新增本文，未修改源码、测试、配置、依赖或冻结输入，未提交或推送。

## 结论

本地技术审核通过，未发现未修复P1/P2业务缺陷；可作为明确待外部门禁的技术交付。最终同SHA Ubuntu Backend/Frontend/Agent official CI与用户业务接受仍未验证，任务必须保持 `in_review`，不得标done或宣称可发布。用户批准方案及提交不等于委托Agent代行业务接受。

已实际读取项目必读规则、tasks.yaml原始PWFL-003契约、批准计划、ADR-0018及前置ADR-0017、相对HEAD的全部业务diff与新增admission.py、API/Engine/control/scheduler/Bridge调用方、四份新增正式测试、旧重试及超时调用测试、entry-review/fixture-regression、最终实现说明、关闭对账、inputs/evidence及全量日志尾部。原始交付是同任务同周期一次、可配置并发上限；验收允许满容量拒绝，批准计划明确单进程及手动终态后新意图语义。未将自拟缩小范围替代原始要求。

## 独立实际路径复验

依据原始契约独立选择入口组合，使用指定conda及独立数据库、独立basetemp重新运行；没有改动现有断言。E2为真实代码、隔离SQLite和合成合作Bridge/故障注入，不是Gold、真实LLM质量或生产吞吐证据。

```powershell
$env:DATABASE_URL='sqlite:///./data/pwfl003-final-audit.db'
$env:PYTHONUTF8='1'
& 'D:/DepTooL/anaconda3/envs/ai-assistant/python.exe' -m pytest tests/test_workflow_admission.py tests/test_workflow_admission_api.py tests/test_workflow_admission_scheduler.py tests/test_workflow_admission_review.py tests/test_workflow_retry.py tests/test_workflow_timeout_cancel.py -q --basetemp=data/pytest-tmp/pwfl003-final-audit
```

实际结果：**58 passed、1 warning、10.60秒、exit0**，原始输出在本轮工具执行session37056。SAWarning来自刻意重复主键造成失败事务的既有反例，无测试失败或跳过。本组合不替代主流程无筛选全量。

| 原始要求/失败边界 | 实际可观察验证 | 结果 |
|---|---|---|
| 同任务同周期一次、无重复记录 | 真实Engine活动时六次重复（含不同有效prompt）只一次Bridge/一条历史；真实HTTP重入409；cron同due/旧due终态后拒绝，下due放行；新Session查历史 | E2通过 |
| 并发上限可配置，满容量拒绝 | Settings默认4、环境串1/1000接受、非法值拒绝；两个不同任务能达到cap2，第三个拒绝；HTTP容量拒绝零新记录/Bridge，释放后放行 | E2通过 |
| manual/cron共同准入、跨线程共享 | 两真实线程/event loop共用进程cap和同workflow互斥；真实_tick容量拒绝不消费水位，释放后同due重试仅一次，下一due执行 | E2通过 |
| 身份绑定 | 两实际Engine相同身份周期独立；同Engine不同合法租户身份不误合并；不共享执行Session | E2通过 |
| 首次/终态/建立任务失败 | 首commit失败无历史且不消费；refresh/create_task/timer失败收尾释放，数据库可用时标failed；终态commit失败保留消费槽，不因同due重试重复副作用 | E2通过相应失败反馈 |
| 生命周期资源与删除互斥 | 取消finally闸门及webhook未结束前仍占容量；删除先保留则并发执行拒绝，执行先准入则不删活动任务；删除commit失败释放保留但不清消费槽 | E2通过 |
| 兼容前置重试/超时取消 | 原retry与timeout_cancel入口实际重跑，失败重试同记录、时限含退避、父取消/真实Bridge传播等原断言通过 | E2通过 |

两项先前P2（首次refresh失败遗留running、删除检查与执行准入竞态）已读取修复diff并实际重跑正式反例通过。旧timeout夹具改默认UUID只隔离独立任务身份，保留原取消断言；没有通过清除业务消费状态来放宽幂等。

## 最终版本与证据核对

基线/候选HEAD：`ee1018435a757fe7e1eca97be6bc271442d277ae`加733项冻结输入。指定解释器Python3.12.0，Windows11；其余依赖与非敏感配置见evidence.json。本地pytest8.4.2与CI固定8.3.4差异已记录，未当作CI通过。

使用同解释器通过stdin读取inputs.json的files，逐项计算 `hashlib.sha256(Path(p).read_bytes()).hexdigest()` 并与清单断言相等；复验前与全量完成后两次均 **733项、变化0、exit0**。最终再逐项核对evidence.json的22个日志路径及SHA256，**22项全部匹配、exit0**。关键抽查：

| 文件 | SHA256 |
|---|---|
| app/workflow/admission.py | ac3437e72fb9884263216667fb8b8adaa2b506f2feea2d37312b81da16db9c7c |
| app/workflow/engine.py | fc03fd5612bfd3883d88677336abd8c9849dc538df588840616fb37e4d2d8093 |
| app/workflow/scheduler.py | dd98575e14dbe1ab3c7f9bb61278888bb45cff3851ff77e7850b366224a729a1 |
| app/api/routes/workflow.py | e5d03c63bb0e83f72f873b58522c54c789c46fe9269b5af2e56166040c76862c |
| app/core/config.py | b47e785a83e0d14178d0aad05353b219dc2a5bd2ce1d23fff5d6710b26e556d0 |

核对主流程同输入全量归档：3197 collected、3194 passed、3 skipped、4 warnings、855.25秒、exit0；Ruff全仓无缓存exit0、mypy221文件exit0；前端typecheck/test/lint/build均exit0，18文件167测试。上述全量/静态/前端为主流程实际执行并归档、经本审核指纹及统计复核的证据，不冒称本Agent自行重跑全部命令。辅助一次rg因Windows通配文件路径语法返回exit1，只影响文本查询；后续直接读取完成，非业务断言失败。

## 关闭对账与未验证项

最终实现说明及acceptance对账与原始契约、代码和实际结果一致。只引入单进程标量锁与准入状态，锁内不SQL/await；无分布式锁、OPS-001依赖、模型/Alembic/新依赖或前端页面变化。接口契约及配置已同步README、示例和Swagger。无新增页面，浏览器页面验收不适用；不以四门通过替代页面交付。

全量三项Windows跳过分别涉及符号链接权限、POSIX0600及打开文件替换语义，不记为通过，仍需最终Ubuntu CI。四项警告为既有AST弃用、两项RAG数值故障注入及工作流失败事务SAWarning，未屏蔽。真实收费模型、生产吞吐、跨进程锁和发布未验证，也不在本卡已证明能力中。

手动终态请求没有显式Idempotency-Key；cron仍串行，重启不保证exactly-once；终态DB不可写时可残留running且不自动补偿。批准计划与实现说明已明确这些边界，没有把它们宣传为已完成能力。回滚需先停止新触发并排空任务/回调，再撤代码配置，不删除或重放真实数据。

最终独立技术审核现已出具，用户业务接受和同最终SHA Ubuntu CI仍缺；不能据本地全绿关闭本卡。未推送、合并、部署或操作真实数据库。仅新增本报告不改变733项验证输入；相关输入以后改变须重验。

归档格式补记：前端typecheck归档副本仅删除末尾空行以通过暂存空白检查，data原始stdout未变。独立复核两份日志去除末尾换行后字节完全相同；更新后的22项日志SHA256全部匹配，733项检查输入仍无变化，验证命令exit0。此为报告格式调整，无需重跑业务测试，技术结论及外部门禁缺口不变。
