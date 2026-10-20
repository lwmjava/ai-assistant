# 到点调度验收与关闭对账

原始任务：FLOW-003。用户2026-10-20批准实施计划及原允许路径加三处文档路径。基线150afa8a8df771f60beb0fede8fe7038c40c500a；实现与证据随分支codex/flow-003-scheduled-execution提交并推送。三项CI以该提交的远程结果为准。

| 原始要求/批准边界 | 输入与操作 | 可观察结果 | 证据类别/位置 | 当前结果 |
|---|---|---|---|---|
| 到点进入执行并写WorkflowExecution | 有效owner/tenant、明确未来一分钟计划；实际_loop/_tick跨界→原Engine→Bridge→ChatService | 到点前零记录，整点后cron执行记录包含实际输入/输出/状态/耗时；新Session核对消息归属及历史API可查 | E2，test_workflow_schedule_integration.py，独立工程48项及专项65项日志 | 通过；最终全量3082pass/3skip/3既有warning/704.69秒/exit0 |
| 不以手动执行冒充定时 | 真正start_scheduler启动_loop，受控sleep推进时刻，未调用手动run | 真正调度循环产生cron记录；manual历史不抑制新定时 | E2，running_loop及manual_history测试 | 专项通过 |
| 设定下一时刻、最近一次及水位 | 整点前后、创建相等、重复tick、下一槽、错过多轮、内存重置；同租户cron全状态max(created_at) | 最近一次而非逐条补历史；失败不自动重试；manual/foreign tenant/started/finished不能干扰 | E2真实调度/原Engine，独立contract文件；时间helper纯探针E3辅助 | 专项通过 |
| 权限和绑定安全 | 失效/缺失/迁租owner；缺失/停用Tenant；扫描后修改/暂停/禁用/删除任务；临时DB绑定 | 无效入口不进入Bridge；Tenant失效真实Service无模型/消息；有效下轮可恢复，不永久误停；不从旧快照恢复删除行 | E2，正式反例、真实Service零调用及独立恢复探针 | ENG-01/02正式红后修复，独立重验通过 |
| 失败隔离与兼容 | 非法cron/时区、依赖缺失、一个任务失败与同tick另一个有效任务 | 错误不扩散，原启停、手动、历史、回调保持；不伪造定时成功 | E2，原9项+新增39项，任务专项65项 | 通过；最终全量同版本成功 |
| 时区与恢复边界 | SQLite naive UTC、等价时区、回退及已批准croniter DST实测 | UTC比较；保持安装croniter候选，不承诺新DST策略、实时SLA或跨重启精确slot | E2回归，方案及工程报告说明 | 批准范围内通过；边界保留 |
| 范围与防漂移 | 仅scheduler/必要helper、正式测试及批准文档 | 不改API/schema/模型/迁移/配置/依赖/CI/前端，不做其他工作流卡 | diff与允许路径核对 | 通过；允许路径、YAML及diff检查，533输入不变 |
| 独立技术审核与用户业务接受 | 新Agent从原始契约复核冻结版本；用户2026-10-20委托未参与实现与既有审查的Agent代行业务验收 | 技术审核与业务验收分属不同Agent；不继承前卡业务委托 | 技术审核 docs/reviews/2026-10-20-flow-003-human-audit.md，48 passed/17.02秒/exit0。业务验收 docs/reviews/2026-10-20-flow-003-business-acceptance.md：集成6 passed/4.23秒/exit0；真实_tick到期前0条，跨过上海10:01后新Session 1条cron success | 技术审核与业务验收通过；最终SHA的三项CI未验证，不得done |
| 最终版本三项CI | 候选最终SHA的Backend、Frontend、Agent official baseline | 同SHA全部通过，不能以旧CI替代 | 实现已纳入本次提交并推送，CI结果尚未返回 | 未验证，不得done |

## 已有实际证据

- 基线工作流9 passed/2.29秒、Ruff0/mypy218文件0；FLOW-001当前前置启停实测通过。
- 原业务红整点应1记录实际0，1failed/2.40秒/exit1。
- 首修42 passed/11.11秒。独立审查随后找到ENG-01旧计划快照误执行、ENG-02租户失效仍启动，四正式反例真实失败并最小修复；首修绿不当作最终证据。
- 修复后新独立工程Agent实际48 passed/12.84秒/exit0，另计划创建时间变化、租户恢复探针通过；无未修复P1/P2。任务专项65 passed/3020 deselected/1既有警告/19.55秒/exit0。
- 最终Ruff全库no-cache0、mypy app/218文件0。完整pytest3082 passed、3 skipped、3既有warnings、704.69秒、exit0；最后源码/测试输入及命令/报告指纹见同前缀evidence.json。

## 明确限制、回滚与状态

E2证据为真实调度/执行器/应用、隔离SQLite和合成provider，不能证明真实模型回答质量或生产性能。DST沿现有croniter6.2.4有限实测；记录创建时间为保守恢复，不是持久化scheduled_for。多实例/同进程并发幂等、失败重试、资源超时/取消、告警和开关UX分别留在原卡，不伪装为本卡已实现。

前端/API未变，本地前端检查不适用，但最终GitHub Frontend门禁不免除。回滚仅撤回调度helper及接线，保留执行历史、消息与审计，不删除或重放旧执行。

当前本地必要检查、独立人工审核和受委托的业务验收均已通过，卡保持 in_review。实现已纳入本次提交并推送，最终提交的三项 CI 尚未返回，不能 done。没有生产启用、付费调用、真实数据操作、合并或部署。
