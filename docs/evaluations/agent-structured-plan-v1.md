# Agent structured-plan-v1 Evaluation

日期：2026-10-13。数据集/评测器版本：agent-structured-plan-v1 / pytest contract-v1。全部为人工指导下 AI 编写的 Smoke、Adversarial、Observed Regression；不是 Gold。不调用真实收费模型，不报告生成质量或生产延迟。

## 契约与可复现入口

| Case族 | 分级 | 正式用例与操作链 | 可观察断言 | 证据等级 |
|---|---|---|---|---|
| 顺序计划 | Smoke | test_plan_entry_acceptance.py，同步/流式真实 HTTP → ChatService → Pipeline → ToolExecutor → SQLite | 结构可解析、依赖/预期产出保留、工具顺序与成功游标、提交后新Session读回 | E2 |
| 步骤失败 | Adversarial | HTTP第三步前第二步失败、test_structured_plan_runtime.py实际calculator错误 | failed_step_id/error_code，成功前缀保留、后继零执行 | E2 |
| 权限租户 | Adversarial | HTTP伪造身份参数、L2未授权；真实ToolRegistry审计落库 | 公开安全码、真实审计reason、工具函数零执行 | E2 |
| 超时 | Adversarial/Observed Regression | 同步/流式preflight、reason、quality、response、tool注入超时 | Runtime明确error，不能被外部watchdog当成功 | E2 |
| 恢复幂等 | Smoke/Adversarial | stream取消→新Session同消息恢复，已完成前缀/总预算；参数错误/敏感字段/未知后继工具 | 前步不重做，预算不刷新；恢复绑定错误全计划零执行 | E2 |
| 敏感失败落库 | Observed Regression CE01 | 拒绝规划→终态Checkpoint→新Session读state_json | 合成凭据不保存、工具零调用 | E2 |
| 有界Schema | Adversarial | test_plan_schema.py strict parser/progress | NaN/Infinity、幅值、深度、重复JSON键、前后依赖、64KiB/16步边界 | E3补充 |
| 快照一致性 | Observed Regression CE05 | test_plan_checkpoint_consistency.py StateManager | 成功工具与预算/指纹、DB轮次一致；旧快照兼容 | E3补充，真实恢复E2另见上列 |
| 工具业务错误 | Observed Regression CE06 | test_plan_tool_outcomes.py真实文件读取/冲突、calculator/sandbox/HTTP产生处 | ToolOutput保留legacy文本、Executor失败status，Pipeline停止后继 | E2工具集成+Pipeline独立反例 |
| 回滚 | Smoke | HTTP显式legacy，旧脚本测试通过opt-in fixture，v1快照关闭新生成仍v1恢复 | 原文本链兼容，非法新规划不自动执行旧链 | E2 |

复现使用 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`，RAG_BACKEND=native，DATABASE_URL=sqlite:///./data/test_ai_assistant.db，pytest串行、独占basetemp。SQLite与合成provider只是隔离集成，Mock只证明离线链路。完整命令、最终结果及SHA256写入 implementation_pagt_004.md 和 closure_pagt_004_20261013.md；本页不把尚未完成全量检查写为通过。

Trace版本沿用现有接口，新增 plan:<id> start/end安全状态与错误码，不输出完整参数。Prompt SYSTEM_PLAN=v1（严格JSON），ToolOutput兼容旧字符串，Plan快照schema_version=1；实际文件指纹见最终证据清单。

未测：真实LLM规划遵循率、真实外部网络可用性、生产并发/性能、断电或checkpoint写失败的exactly-once，多实例。这些不属于批准原卡，不得由Mock推导。
