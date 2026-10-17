# Agent 运行恢复回归契约 v1

类别：Observed Regression + Adversarial + Smoke，非 Gold。源于 2026-10-13 PAGT-006 代码评审的七项发现。模型为脚本提供商、工具为合成计数工具，确定性断言验证状态/权限/预算，不衡量 LLM 质量。

版本：`agent-runtime-recovery-v1`。主运行器 pytest；数据版本由测试文件 Git diff 标识，历史反例 JSON 保留评审基线现象，不改写为成功输出。

| 发现 | 正式回归 |
|---|---|
| F01 工具重复执行 | `test_live_checkpoint_resume_does_not_repeat_completed_tool`、`test_cancelled_live_stream_can_resume_without_repeating_tool` |
| F02 真实入口不可达 | `test_http_resume_reuses_message_without_quota_charge`、`test_http_resume_rejects_invalid_binding`、`test_active_stream_blocks_resume_and_close_releases_claim` |
| F03 预算重置 | `test_restored_execution_keeps_total_budget`、`test_invalid_intent_consumes_persisted_budget` |
| F04 成功短路未写终态 | `test_successful_short_path_has_terminal_checkpoint`、HTTP 恢复成功库终态断言 |
| F05 读异常逸出/损坏污染 | `test_read_failure_falls_back_to_normal_execution`、`test_malformed_snapshot_does_not_partially_mutate_state` |
| F06 严格类型漏检 | `test_strict_type_errors_never_call_function` |
| F07 实际清理缺失 | `test_expired_cleanup_is_bounded_and_preserves_fresh_rows`、`test_cleanup_lifecycle_runs_startup_and_periodic_batches` |

主用例位置：`tests/test_agent_runtime_recovery.py`、`tests/test_chat_resume.py`。基础失败边界仍由 `test_agent_state.py`、`test_tool_executor.py`、`test_agent_state_resume.py` 承载。

独立复审补充 R1/R2：恢复时重新检索当前身份可见内容并保留来源（流式/非流式）；数据库负轮次在 HTTP 显式恢复时拒绝。补充连续两次中断总预算、完整/拒答/异常终态、清理租户过滤与故障回滚。最终新增用例共 40 条；按 `RAG_BACKEND=native` 隔离本机后端配置后，加既有默认后端失败项共 41 passed，退出码 0。完整回归结果见本轮实现说明。

运行：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -m pytest tests/test_agent_runtime_recovery.py tests/test_chat_resume.py -q --basetemp=data/pytest-tmp-recovery-eval`。验收门槛为所有确定性断言通过，0 失败/0 跳过。证据为真实 Pipeline/Service/HTTP + 隔离 SQLite（E2），脚本模型不冒充真实生成评测。

未覆盖：真实客户身份、生产数据库、跨实例租约、工具副作用与快照提交之间全部断电窗口、真实付费模型。关闭任务需另行独立复审与用户业务验收；此文件不授予生产清理或发布授权。
