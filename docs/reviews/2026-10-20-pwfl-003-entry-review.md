# 工作流准入与周期幂等：独立入口审查

日期：2026-10-20。审查身份 `/root/execution_review`，未参与业务实现。原始依据：tasks.yaml PWFL-003、用户批准计划 `docs/plans/plan_pwfl_003_20261020.md`、ADR-0018及前置ADR-0017。使用task-card-delivery独立入口审查与等价代码质量检查维度，不代替最终人工审核或业务接受。

## 首轮实际反例与待修复发现

新增专属 `tests/test_workflow_admission_review.py`，经真实WorkflowEngine、真实delete路由函数、独立SQLite持久化与多个真实线程/事件循环，不只调用准入helper。所有数据合成，E2隔离集成，非Gold、非生产吞吐证据。

运行：Windows，指定Python `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`；`DATABASE_URL=sqlite:///./data/pwfl003-review.db`，`PYTHONUTF8=1`。

```powershell
& 'D:/DepTooL/anaconda3/envs/ai-assistant/python.exe' -m pytest tests/test_workflow_admission_review.py -q --basetemp=data/pytest-tmp/pwfl003-review-01
```

结果：**2 failed、4 passed，exit1，7.14秒**。测试内每项另建隔离tmp_path SQLite，不共用其他Agent数据库。

| 级别 | 实际发现与证据 | 影响与修复建议 |
|---|---|---|
| P2 | `test_review_initial_lifecycle_fault_releases_capacity_and_marks_history[refresh]`：首次commit成功后refresh注入异常，新Session读到唯一记录仍running；Bridge零调用，活动lease已释放 | 产生永远运行中的假历史且due已消费。将首次commit后的refresh/取字段也纳入setup失败收尾，数据库可用时rollback后标failed；保留due已消费语义。 |
| P2 | `test_review_delete_and_start_atomicity_across_threads`：delete检查通过后、实际delete前，另一线程Engine准入并进入Bridge；delete仍提交并级联删除workflow及活动历史 | 实际执行继续、终态落库失败，破坏ADR-0017活跃任务删除保护。建议同一准入锁原子登记删除保留标记，SQL不持锁；acquire拒绝被保留的workflow，删除成功/失败finally释放保留。 |

修复计划已向主Agent报告，未修改业务。必须先记录实施修复计划、保留正式反例再修复和独立复验；存在上述未修复P2前禁止关闭本卡。

首轮通过：跨线程不同event loop共享全进程cap；同workflow跨线程重入被拒绝且零重复记录；首次commit失败不新增记录且释放后同due可重试；计时器建立失败标failed且保留已消费due、释放容量允许其他workflow。

## 修复独立复核

主执行者将首次refresh纳入setup异常收尾，并在首commit前快照执行ID、起始时间和owner身份；数据库可用时失败标记写回，不再遗留running。API实现者增加同准入锁下的删除保留标记，acquire拒绝删除中的身份，SQL不在锁内；finally释放删除保留，只有commit成功才清理已消费槽。

原删除竞态反例保留相同真实交错点，断言两种合法原子次序：执行先获准则不得删除活动任务；删除先保留则并发执行明确拒绝、Bridge不启动、无新增历史且可删除。该调整验证互斥，不通过移除并发来掩盖缺陷。

补充正式入口反例：删除commit失败后能再次manual执行（reservation已释放），但同due仍拒绝（消费槽未丢）；不同实际Engine相同身份/周期独立；同Engine更换合法租户身份不误合并；成功和失败终态后同due/旧due均拒绝、下due允许；取消清理闸门期间容量保持，清理完成才释放且该due不重放。

修后独立12项：`pytest tests/test_workflow_admission_review.py -q --basetemp=data/pytest-tmp/pwfl003-review-02`，**12 passed，exit0，9.38秒**。新增测试Ruff最后复核exit0；第一次Ruff发现1条E501，仅测试断行修正，随后通过。无新增业务代码修改。

固定ID的旧timeout fixture改默认UUID经独立核对合理：每次fixture重建的是新业务任务，固定相同ID会人为复用同Engine/租户/workflow/due身份，保守已消费槽按契约应拒绝。保留原取消断言，不清理生产消费状态迁就测试，没有削弱权限、取消或幂等要求。

已核对完整业务diff（admission/control/engine/scheduler/config/API）、旧Bridge/ChatService调用方、配置示例/README/Swagger及原始契约。实际跨线程测试在两个真实事件循环中使用独立Session，共享容量/同workflow互斥；没有把单线程helper自测当成跨线程证据。

## 最终验证与版本

最终组合命令（同一独立数据库与指定解释器）：

```powershell
& 'D:/DepTooL/anaconda3/envs/ai-assistant/python.exe' -m pytest -k workflow -q --basetemp=data/pytest-tmp/pwfl003-review-03
& 'D:/DepTooL/anaconda3/envs/ai-assistant/python.exe' -m ruff check tests/test_workflow_admission_review.py --no-cache
```

结果：**144 passed，3053 deselected，2 warnings，70.90秒，exit0**；新增独立测试Ruff exit0。两条警告分别为既有ast.NameConstant弃用及有意重复主键注入的SAWarning。本轮组合包含最终修复与12项独立反例；`-k workflow`仅证明选中的工作流集合，不冒称全量或其他idempotency集合。命令输出已在本Agent终端会话3234实际取得，统计与命令正文落本报告，未另生成独立stdout文件。

两项原P2已修复并经入口反例复验，不再有本审查发现的未决P1/P2。基线与当前HEAD：ee1018435a757fe7e1eca97be6bc271442d277ae，加当前未提交文件。全量回归、最终CI与人工业务接受由主流程处理；本审查不代行最终人工审核，不据专项通过关闭任务。未提交、推送、合并或部署。

| 最终输入 | SHA256 |
|---|---|
| app/workflow/admission.py | AC3437E72FB9884263216667FB8B8ADAA2B506F2FEEA2D37312B81DA16DB9C7C |
| app/workflow/control.py | BAEE8B5FA79273F2D32DC0B878331BDE526DAD6E9DDA927D7B35A08AC2A65DE1 |
| app/workflow/engine.py | FC03FD5612BFD3883D88677336ABD8C9849DC538DF588840616FB37E4D2D8093 |
| app/workflow/scheduler.py | DD98575E14DBE1AB3C7F9BB61278888BB45CFF3851FF77E7850B366224A729A1 |
| app/api/routes/workflow.py | E5D03C63BB0E83F72F873B58522C54C789C46FE9269B5AF2E56166040C76862C |
| app/core/config.py | B47E785A83E0D14178D0AAD05353B219DC2A5BD2CE1D23FFF5D6710B26E556D0 |
| tests/test_workflow_admission_review.py | 0CF2475B96F7AD7D4B21CAF479128D26DE5C30C78FE48E3F834DA5FE35DAC8EE |
| tests/test_workflow_timeout_cancel.py | 49EE0B431DF85197481C650BDB5123F7D911DF72677886A4C7B5A1EF1850D2F8 |

相关检查输入更改须重验，不用本报告的旧绿结论关闭新版本。
