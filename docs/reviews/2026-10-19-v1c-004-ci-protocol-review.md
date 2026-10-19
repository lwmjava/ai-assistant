# 工具协议修复独立审查

日期：2026-10-19。审查身份：独立 Agent `/root/ci_protocol_review`，未参与实现。方式：只读核对计划、失败调查、源码、入口和最终三项功能 diff；为避免共享测试库竞争未自行运行 pytest。

结论：当前修复未发现 P1/P2，可按用户授权保存为待真实 CI 验收的提交。该结论不等于真实模型恢复或任务关闭。

- SYSTEM_ACT 明确完整应用层 JSON 信封，禁止供应商私有 DSML 标签，未扩大解析支持、工具权限或评测门槛。
- official 入口离线反例验证 DSML 保持 0.5 失败、标准信封达到 1.0，并保持 12 次请求、不为错误答案重试采样。
- runtime 与 official 的公共系统提示一致，Evaluation 1.0.1 明确合成证据，不冒充真实模型质量验证。
- 入口审查确认本轮不修改 inspect_tool_call；提示禁止 Markdown 或 DSML 不代表解析器新增对应检测。混合标签和 Markdown 包裹的历史解析行为不在本轮变更范围，不得据新提示声明已有拒绝能力。
- 建议同步任务允许路径与本地提交授权，主 Agent 已按用户批准登记。最终 full pytest、Ruff/mypy 由主 Agent 收口；同最终提交的真实 official CI 尚待通过。

审查结论以本轮 prompts.py、test_agent_eval_runner.py 和 Evaluation YAML 的冻结摘要绑定，摘要见本轮 ci-protocol-evidence.json。审查后这些输入如变化需重新审查。
