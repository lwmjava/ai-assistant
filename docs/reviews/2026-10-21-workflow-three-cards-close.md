# 工作流三卡关闭对账

日期2026-10-21（Asia/Shanghai）。用户明确指令“关闭三张卡，github CI通过了”，按上下文为PWFL-003、FLOW-005、PWFL-004，登记为这三卡最终业务关闭接受。当前已推送业务候选11f36aa76e6eebaa6efe0313dfc86fc9d29fc1eb；无业务源码、测试、配置、依赖或CI行为变化，仅更新关闭元数据与记录。三个无关未跟踪文件不修改、不提交。

## 最终版本与CI

[CI运行39544372233](https://github.com/lwmjava/ai-assistant/actions/runs/39544372233)，push事件，head_sha精确为11f36aa76e6eebaa6efe0313dfc86fc9d29fc1eb，status=completed、conclusion=success。通过GitHub connector直接GET actions/runs?head_sha及run/jobs独立核验，不仅转述用户确认。精简原始字段见[CI JSON](2026-10-21-workflow-three-cards-ci.json)。

| Job | 平台 | 结果 |
|---|---|---|
| [Backend ruff/mypy/pytest](https://github.com/lwmjava/ai-assistant/actions/runs/39544372233/job/119039093024) | ubuntu-latest | success；Pytest及RAG baseline gate step均success |
| [Frontend typecheck/Vitest/ESLint/build](https://github.com/lwmjava/ai-assistant/actions/runs/39544372233/job/119039092827) | ubuntu-latest | success；四门step均success |
| [Agent official baseline](https://github.com/lwmjava/ai-assistant/actions/runs/39544372233/job/119039093014) | ubuntu-latest | success；official evaluation与gate/probes均success |

没有核验或修改branch protection/bypass，不声称自动强制保护已生效；本次不合并、不部署。

## 原始契约与最终证据

| 卡 | 原始交付/验收 | 证据及版本 | 关闭结论 |
|---|---|---|---|
| PWFL-003 | 同任务同周期仅一次；上限可配置；重复无重复历史；满容量排队或拒绝 | 原计划/ADR18、admission正式集成与跨线程/Engine反例；最终11f36aa完整3213pass包含这些回归；最终737输入匹配；新Ubuntu CI通过 | 允许关闭，单进程拒绝策略、不提供分布式锁或手动终态请求Idempotency-Key的非目标保持 |
| FLOW-005 | 禁用/移出租户后suspended_owner且一条本租户告警；tenant_admin可见工作流/原创建者/原因 | owner_alert真实Engine/HTTP隔离集成、A刷新/B隔离实际浏览器截图；Swagger/README契约补齐；最终737与CI匹配 | 允许关闭；不发邮件/Webhook，不替代执行失败告警 |
| PWFL-004 | 一次失败一条告警；日志可按trace_id检索 | 真实Engine/newSession失败、重试、成功、超时与租户过滤；异常原文扩散及success误报已修复，正式安全用例与独立49+5探针、fresh37项复验；最终737与CI匹配 | 允许关闭；终态后摘要，不承诺实时逐attempt轨迹或外部告警平台 |

737项最终输入及23份日志/截图SHA256已全部重算匹配，exit0。最终业务检查输入见[输入清单](2026-10-21-workflow-alert-fixes-inputs.json)，环境/命令/日志指纹见[evidence](2026-10-21-workflow-alert-fixes-evidence.json)。完整本地回归3213passed/3skipped/4warnings/724.71秒/exit0，Ruff与mypy222files、前端四门通过。Windows的3项平台skip未冒称通过，当前同源码Ubuntu Backend CI已通过；本报告不杜撰Linux具体pytest数量。

PWFL-003原733清单会因后续告警卡修改Engine/README/Swagger而变化；不声称旧733仍未变。本次关闭统一绑定更新后的最终737及包含三卡的最终全量与CI；保留旧报告作为历史证据，不用旧绿关闭新版本。只新增本关闭记录与tasks状态，不改变任何冻结检查输入，按AGENTS §10的仅验收说明例外沿用11f36aa验证；关闭记录提交自身不冒称已另跑CI。

前置PWFL-001/FLOW-002/NFR-002等既有能力与三卡边界由当前工作流全量/独立关闭前专项核对，不能仅凭done字段推导。没有未修复的本卡P1/P2；原初验P1/P2及成功摘要补丁均有反例、修复及新版本复验，见[修复交付](2026-10-21-workflow-alert-fixes-delivery.md)、[独立审查](2026-10-21-workflow-alert-fixes-independent.md)、[最终独立技术审核](2026-10-21-workflow-alert-fixes-human-audit.md)。关闭前当前HEAD独立工作流专项实际163passed/3053deselected/2warnings/48.49秒，报告见[独立关闭复核](2026-10-21-workflow-three-cards-close-recheck.md)。

## 残余边界

终态DB不可写时可留running且无恢复队列、cron串行与重启后非exactly-once保持已批准限制；原WorkflowExecution.error及历史告警未自动清理，不宣称全系统脱敏。若需真实敏感历史清理，须单独授权；没有读取或删除真实数据。回滚需先停止触发、排空执行后回退代码，不重放历史。不自动开启下一卡。


## 状态登记

原三卡均由in_review改为done（PWFL-004的progress.status同步done）。用户业务接受与实际同SHA CI证据均已齐备；关闭元数据修改不改变737项检查输入。原实现说明中的待CI/待接受为当时历史状态，本次关闭记录取代该未决状态，不倒写当时的失败或未验证事实。只同步当前分支，不合并、部署或自动开始下一卡。


独立关闭专项完整输出归档：[关闭回归日志](2026-10-21-workflow-three-cards-close-regression.txt)。状态/说明更新后737项检查输入再次重算无变化，YAML三卡done断言通过；关闭元数据属于纯文档变更，未触发业务检查输入变化。
