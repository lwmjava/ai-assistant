# 工作流并发与周期幂等实现说明

日期：2026-10-20。原始任务PWFL-003。基线ee1018435a757fe7e1eca97be6bc271442d277ae。用户批准具体计划及列出的最小扩围，授权本地提交；未授权推送、合并、部署或代行本卡业务接受。

当前状态：实现已完成，独立入口审查的两项P2已修复，最终独立技术审核通过（58项独立复验）；最终本地检查已通过（3194passed/3skipped/4warnings、Ruff/mypy、前端四门exit0），最终Ubuntu CI与业务接受未验证，任务不得done。正式计划见[计划](plan_pwfl_003_20261020.md)，架构决定见[ADR-0018](../adr/0018-workflow-concurrency-idempotency.md)。

## 用户可观察行为

有既有workflows/write权限的用户可手动执行有权访问的工作流。相同实际数据库Engine、租户与workflow已有活动执行时，重复请求返回409 / WORKFLOW_ALREADY_RUNNING；进程共享容量用满时返回409 / WORKFLOW_CAPACITY_EXCEEDED。拒绝不会创建执行历史或调用Bridge，不记录prompt或输出。既有鉴权、owner/tenant运行时复核和取消权限保持。

新WORKFLOW_MAX_CONCURRENCY默认4，允许整数1..1000，环境变量支持十进制整数字符串；bool、浮点、非有限数、零、负值和越界配置拒绝。手动、定时与直接Engine调用共享全进程容量，不同线程/事件循环也共享；SQLModel会话仍由各执行调用唯一持有，不跨loop等待。

手动执行终态后可再次执行，视作新的意图。cron使用aware UTC的精确due与实际Engine/租户/workflow身份，仅首次记录commit成功才消费周期。同due或旧due无论成功、失败、超时、取消均拒绝再执行；下一个due可执行。首commit失败不消费；容量拒绝不消费，后续tick按既有run_latest重试。任务内部重试继续使用同一历史，不为每次尝试新增记录。

删除与执行准入使用同一标量锁下的原子保留。执行先进入则删除409；删除先保留则新执行409 / WORKFLOW_DELETING。删除提交成功后清周期标记；删除失败保留周期消费并释放删除保留。锁内不执行SQL或await。

## 执行收尾与失败

容量lease覆盖首次记录、Task/计时器建立、合作取消finally、终态持久化和webhook结束。取消发出后须等真实子任务退出才释放，避免后台仍运行时承认空闲容量。首次记录提交后刷新或Task/timer建立失败，数据库仍可用时将唯一记录标failed / WORKFLOW_EXECUTION_FAILED；已经消费的due不回放。任务建立失败会关闭尚未调度的coroutine，已调度子任务则取消并等待。

终态数据库写入失败继续沿原安全失败路径；容量仍清理、due保守保留，数据库可能留下running历史。本次不增加恢复队列，不承诺已发生外部副作用可回滚。既有合作取消、同事件循环取消绑定及全执行重试预算保持。

## 实际文件

- app/workflow/admission.py：弱Engine身份周期槽、进程容量、原子执行与删除保留。
- app/workflow/engine.py、control.py：共同入口lease、setup异常收尾、线程安全活动句柄。
- app/workflow/scheduler.py：传递扫描精确due，准入拒绝不提前消费调度水位；保留串行扫描。
- app/core/config.py、.env.example、README.md：并发配置及行为；app/api/routes/workflow.py与docs/swagger.json：409契约与删除互斥。
- tests/test_workflow_admission*.py：版本workflow-concurrency-idempotency-v1，Smoke/Adversarial，非Gold；旧timeout取消夹具只去固定workflow ID以隔离独立任务身份，原断言保留。
- docs/plans、docs/reviews、ADR索引与tasks.yaml：批准、计划、实现、独立审查和最终版本证据。

## 验证与审查

开发前隔离基线：128 passed / 3029 deselected / 2 warnings / 45.05秒，Ruff与mypy exit0。红测复现实际重复历史及HTTP201错误；配置红测、scheduler拒绝消费水位与due丢失反例各自记录于交付会话。

独立入口Agent /root/execution_review实际复现refresh后running历史和delete/start竞态两项P2。先落[缺陷及修复计划](../reviews/2026-10-20-pwfl-003-entry-review.md)，再修复并复验。首次组合回归155pass/1fail暴露固定ID夹具复用，记录见[归因](../reviews/2026-10-20-pwfl-003-fixture-regression.md)。另一次运行加载了修复前独立反例2fail/160pass，不冒称通过或最终证据。

最终完整命令、退出码、统计及日志指纹见[验收对账](../reviews/2026-10-20-pwfl-003-acceptance.md)与evidence.json；最终输入为733项[SHA256](../reviews/2026-10-20-pwfl-003-inputs.json)，不是仅靠HEAD冒称验证工作区。日志在data/pwfl003-*及归档证据，详细依赖/配置不含凭据。

## 未实现与回滚

不实现跨进程/节点锁、持久exactly-once、显式手动Idempotency-Key、队列、cron并行任务池、模型/迁移、新依赖或前端页面修改。长cron仍延迟后续扫描；上限是共享入口容量，不承诺调度器能并行跑满。进程重启只沿用既有保守历史水位，不能把它当分布式锁。所有验证仅隔离数据与合成Bridge，不能证明真实模型质量或生产吞吐。

回滚：停止新触发，等活动任务及webhook排空，再撤回本卡代码和配置；无需迁移数据库，不重放已消费历史，不删除真实数据。未执行生产、费用、推送、合并或部署。


关闭登记（2026-10-21）：用户明确接受并要求关闭；同业务候选11f36aa的Ubuntu Backend/Frontend/Agent official CI均success，当前737输入与最终完整回归匹配，关闭前独立163项通过。对应任务已done，详情见docs/reviews/2026-10-21-workflow-three-cards-close.md。上文待验收内容保留为历史时点记录，不代表当前仍有该阻断；原非目标与残余边界保持。
