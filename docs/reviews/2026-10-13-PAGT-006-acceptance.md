# PAGT-006 独立验收

日期：2026-10-13。验收对象为当前工作区最终实现，包含尚未提交的评审修复。用户明确要求启动一个 Agent 做验收；本 Agent 未参与业务实现，只新增本报告，没有修改业务代码、测试、任务状态或进行提交发布。

**结论：有条件通过。当前范围的独立工程验收通过，没有发现新的实现阻断项；用户业务体验及交付边界接受尚不能由脚本测试代替。建议将本报告作为关闭依据之一，待用户确认后关闭任务，当前保持 `in_review`。**

## 依据与方法

已读取 AGENTS.md、AI 辅助开发迭代指导、Agent Harness 治理规范、tasks.yaml 的本卡、适用 Cursor Rules（全局、架构、Agent Runtime、Tools、Testing、安全、RAG/Memory、可靠性、Brownfield）、ADR-0010、2026-10-13 修复计划和实现说明、原代码评审、独立复审与 `agent-runtime-recovery-v1` Evaluation。核对实际 Pipeline、StateManager、执行器、ChatService/API、checkpoint 服务及清理生命周期，与文档授权和限制对账。

采用真实 Pipeline/Service/HTTP 和 SQLite 测试数据，模型与计数工具为脚本替身；独立执行而非沿用实施者历史全绿。全部 Python 使用 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`；显式 `RAG_BACKEND=native` 与 `DATABASE_URL=sqlite:///./data/test_ai_assistant.db`。所有测试串行执行，没有并发共享测试库、没有访问业务数据库或真实付费模型。恢复专项也包含独占内存 SQLite。

## 本卡三条验收

| 验收标准 | 独立证据与结论 |
|---|---|
| 工具调用统一经 Tool Executor，含超时/重试/权限/审计 | 实际检查 Registry 委托和默认管线注入；执行器专项验证超时、幂等瞬时重试、非幂等不重试、身份伪造拒绝、聚合审计、严格参数零调用和兼容观测。通过。 |
| 编排中间状态由 State Manager 管理，长会话可续 | 真实动作循环生成 checkpoint 后中断，新 Session 恢复；服务/API 取消续跑不重复已完成工具、不重新扣消息配额，累计预算及连续两次中断有界。通过，恢复粒度为同一用户消息的工具链，不是跨轮任务重放。 |
| 现有对话主链行为不回归 | Agent/工具/Chat/配额/日志与预算领域回归实际 338 passed；普通请求兼容、流式事件、上下文预算、权限与日志回归通过。当前确定性回归通过，真实用户回复质量不由脚本模型证明。 |

75 条专项包含在 338 条领域回归内，不能相加为 413 条独立用例。

## 四项业务行为核对

1. **正常对话与工具兼容**：普通及工具主链的既有回归通过；calculator、datetime、code_sandbox 的 bound/legacy 观测守门通过。web_fetch 未新增 golden 沉淀或执行外网实测，该历史未覆盖项保持记录。没有运行实际控制台点击或真实生成质量评测。
2. **中断续跑**：真实服务取消后用 API 恢复的用例实际通过。原用户消息计数保持 1，配额上限 1 已使用时仍能恢复；完成工具调用次数不增加；总预算包含恢复前轮次，连续中断不刷新。当前 RAG 上下文重新组装、来源重建，旧快照正文被舍弃。证据是受控取消和 TestClient/ASGI，而非真实公网断线或操作系统断电。
3. **拒绝恢复**：除专项外，另用一次性 stdin 脚本对非流式与流式各 10 种条件实跑精确 HTTP 状态，并逐次断言工具零调用、不新增用户或助手消息。结果如下。正在执行同消息的 409 与关闭流后释放 claim 由已实际执行的正式专项覆盖。
4. **当前边界**：README、ADR 与实现明确 API 显式恢复、进程内互斥、无前端自动恢复按钮、不承诺工具副作用与 checkpoint 之间的 exactly-once。用户批准入口及范围扩展是事实；本 Agent 不代用户接受其业务后果，不把单实例证据升级成生产合格。

| 条件 | `/api/chat` | `/api/chat/stream` |
|---|---:|---:|
| 其他用户（系统管理员）、其他租户（系统管理员）、错误消息 | 各 404 | 各 404 |
| 原内容不一致 | 400 | 400 |
| done、过期、持久化关闭、损坏 JSON 字段、数据库负轮次 | 各 409 | 各 409 |
| 缺 conversation_id | 422 | 422 |

补强实测共 20 个请求，均为期望状态；工具调用 0、新用户消息 0、新助手消息 0。单次 fixture 的用户消息始终为 1。脚本重复使用现有 `resume_client.__wrapped__` 的合成环境并独立增加严格状态断言，不改测试文件；代码保留在本次 Agent 工具执行记录。

## 实际命令和结果

以下 `python` 均指声明的 conda 解释器，命令前均设置上述两项环境变量。

| 实际命令 | 退出码 | 结果 |
|---|---:|---|
| `python -m pytest tests/test_agent_runtime_recovery.py tests/test_chat_resume.py tests/test_tool_executor.py tests/test_agent_state.py tests/test_agent_state_resume.py -q --basetemp=data/pytest-tmp-pagt006-acceptance-20261013` | 0 | 75 passed，5.73 秒，1 条既有 ast 弃用警告 |
| `python -m pytest -k 'agent or tool or chat or quota or rag_log or pipeline_budget' -q --basetemp=data/pytest-tmp-pagt006-acceptance-domain-20261013` | 0 | 338 passed / 1486 deselected，34.05 秒，同一警告 |
| `@'<一次性 HTTP 拒绝矩阵脚本>'@ \| & 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' -`（PowerShell here-string 实际按多行执行） | 0 | 20 个精确状态与零副作用断言通过；既有 fitz 弃用与重复 Channel 登记警告，无断言失败 |

拒绝矩阵脚本的构造为：导入 `tests.conftest` 初始化测试库，遍历两个端点及表中十类条件，在每个独立 `pytest.MonkeyPatch.context()` 内开启现有 fixture；修改 actor、payload 或真实 checkpoint 行，发送请求并断言精确状态、计数工具空列表、原会话只有一条 user 且没有 assistant；最后 `gen.close()` 清理合成数据。脚本未作为文件写入业务仓库。

本 Agent 未重新执行全量 pytest、ruff、mypy、前端构建；实施说明中的 1824 passed 与静态/构建结果仅核对为上一轮记录，不标成本轮实际执行。不存在本轮失败或跳过用例被隐藏的情况。

## 阻断、未验证与关闭建议

当前已批准范围内未发现未解决 P1/P2，也没有需要修改代码的验收阻断项。原 F01–F07 和独立复审 R1/R2 对应正式恢复用例均已实际执行通过。终态、坏快照、读故障、严格参数、过期清理有界性、清理失败回滚及租户范围均有本轮专项证据。

没有验证：真实用户的端到端控制台体验、真实模型回复质量、外网 web_fetch、真实生产权限撤销、PostgreSQL 生产、多实例租约、进程被杀或断电的全部窗口。现有 Trace 为项目既有内存能力，不能宣称完整持久化重放。上述限制没有被测试替身填成生产结论。

**建议：工程验收可通过；若用户确认同消息 API 恢复与单实例边界满足本卡业务目标，可记录业务验收结论并将本卡改为 done。** 用户仅要求启动验收 Agent，不等于已经接受本报告结论或批准生产上线。本报告不自行关闭任务、不批准多实例/exactly-once、不授权迁移、生产清理或发布。

报告回滚仅删除本文件；本轮没有业务代码变更。实现回滚方案仍以修复实现说明和 ADR 为准。
