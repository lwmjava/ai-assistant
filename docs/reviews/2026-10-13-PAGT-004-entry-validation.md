# PAGT-004 独立入口验证

日期：2026-10-13。验证身份：`/root/pagt004_entry_validation`，未参与业务实现。
依据：原始任务卡的可解析步骤、依赖、预期产出、逐步执行与单步失败定位，以及用户批准的 ADR-0011 固定顺序契约。技能为 task-card-delivery v1.3.0 的 Workflow 5；验证不授权发布或代替未委托的人工业务判断。

## 实际路径与证据类别

新增 `tests/test_plan_entry_acceptance.py`；只替换脚本模型、合成原子工具和已认证主体输入，执行真实 FastAPI chat router → ChatService → AgentPipeline → ToolExecutor → 审计/Checkpoint/消息持久化。使用独占 `sqlite://` + StaticPool，不使用生产数据。取消测试先实际执行第一步并关闭 ChatService 流，再通过新的 HTTP 请求和 Session、原用户消息 ID 恢复，未以预置快照推断恢复成功。

证据为 E2 控制链路测试，数据为 AI 编写 Smoke/Adversarial，非 Gold。脚本 provider `entry-plan-script-v1`；合成工具契约 `entry-v1` / `entry-L2-v1`。默认工具预算 3、模型超时 0.05 秒、RAG 关闭、QualityGate 关闭、self 编排、Checkpoint 开启、租户消息限额 1。恢复预算反例将上限缩到已使用的一轮，以证明累计预算仍生效。

## 独立反例

| 要求/边界 | 实际断言 |
|---|---|
| 新运行成功与双入口 | 三个工具依赖步骤按 1/2/3 执行，成功前缀游标 3；同步 message_id/reply 与流式 message/done 契约；实际工具审计身份/租户绑定 |
| 部分成功后的业务失败 | 第二步 ToolOutput error，准确 step2/TOOL_FAILED（外部陌生错误码不外传），第三步 pending 且零调用；同步和流式不宣称成功 |
| 身份/租户与风险拒绝 | tool 参数伪造 foreign tenant；L2 工具限制管理员但当前 member；原 Executor 拒绝，工具零调用，拒绝原因及当前身份写实际审计，details 仅四个安全字段 |
| reason 计划外调用 | 有效 tool_call 信封在 reason 步仍拒绝，准确 step1/UNPLANNED_TOOL_CALL，后继 pending，工具零执行 |
| 模型超时 | reason 模型睡眠超过已配置超时，准确 step1/MODEL_TIMEOUT，后继 pending，工具零执行 |
| 取消后新 Session 恢复 | 真实第一步执行并提交 checkpoint，取消保留 cursor=1/used=1；同消息 API 恢复完成 2/3，不再规划，不重复第一步，用户消息仍仅一条且限额 1 可恢复 |
| 恢复预算不刷新 | 同一真实中断后预算设为已用 1，恢复 step2/TOOL_BUDGET_EXCEEDED；工具仍只有第一次调用，used=1 |
| 显式旧文本回滚 | 关闭结构化构造参数，真实服务生成文本计划并调用旧工具，正常回复，structured_plan=None |

## 命令与结果

使用声明解释器 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`，环境 `RAG_BACKEND=native`，pytest 全部串行。

1. 独占首次 `python -m pytest tests/test_plan_entry_acceptance.py -q --basetemp=data/pytest-tmp-plan-entry-20261013`：退出 1，4 failed / 9 passed，3.36 秒。四项是测试期待外部 business_failure/内部权限原始 reason，而实施者同步新增安全错误码白名单。核对原始契约后，改为公开稳定 `TOOL_FAILED`/`PERMISSION_DENIED`，审计仍精确断言原拒绝 reason；保留准确失败 step、后继 pending 和零调用要求。不是降低失败门禁。
2. 相同专项命令：退出 0，13 passed，2.77 秒。主 Agent 先前领域运行出现 calls 空，独占未复现，推断可能受全局 route service 的 provider 状态影响；fixture 改为显式注入独立真实 `ChatService(llm_provider=provider)`，消除该外部依赖，随后领域无此失败；未替换主执行链。
3. `python -m pytest -q -k 'agent or tool or chat or pipeline_budget or plan' --basetemp=data/pytest-tmp-plan-entry-domain-20261013`：退出 1，4 failed / 461 passed / 1486 deselected，29.83 秒；13 个独立入口全部通过。失败为主 Agent 新增的三条待修复反例（拒绝计划的敏感字段仍在终态快照、恢复参数 type/credential 未重校验），及 `tests/eval/test_rag006_pipeline.py::test_chat_service_rag_injects_facts_without_executing_dropped_injection`（未返回 NW-PRINT-X1 事实）。已通知主 Agent，不据此宣称整体任务通过；缺陷归属与后续修复验证由主 Agent记录。
4. `python -m ruff check --no-cache tests/test_plan_entry_acceptance.py`：退出 0，All checks passed。

本轮尝试前 1–3 的候选文件 SHA256（若后续变更，以最新重跑证据为准）：

| 文件 | SHA256 |
|---|---|
| app/agents/plan.py | 09181160c178539491fbd7056bd9beeb1f4c221631d5387c64f15efb11511f6f |
| app/agents/plan_runtime.py | 25ef359bcd46531c404e10d9cba2fc96030e607a2c98bedd9170b68ed8caaf9a |
| app/agents/pipeline.py | 211c14deeae981e79e05f9eaf15f975f193842036ea126e6bd7e09354c020eaf |
| app/agents/state.py | 5f2629b1b37966ef7522cb4913adf887c358c2dceba91243681bd5b0cd9b75fb |
| tests/test_plan_entry_acceptance.py | 9725f8b20ecae395263b393dea43d0c7d01d002cc4f1d9329f60fa295a0762a3 |
| docs/ai-prompts/skills/task-card-delivery/SKILL.md | 97248dec90e7b927da655e6c336dccd12ae4c634d3cc4ff9e8cc4e9dd806c2dd |

当前结论：独立入口契约本轮通过，领域存在未修复失败，本卡不应关闭。所有 pytest 时隙已释放给主 Agent；本角色没有修改业务代码。

## 未验证与限制

未调用真实模型、外部写工具或 Embedding；不证明真实生成质量。未跑浏览器/前端步骤 UI（本卡不交付），不覆盖 PostgreSQL、多实例、JWT 校验或 exactly-once。主体依赖显式注入，保留实际 RBAC、Service 归属与工具权限边界；无生产、迁移、Git 提交或部署操作。测试发现业务失败时通知主 Agent，不能改业务代码或降低断言。
