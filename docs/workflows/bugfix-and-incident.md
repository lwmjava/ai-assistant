# ai-assistant Bug 修复与线上事故工作流

> 核心原则：真实证据优先、先复现、先写失败测试、定位根因后做最小修复，并以业务回归而非“代码能编译”收尾。未经授权不得直接操作生产、覆盖数据或执行高风险动作。

固定必读：

- `../../AGENTS.md`
- `../governance/agent-harness-engineering.md`
- `../checklists/review-and-release-checklist.md`
- 当前任务、事故记录、相关代码、测试和最近变更

## 1. 分级与止损

- 事件编号：`<填写>`
- 影响：`<填写用户/租户/订单/资金/数据/时间范围及证据>`
- 严重度：`<填写项目分级和理由>`
- 当前版本/环境：`<填写提交、镜像、配置、模型、Prompt、RAG 索引版本>`
- 负责人/沟通频道：`<填写>`

若存在数据破坏、安全泄露、资金风险或持续扩大影响：先按预案隔离流量、禁用 Feature Flag、切只读、回滚或转人工；每项生产写操作需审批和审计。

## 2. 收集真实证据

按 trace_id/request_id/user-safe identifier 关联：

- 用户输入与期望（脱敏）：`<填写>`
- 实际输出/错误码：`<填写>`
- 状态迁移：`<填写>`
- LLM/Prompt/模型配置：`<填写版本，不记录秘密>`
- RAG 查询、过滤、候选、重排、引用：`<填写>`
- Tool 请求/响应、重试、幂等、审批：`<填写>`
- Service/Repository/外部依赖日志与指标：`<填写>`
- 部署、配置、Schema、数据或流量变化：`<填写>`

把内容分为“已观察事实”和“待证假设”。不得仅凭报错文本、截图或模型回答猜根因。

## 3. 复现

1. 在安全、可控环境用最小输入复现；生产数据必须脱敏或构造等价数据。
2. 固定版本、配置、时间、随机种子、模型、Prompt、Tool Mock/真实依赖和 RAG 快照。
3. 从用户入口复现一次，再逐层缩小至 Agent/Skill/Tool/Service/Repository 或基础设施。
4. 对非确定性问题重复运行并记录频率与分布。
5. 无法复现时记录尝试、环境差异和需要增加的观测点，不宣布“偶发已恢复”。

复现命令：`<填写可复制命令>`；复现证据：`<填写日志/测试/trace 路径>`。

## 4. 先写失败测试

选择最接近根因且能防回归的层级：

- Unit：纯逻辑、校验、状态迁移、错误映射。
- Contract/Integration：Tool/Service/Repository/MCP/上游契约。
- Agent/Evaluation：工具选择、参数、状态、引用、升级和安全行为。
- E2E/业务：真实入口与关键业务结果。
- Characterization：存量 Java/Go/Node/PHP/.NET 的既有行为。

要求：测试在修复前因同一缺陷失败；排除测试本身、环境或脆弱 Mock 导致的失败。记录：`<测试路径、命令、失败输出>`。

## 5. 根因分析

沿证据链验证，不把相关性当因果：

1. 首个错误状态在哪里产生？
2. 哪个不变量被破坏？谁应负责维护？
3. 为什么现有校验、Guardrail、测试、监控或 Evaluation 没有拦截？
4. 是代码、配置、数据、依赖、并发、模型、Prompt、RAG、权限还是发布过程导致？
5. 最近变更只是触发条件还是根因？

根因陈述格式：`当 <前置条件> 时，<组件> 因 <机制> 破坏 <不变量>，导致 <可观察影响>；证据为 <链接/日志/测试>。`

## 6. 最小修复

1. 只修改恢复不变量所需的最小范围；无关重构另立任务。
2. 在责任层修复：业务规则进 Service/Tool/Skill/Guardrail/State Machine，不用 Prompt 补丁掩盖。
3. 保持公开契约兼容；需破坏性变更时走版本化、迁移和 ADR。
4. 副作用路径必须验证幂等、并发、部分失败、补偿和审批。
5. 增加必要观测点，但日志不得包含敏感信息或内部秘密。

修改范围：`<填写文件/模块>`；明确 Non-goals：`<填写>`。

## 7. 分层验证

1. 运行新增失败测试，确认转绿。
2. 运行受影响模块单元/集成/契约测试。
3. 运行 Agent/RAG/Safety Evaluation，比较修复前后指标和关键切片。
4. 运行存量关键业务回归：不仅看 HTTP 200，还核对订单、账单、库存、权限、事件、审计等业务结果。
5. 重放脱敏生产案例或影子流量，禁止真实副作用。
6. 验证反例：正常输入、边界输入、无权限、重复请求、依赖故障、超时和取消。

验证证据：`<命令、退出码、报告、trace>`；未执行项：`<原因、风险、补齐责任人和时间>`。

## 8. 发布与事故恢复

- 发布方式：`<热修/灰度/配置/模型或索引回退>`
- 停止阈值：`<错误率、安全、业务损失、时延等>`
- 回滚步骤：`<可复制且已演练>`
- 数据修复：`<影响集识别、脚本 dry-run、审批、核对、审计>`
- 观察窗口：`<填写>`

确认技术恢复、业务恢复和数据恢复三者分别完成；状态码恢复不等于业务已恢复。

## 9. 复盘与长期措施

- 时间线：`<事件、证据、决策和负责人>`
- 根因与促成因素：`<填写>`
- 探测缺口：`<为何监控未及时发现>`
- 测试/Evaluation 缺口：`<新增 case 路径>`
- 防复发措施：`<具体任务、优先级、负责人、验收和截止时间>`
- 文档/Runbook/ADR 更新：`<路径>`

禁止写“加强测试/加强监控”而无具体指标、场景、负责人和验收方式。

## 10. ai-assistant 定界参考

按首个错误状态将问题优先归类：

| 类型 | 重点证据 |
|---|---|
| API/认证 | `app/api/`、`app/core/security.py`、请求/错误码 |
| Chat 编排 | `app/services/chat_service.py`、会话和持久化 |
| Agent | `app/agents/pipeline.py`、Supervisor、Prompt、阶段事件 |
| Tool/Skill | `app/agents/tools/`、`app/agents/skills/` |
| RAG | Parser、Chunk、Embedding、VectorStore、过滤、引用 |
| Memory | 组装顺序、预算、覆盖与压缩 |
| Security | 输入、注入、权限、输出过滤 |
| Workflow | 调度、Bridge、重复执行和最终状态 |
| 外部依赖 | LLM、Embedding、Milvus、MCP 的超时和错误映射 |

常用回归命令：

```powershell
pytest tests/<最小复现测试>.py -v
pytest tests/test_chat.py tests/test_agent.py tests/test_tools.py -v
pytest tests/test_rag.py tests/test_rag_import_jobs.py tests/test_rag_backend.py -v
ruff check .
mypy app/
```

前端相关问题：

```powershell
cd frontend
npm run typecheck
npm run build
```

只执行真实存在并与影响范围匹配的命令。修复前失败和修复后通过必须使用同一复现证据。
