# 工作流到点触发实现说明

日期：2026-10-20。基线150afa8a8df771f60beb0fede8fe7038c40c500a，分支codex/flow-003-scheduled-execution。用户已批准实施计划、原允许路径及三处文档扩展。本说明随实际审查与验证更新，当前不是整卡完成或业务接受结论。

## 实际完成的链路

工作流仍由现有lifespan启动的单进程、串行asyncio调度器扫描。现已按任务时区求当前最近到期时刻，而非求未来下一次后再要求它已到点；到期后复用原WorkflowEngine、WorkflowBridge和ChatService，以创建者身份执行并写WorkflowExecution。新Session可读到cron来源、租户、输入/输出、状态、开始/结束与耗时，既有历史API可以查询。未直接调用手动执行代替定时验收。

`app/workflow/engine.py:latest_due_time`复用安装的croniter，使用1微秒前驱边界包含整点，候选转aware UTC并拒绝未来值。`next_run_time`原API语义不变。DST继承croniter6.2.4实际候选，不新增跳过不存在时刻或重复时刻执行两次的政策。

`app/workflow/scheduler.py:_tick`把工作流创建时间作为允许的起始下界（相等可进入），把进程缓存与同workflow/tenant/cron的全部状态记录max(created_at)作为已消费水位（相等已消费）。manual与外租记录不能抑制定时执行，started_at/finished_at不参与消费水位。每次只选择最近一次，不逐条补历史；尝试前推进进程水位，失败不在本卡自动重试。

进入执行前在新Session按ID重新读取工作流及owner，缺失、禁用、暂停任务不从旧扫描对象恢复；cron、timezone或创建时间与扫描计划身份不一致时本轮拒绝，避免旧到点判定启动新的未来计划。Tenant必须存在且active，停用/缺失租户零Engine/零模型/零业务消息且不误标owner失效；后台任务沿现有受控执行资格，不能继承系统管理API的停用租户管理豁免。原owner缺失/停用/迁租拒绝与停用逻辑继续适用。无效cron/时区或某任务异常按任务隔离，新调度异常日志仅含任务ID及异常类型，不写原始Prompt/模型正文。

## 可复现验证

`tests/test_workflow_schedule_integration.py`通过真实_tick和真正启动的_loop跨越受控时间边界，保留真实Engine/Bridge/ChatService、隔离SQLite和合成模型；新Session读消息与执行记录，真实历史API核对。覆盖默认管线及可选编排、同槽重复、下一槽和手动历史不抑制定时。测试配置明确使用无需RAG的普通任务，并关闭异步反思及响应缓存来隔离非目标后台写入，未绕过身份/租户规则。

`tests/test_workflow_schedule_contract.py`由独立验证Agent编写，真实调度与原Engine落库，合成Bridge用于确定性失败注入。覆盖所有状态恢复、cache/数据库水位合并、不同租户和manual过滤、迟启动/长完成时间、最近一次、回退、撤权、旧快照、非法cron/时区、缺依赖及已说明DST案例。必需croniter缺失会失败，不把整份反例跳过。

真实红证据：独立Agent在未修复版本通过_tick→原Engine及隔离SQLite复现整点预期1条实际0条，1 failed、2.40秒、exit1。不是导入、环境或夹具失败。首修相关三文件42 passed、1条既有sandbox警告、11.11秒、exit0；Ruff全库no-cache及mypy app/218文件exit0。上述是中间证据，最终完整命令/环境/输入和日志指纹以docs/reviews机器证据为准。

## 明确未完成与范围限制

不新增失败重试、超时/取消功能、并发池、分布式锁或精确slot持久化；PWFL-001～004、FLOW-004/005不因此完成。不改前端、API/schema、模型表/迁移、配置、依赖、ChatService或默认开关。

记录created_at只能提供保守恢复水位，没有scheduled_for无法保证跨重启精确计划槽或恰好一次；扫描采用既有串行时间快照，没有实时SLA承诺。实际时间到记录落库之间跨槽延迟的恢复歧义保留，不伪装为完整幂等。

证据级别E2为真实应用/原执行器和隔离后端，模型为合成provider，只证明定时执行链路，不证明真实LLM回答质量或生产可用性。没有付费调用、生产数据、索引重建、合并或部署。用户已委托独立 Agent 做业务验收并通过；最终提交的三项 CI 返回前不能 done。

## 回滚

撤回最近到期helper与调度比较接线，恢复原调用；保留Workflow、WorkflowExecution、消息和审计，不删除或自动重放已执行任务，无数据库迁移。回滚到旧到点缺陷会使自动执行不再触发，应在运维记录中明确，而不把它称为正常定时能力。

独立工程首轮发现ENG-01扫描后计划变化仍执行（P2）和ENG-02租户失效仍启动（P1），已先记录真实探针及四条正式红，再在原cron入口最小修复。详见docs/reviews/2026-10-20-flow-003-review-fixes.md及engineering-review.md。初轮42绿不能替代这些修复后的最终验证；新测试另核对租户失效时真实ChatService没有模型调用或业务消息。

最终本地验证：无筛选pytest3082 passed、3 skipped、3既有warnings、704.69秒、exit0；任务专项65 passed、19.55秒、exit0；Ruff全库no-cache0，mypy app/218文件0。533冻结执行输入前后一致，完整命令与日志SHA256见同日flow-003-evidence.json。三项跳过为既有Windows CLI平台限制，新增工作流用例均实际执行未跳过。

未参与实现与既有审查的独立人工审核已通过，结论见 `docs/reviews/2026-10-20-flow-003-human-audit.md`：五个关键文件指纹与冻结证据一致；指定工作流三文件 48 passed、17.02秒、exit0；到点失败后同槽不重试，扫描后禁用或暂停不执行旧快照。无未修复 P1/P2。

用户于 2026-10-20 委托另一名未参与实现与既有审查的 Agent 做业务验收，结论见 `docs/reviews/2026-10-20-flow-003-business-acceptance.md`。集成测试 6 passed、4.23秒、exit0。真实 `_tick` 在到期前读到 0 条；跨过上海时间 10:01 后，新 Session 读到 1 条 `triggered_by=cron`、`status=success` 的执行记录，租户、输入和合成输出都在。业务验收通过。实现与本说明一并提交并推送。卡仍保持 `in_review`，最终提交的三项 CI 尚未返回。
