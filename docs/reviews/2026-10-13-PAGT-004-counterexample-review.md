# PAGT-004 入口级独立反例审查

日期：2026-10-13。审查身份：`/root/pagt004_plan_review`，参与过计划审查，未参与实现。授权角色是 task-card-delivery 的独立入口级反例审查，不能兼任最终人工审核 Agent。

历史发现状态：发现 P1/P2。2026-10-13 后续修复的当前独立复验见 [repair-recheck](2026-10-13-PAGT-004-repair-recheck.md)，CE01-07已通过；原审查者受usage limit未完成复验，由新的独立身份接续。以下保留发现时的原始证据，不当作当前未决缺陷。

原始契约：PAGT-004 交付可解析结构化步骤（依赖与预期产出）、逐步执行与具体步骤失败定位，不实现通用 DAG、不改五阶段。用户已批准固定顺序 B 方案及 Mock 范围扩展，ADR-0011 为 Accepted。当前 PAGT-006 的既有工作区修复不归为本卡交付。

## 缺陷清单与修复要求

| ID | 严重性 / 置信度 | 实际缺陷 | 修复与正式回归要求 |
|---|---|---|---|
| CE-01 | P1 / 10 | 新计划的敏感 arguments 被 `_prepare_structured_plan` 拒绝，但 `state.plan` 仍保留完整原始 JSON；run 错误出口将其写入 checkpoint。合成 password marker 实际进入 state_json | 拒绝前或终态持久化前清理不允许保存的完整原计划；结构、文本视图和全部相关快照字段不得落敏感值。真实 Pipeline + 隔离 DB 新 Session 读回反例，不能只 assert 工具零调用 |
| CE-02 | P1 / 10 | 恢复路径只校验通用 JSON/schema/progress，不复用新计划的注册工具、工具参数 schema 与敏感 payload 检查。恢复 wrong integer 参数实际调用一次并成功；恢复含敏感参数也实际调用并成功；第二步未知工具时先执行第一步才失败 | 在任何步骤执行前，对恢复计划复用完整执行绑定检查，失败不能降级新执行。覆盖注册表变化、非 strict 工具参数错误、敏感恢复载荷、未知后继工具，工具零调用 |
| CE-03 | P2 / 10 | `_needs_plan` 与 `_evaluate_quality` 未设置 runtime 超时。LLMOptions.timeout=.01 时注入睡眠 provider，真实 run 仍由 .1 秒外部 watchdog 终止 | 新结构化请求以及已有 v1 恢复路径，对 preflight/QualityGate 同样落实有界超时，保留显式 legacy 契约；同步/流式测试 runtime 处理，不靠测试 watchdog 作为成功证据 |
| CE-04 | P2 / 10 | 计划完成后的草稿若 provider 返回 None，`iterate_plan` 抛原始 TypeError，而非可定位安全的 PlanExecutionError；`failed_step_id` 为空 | 先验证草稿类型、空值和长度再解析工具意图；返回 draft 阶段安全代码，统一 run/run_stream 错误定位。reason 非字符串目前能准确定位 s1，应保留其行为 |
| CE-05 | P2 / 10 | 快照声称工具步 succeeded/cursor=1，但 tool_rounds_used=0 且无执行指纹，仍通过 StateManager 验证；真实恢复跳过工具，最后 error=None | 新 schema 快照需验证成功工具前缀与已用预算、对应指纹一致；DB权威轮次覆盖后不得接受矛盾状态。保留旧 checkpoint 兼容，反例最终不能伪造业务完成 |
| CE-06 | P2 / 10 | 真实注册的 file_ops 返回 JSON status=error/code=access_denied，Executor 仍报告 ok；Pipeline 把读取不存在文件标成 succeeded/cursor=1，error=None。web_search/file_ops/file_ops_write 产生处目前均仍返回普通 JSON 字符串 | 已治理工具的业务错误也在产生处分型为兼容 ToolOutput；保留 legacy JSON 文本/golden，error/unavailable/conflict 不能标步骤成功。empty/no_sources 是否属于成功需按原工具契约明确，不扫描任意外部工具字符串 |

CE-01/CE-02 在发现时已即时通知主 Agent。修复由主 Agent 落盘计划、补正式失败测试并实施；本审查不修改业务代码或正式 tests。

## 实际执行方式与结果

执行解释器：`D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`。PowerShell here-string 经 stdin 运行纯内存 Python，`DATABASE_URL=sqlite://`、`ENV=dev`；未运行 pytest，不使用共享测试库，未调用外部网络/模型、未读取真实秘密。合成 marker 仅用于判断泄漏布尔值，不输出敏感正文。QuietRegistry 仅把审计写入替换为内存边界；真实 Pipeline、StateManager、Executor 和工具函数仍实际运行，因此这里只证明 runtime 行为，真实数据库/HTTP验收由正式集成测试承担。

四组脚本均退出 0；脚本捕获缺陷不意味着业务断言通过。第一组使用浅引用快照，运行后再次验证出现 false；第二组改用 deepcopy 并在运行前记录校验结果，下表采用修正后的证据，不用错误测量证明拒绝。

| 反例 | 实际输出摘要 | 判定 |
|---|---|---|
| 新规划含 `password` 合成字段 | error=SENSITIVE_PLAN_ARGUMENT；checkpoint_count=1；secret_in_checkpoint=true | CE-01 复现 |
| 恢复工具 `count='wrong'`，schema 要求 integer，strict_validation 默认 false | accepted_before_run=true；tool_calls=1；error=null；cursor=1 | CE-02 复现 |
| 恢复第一步 demo、第二步 missing 工具 | accepted_before_run=true；tool_calls=1；error=步骤 s2 失败(tool_not_found)；cursor=1 | CE-02 复现，整计划绑定未先校验 |
| 恢复工具含合成 password 字段 | accepted_before_run=true；tool_calls=1；error=null；cursor=1；合成值进入 checkpoint | CE-02 复现 |
| QualityGate provider 睡眠 1 秒，timeout=.01，外部上限 .1 秒 | external_watchdog_needed=true | CE-03 复现 |
| preflight provider 睡眠 1 秒，timeout=.01，外部上限 .1 秒 | external_watchdog_needed=true | CE-03 复现 |
| 最终 stream provider 睡眠 1 秒，timeout=.01 | result=runtime_timeout | 正向故障控制通过 |
| reason provider 返回 dict | PlanExecutionError；s1 failed；failed_step_id=s1；安全 STEP_FAILED | 正向故障定位通过 |
| 完成步骤后的 draft 返回 None | TypeError: object of type NoneType has no len；failed_step_id=null | CE-04 复现 |
| succeeded 工具 cursor=1，无指纹且轮次=0 | snapshot_accepted=true；tool_calls=0；error=null；rounds=0；cursor=1 | CE-05 复现 |
| 真实 file_ops_tool 读取有效公开 docs 别名下不存在的合成文件 | error=null；step_status=succeeded；观测含 access_denied；cursor=1 | CE-06 复现 |

恢复反例使用 `StateManager(state).snapshot()`、深拷贝内存 store、实际 `Pipeline.run`；fresh 计划反例使用真实 `_prepare_structured_plan` 与终态 `_checkpoint`。工具采用合成原子函数计数，参数 schema 为 `count: integer`；没有自造替代计划解析器或替代步骤循环。

CE-06 为追加第五组脚本，退出 0；仅测试进程将 `FILE_OPS_ENABLED=true`、`FILE_OPS_ALLOWED_ROOTS` 设为现有 docs 公开别名，注册实际 file_ops_tool 并运行真实 func/Executor/Pipeline；请求合成不存在路径，无数据读取或文件写入。不得把这个结果描述成跨租户权限通过，它只证明业务错误误报成功。

## 走查范围与未验证项

实际读取 plan 模型、plan_runtime、Pipeline 全部新入口与质量门、StateManager、Tool/Executor/内置业务错误返回、Trace 脱敏、原卡和批准计划/ADR。恢复身份/租户的 DB/HTTP绑定不是本轮内存脚本的证明范围，需独立集成证据；不能从参数受限推导租户安全。

checkpoint 写入失败继续执行是既有 fail-open 持久化语义；恢复的断电/写失败窗口仍不得宣称 exactly-once。本轮尚未把该既有限制判成新增业务缺陷，也未证明写失败后的可恢复性。等待主 Agent 对当前交付保证明确登记。

尚未检查最终完整改动指纹与修复后入口；本报告当前只记录发现，不代表审查通过。完成修复后应在本报告追加实际复验结果、对应正式测试位置与最终指纹。
