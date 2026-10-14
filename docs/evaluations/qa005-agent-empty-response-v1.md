# Agent 空响应回归 v1

Observed Regression 来源为 GitHub run 38548192922 的 agent-003 空工具提议；历史报告只能确认文本为空，不能确认服务端具体原因。冻结 Silver agent-chain-v0.1.1 的案例、期望值、提示和四项质量阈值保持；不新增 Gold，不用 holdout 调参。

评测器 agent-chain-eval-0.4.0、完整报告 agent-chain-report-3.0.0、门禁 agent-chain-gate-3.0.0。12个逻辑首请求，全轮最多一次符合条件的空响应恢复，实际12～13次；仅空白/null、stop/missing且无原生工具字段可恢复。相同模型、输入、选项，60秒逻辑调用超时含两次尝试，780秒全轮超时不重置。非空错误答案、截断、过滤、无效响应结构、HTTP失败和原生工具调用不恢复。

call_attempts 按10个route与2个tool_choice记录首轮和恢复；reply保存原始文本/null，error_code固定，finish_reason白名单，native_tool_calls仅布尔，不记录推理正文或原生工具参数。logical_requests固定12，model_requests为物理调用计数，empty_response_retries为0或1。独立门禁核对顺序、案例、计数、恢复资格和最终回复绑定，重算质量指标；最终仍空时另报empty_model_response，保守full路由不能把空文本伪装成通过。

异常中断保存独立 *.attempts.json（agent-chain-failed-attempts-1.0.0，mode=failed-attempts），保留已发请求与固定失败状态，不能当作完整official质量证据。正式报告或失败账本已存在时，在请求前拒绝覆盖。故障探针同步最终回复绑定但保留原始恢复尝试；只证明拦截机制，不证明模型质量。

确定性契约：tests/test_llm_response_validity.py，tests/eval/test_agent_eval_runner.py、test_agent_gate.py、test_agent_gate_entry.py。覆盖空/空白/null/畸形、正常文本保持、无隐式failover、全轮预算、同输入、禁止错误答案重试、原生旧字段、两次超时尝试落盘与已有账本零调用。真实评测仅执行用户授权的一轮，结果见实现说明。
