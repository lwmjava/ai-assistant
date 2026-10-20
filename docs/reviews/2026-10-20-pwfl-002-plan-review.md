# 工作流超时与取消计划独立审查

日期：2026-10-20。审查身份：独立子 Agent `/root/pwfl002_plan_review`，未参与计划编写或业务实现。仅新增本审查文件，未修改业务、任务卡，未执行实现验收。

审查输入：原始 `tasks.yaml` PWFL-002、`AGENTS.md`、task-card-delivery、推进指导与 Harness 治理入口、适用 global/architecture/runtime/testing/security/reliability/brownfield 规则、ADR 索引及 ADR-0012、`app/workflow/{engine,scheduler,bridge}.py`、`app/api/routes/workflow.py`、`app/models/workflow.py` 与实施计划。计划 SHA256：`EB877BF5163924E07F9EB479BABE8DEA5E99FE7DBEE2D2479A33F8FAFDB01DA7`。

主 Agent 已读取 plan-eng-review；本审查没有运行其交互流程，命名 AskUserQuestion 能力不可用，按架构、代码质量、兼容、权限、失败路径、测试与性能执行等价审查。不得将本文表述为该技能完整运行结果。

## 结论

方向符合原始契约，所提模型字段、迁移、ADR 和 API 文档扩围有直接交付依据；维持同步 `/run`、复用 engine/bridge、无新增历史字段、无后台请求 Session 遗留、无分布式队列，范围合理。应先补充下列约束，再将具体方案交用户批准；批准前禁止实施业务。本文不是架构批准、代码验收或 PWFL-002 关闭证据。

## 必须补充的实施约束

1. **Session 所有权与终态回读（P1）**。现有请求和 cron 将 Session 交 bridge/ChatService，取消路由具有另一 Session。活动注册仅持 task、不可变 workflow/tenant/execution 绑定及必要信号，不共享 ORM 实例或执行 Session 给取消请求；只有执行所有者写执行终态，取消请求等待其收尾后以刷新或新 Session 回读。否则 identity map 可继续返回 running，或两个请求并发操作同一 Session。反例：取消接口预先读 running，执行结束后其返回必须为持久化终态，不能覆盖 success。
2. **活动删除竞态（P1）**。现有 DELETE 会级联删除执行历史；计划只讨论完成/取消竞态，未覆盖运行中删除。必须约定最小行为（推荐本进程存在活动执行时 DELETE 返回 409，不触发取消、不中途删历史），并测试删除与取消/完成的交错。否则最终状态提交可能面对已删除的 WorkflowExecution，资源已释放却无法保留任务要求的标记。无须借此实施下一卡的通用并发池或分布式锁。
3. **取消中数据库错误及重复取消（P1）**。bridge/ChatService 可能在中途事务失败或已持久化部分消息。终态写入前必须处理失败事务 rollback，不能把旧的 pending transaction 带入收尾；重复调用取消或父任务二次取消不可中断唯一清理路径。数据库不可写时记录持久化失败，不能宣称终态已保存；任务注册清理与 Session 关闭必须仍发生。反例同时检查历史、活动注册及合成 finally 资源，不只断言 CancelledError。
4. **合作式取消的可证明边界（P2）**。超时是发出取消的截止时间，不是硬性清理完成时限。同步阻塞或吞掉 CancelledError 的下游不能被本方案强行终止；不能以移除注册、HTTP 返回或遗弃 task 声称释放。ADR/README 明确适用合作式路径、等待清理与不能保证中断的外部副作用；测试合作式长任务真实释放，对未退出路径保留活动事实。此限制符合“不跨进程强制杀除”，但不能弱化原契约的合作式主路径释放验收。

## 其余审查要求

- 超时与取消只终止该次执行，cron 循环继续；应用停机/父取消则收尾后向父传播，必须分别测试，避免 catch CancelledError 后使 stop_scheduler 永久等待。
- 300 秒默认与 1–3600 范围是待批准具体选择；创建和更新明确拒绝 bool、浮点、null（更新时未提供保持原值），迁移设置非空服务端默认并验证旧记录。超时使用单调时钟；执行开始后采用固定快照，不因另一个请求更新时限改变当前截止时间。
- 多实例 engine 共享注册须绑定实际事件循环与隔离测试库，终态释放，避免跨测试循环残留；不为此增加未批准的长期缓存或容量系统。取消 API 的 DB 记录必须与 workflow、tenant 和注册三者一致，不能凭可猜 ID 取消。
- 同租户 workflows/write 的成员及系统管理员跨租户行为沿用现有规则；owner 缺失不可回退触发者。取消操作的审计必须区分请求、已完成、未找到活动任务，不能在失败时记录成功。
- `/run` 201 与 ExecutionOut 结构保持；新错误标记不得暴露桥接异常敏感内容。webhook 成功后有界通知不回写超时状态，取消成功执行时只返回成功终态。
- 回滚先排空活动执行，旧代码容忍新增列；真实库迁移不在本次授权中。前后端契约变化触发前端四门，后端公共路径全量 pytest/Ruff/mypy，最终版本绑定及适用 Linux/CI 证据遵项目门禁。

## 证据与批准边界

本审查为静态计划评审，无运行测试，不以主 Agent 的 59 条基线或历史 CI 证明新增功能。读错的复数文件名 `app/api/routes/workflows.py` 已纠正为实际单数 `workflow.py`，不影响以上源码审查依据。

用户已批准 docs/plans、docs/reviews 和提交；其余模型、迁移、ADR、README、Swagger 路径及新生命周期决定仍待明确批准。补充以上约束后，可以集中提交一次具体扩围批准，不应重复请求既有文档目录与提交授权。PWFL-002 仍为 backlog，不能以本文关闭任务。

## 修订后复核

同日复读修订计划，SHA256：`9C05D930FA71606F1CA51CD1CB293CBB522D4FED4B30C5E73833E5E3FEA67F90`。四项必须补充已逐项纳入方案和失败边界表：Session 隔离/新 Session 回读、活动删除 409、失败事务 rollback 与原所有者收尾、重复取消防护及合作式截止时间限制。

复核结论：**可提交用户批准具体扩围和生命周期语义，无剩余阻断审批的计划缺口**。上文其余要求为后续实施与独立验收检查项；不得把计划修订视为业务已修复。仍须先获扩围批准、写 ADR，再实施并取得运行证据。
