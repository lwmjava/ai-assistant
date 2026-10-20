# 工作流超时与取消实现说明

日期：2026-10-20。当前状态：旧库迁移阻断已修复，本地全量与适用检查通过，最终独立技术审核通过；任务保持in_review，不能标done。用户已批准计划及追加app/core/migration.py，并授权本地提交；未授权推送、生产数据迁移或代行本卡业务接受。

## 谁可以做什么

有workflows写权限的成员可以为本租户工作流配置timeout_seconds（严格整数1–3600，默认300），并通过取消接口停止本进程内的执行。系统管理员沿用跨租户管理权限。手动与cron执行共用原WorkflowEngine/Bridge/ChatService，不另建Agent Runtime。

时限为执行开始时的快照，包含bridge尝试与重试退避。到期发起合作式取消，等子任务finally实际完成后将原记录保存为failed / WORKFLOW_TIMEOUT。显式取消保存failed / WORKFLOW_CANCELLED；终态有finished_at与duration_ms，不增加历史字段或新状态枚举。父调用取消完成收尾后继续传播，重复取消不反复打断清理，取消一次cron执行不会取消调度调用方。

## 接口、失败与权限

`POST /api/v1/workflows/{workflow_id}/executions/{execution_id}/cancel`验证写权限、租户、工作流/执行/实际Engine绑定。错绑或越权404，无本进程活动句柄409，终态幂等返回。取消路由不共享执行Session，等待完成后新Session读终态。活动执行期间DELETE返回409，执行后沿用旧删除权限。owner缺失不回退触发者，重试退避后复核owner与tenant。

非有限raw JSON（NaN、Infinity、-Infinity）先转安全字符串，再由strict int拒绝，创建/更新稳定422且无写入；未改全局错误处理。真实终态DB持久化失败时接口安全503，执行资源仍清理，原running历史可能保留、后续取消409，不承诺自动恢复或持久化补偿。审计沿用WORKFLOW_EXECUTE，记录operation/execution_id/status，不记录prompt或输出。

注册仅保存实际Engine身份、workflow/tenant/execution绑定及任务和完成信号，不持有跨请求共享Session/ORM。相同URL的两个独立内存Engine不会相互取消。

## 旧库升级与回退

Workflow新增非空timeout_seconds列，服务端默认300；直接Alembic升级、降级及create_all兼容均覆盖。启动对账现在检查超时列：旧修订且列缺失时沿现有分阶段stamp父修订及upgrade补列，不直接标成最新。更早修订但已由create_all建出后续对象的混合库不会重跑重复DDL。旧任务和历史记录均保留。

若修订号已经head但超时列缺失，作为schema漂移明确失败：开发auto_migrate返回False并沿既有规则不阻断启动，生产拒绝启动；不自动降级或擅自修补真实库。上述只在隔离库验证，没有对实际用户库执行迁移。

回滚先排空活动执行，再回退本卡代码；可保留新增列及历史。若需撤迁移，只撤超时配置列，不删除执行历史或重放任务。真实重要数据操作需另行授权。

## 修改文件与任务映射

| 文件 | 实际交付 |
|---|---|
| app/workflow/engine.py、control.py | 总时限、进程内注册、合作取消、唯一收尾、身份复核 |
| app/api/routes/workflow.py | 超时契约、取消API、活动删除/owner拒绝、输入安全与审计 |
| app/models/workflow.py、alembic/versions/b5c8d3e0f271_add_workflow_timeout.py | 配置列、默认值及迁移 |
| app/core/migration.py | 启动时先核对新增列，防止错误stamp |
| tests/test_workflow_timeout_cancel.py、test_workflow_cancel_api.py、test_workflow_timeout_migration.py、test_workflow_timeout_review.py | workflow-timeout-cancel-v1的Smoke/Adversarial场景，非Gold |
| README.md、docs/swagger.json、ADR-0017及索引、tasks.yaml、计划/评审 | 契约、批准范围与实际证据 |

## 验证与证据

本轮修复基线提交b7fab9d6db4646ee3a08cc69c1f627b4cc418ee9，分支codex/flow-003-scheduled-execution，开始时工作区干净。历史WIP证据保留于b7fab9d及原inputs/evidence文件，不再用其失败状态描述当前功能。

指定conda Python3.12.0，Windows11 build22621；pytest8.4.2、Ruff0.8.4、mypy1.14.1、SQLModel0.0.22、Pydantic2.10.4、httpx0.28.1、Alembic1.14.1、langgraph1.0.10。CI相关extra未禁用；本地pytest与项目dev声明8.3.4存在版本差异，最终Ubuntu CI仍需验证。Node22.20.0/npm10.9.3。

已实际执行：

- 基线迁移1 failed/5 passed，补充反例后4 failed/2 passed，均exit1，确认旧库缺列与head漂移安全失败行为缺失；日志data/pwfl002-core-baseline.txt、pwfl002-core-red.txt。
- 修复后迁移与既有reconcile：9 passed、4.66秒、exit0，日志data/pwfl002-core-green.txt。
- Ruff全库无缓存exit0；mypy app/220文件exit0，日志data/pwfl002-core-ruff.txt、pwfl002-core-mypy.txt。
- 本轮前端typecheck/Vitest/lint/build各exit0，Vitest18文件167测试，2.76秒；日志data/pwfl002-complete-frontend-*.txt。前端源码/依赖未改，使用已有node_modules，未重装依赖；本卡只交付API，无新增UI按钮或页面验收。
- 最终无筛选pytest：3154 passed、3 skipped、4 warnings、905.94秒、exit0；收集3157项，日志data/pwfl002-complete-full.txt。3skip为既有Windows符号链接/POSIX权限/打开文件替换限制，不替代Linux验证。4warning为既有弃用、两项非有限向量故障注入及本卡重复ORM身份事务反例。冻结728输入结束后逐项重算变化0。

最终候选输入清单为docs/reviews/2026-10-20-pwfl-002-complete-inputs.json（728项，HEAD加源码/测试/配置/依赖/评测SHA256）。实际命令、E2关闭对账、完整日志和未验证项最终统一记录在docs/reviews/2026-10-20-pwfl-002-acceptance.md及complete-evidence.json。任何相关输入变化均使对应证据失效。

## 实际Agent与技能

采用task-card-delivery 1.3.0；计划用等价工程维度审查，未宣称完整运行不可用的plan-eng-review交互。主Agent负责engine/control及本轮启动修复；api_impl负责API/契约文档；model_migration负责模型/直接迁移；execution_review独立构造并实际验证入口反例（最终44项）；migration_review负责本轮启动入口补充复核。最终审核由未参与实现及既有审查的final_audit出具：独立53 passed、14.35秒、exit0，无未修复P1/P2；728输入变化0，14份日志指纹一致。报告docs/reviews/2026-10-20-pwfl-002-human-audit-1.md。该技术审核不替代用户业务接受或最终Ubuntu CI。

## 明确没做与限制

不做跨进程强杀、分布式取消、并发池、跨重启续跑、持久化恢复队列、前端取消按钮、生产迁移或模型质量提升。取消只支持合作式异步代码，超时是发起取消的截止点，清理可继续等待；不保证中断同步阻塞、吞取消的下游或撤回已完成外部副作用。成功后的webhook有独立既有10秒时限，不追溯改执行成功状态。

最终Ubuntu CI与用户业务接受尚未验证。没有推送、合并或部署授权。即使本地全绿，也应保持in_review等待必要外部门禁，不自动关闭。
