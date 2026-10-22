# ADR 目录

> 状态：Accepted ADR 目录；架构批准不等于能力实现
> 更新日期：2026-10-19

本目录只存放已批准或评审中的架构决策记录。

## 已批准

ADR-0006：[保守清洗与到期删除补偿](0006-rag-cleaning-and-retention-compensation.md)，Accepted，2026-10-05 用户批准按 RAG-014 计划实施；真实数据迁移/删除未授权。

| 编号 | 文件 | 状态 | 决定 | 任务 |
|---|---|---|---|---|
| ADR-0001 | `0001-knowledge-base-permission.md` | Accepted | 默认 tenant；可选 uploader 检索隔离；控制面权限按第 4 节；引用核验见 ADR-0007；资源 ACL 为 Planned | `RAG-002`、`RAG-026/027` |
| ADR-0002 | `0002-official-vectorstore.md` | Accepted | 正式目标及当前源码默认 Milvus，仍 Partial；冻结评测保持 Local；索引切换见 ADR-0008，启动及实例绑定见 ADR-0019 | `RAG-003`、`RAG-015/032/036`、`VDB-001/002` |
| ADR-0003 | `0003-effective-date-versioning.md` | Accepted | 默认只检索已生效的当前版本；带未来日期的查询才打开 scheduled 预告 | `RAG-006` |
| ADR-0004 | `0004-skill-scope-and-admin.md` | Accepted | 私有技能只对创建者生效；系统管理员可跨租户管理；系统全局技能启用后所有人可见可选用 | `SKILL-001` |
| ADR-0006 | [0006-rag-cleaning-and-retention-compensation.md](0006-rag-cleaning-and-retention-compensation.md) | Accepted | 保守清洗、原始快照和到期清理补偿 | `RAG-014` |
| ADR-0007 | [0007-rag-source-verification-permission.md](0007-rag-source-verification-permission.md) | Accepted | 有权命中块只读核验，不扩大整文件下载/管理权；功能仍 Planned | `RAG-027` |
| ADR-0008 | [0008-embedding-index-identity-and-switching.md](0008-embedding-index-identity-and-switching.md) | Accepted | 索引身份、准备后切换、旧索引回退；实际重要数据重建另授权 | `RAG-032` |
| ADR-0010 | [0010-agent-runtime-state-boundary.md](0010-agent-runtime-state-boundary.md) | Accepted | 独立 Tool Executor（校验/超时/有限重试/审计/结果归一化）+ StateManager 包装 AgentState；新表 `agent_run_states` 持久化 checkpoint；新表位置在 `app/models/`、待扩展 allowed_paths 批准 | `PAGT-006` |
| ADR-0011 | [0011-structured-sequential-agent-plan.md](0011-structured-sequential-agent-plan.md) | Accepted | 固定顺序 Plan v1、显式前向执行依赖、逐步状态与恢复；旧文本显式回滚。自动证据不等于人工业务验收 | `PAGT-004` |

权限过滤与生效日期过滤实现在 `RAG-006`。默认切换的最新批准与实际状态见ADR-0002第11节及VDB三卡验收；不以历史阶段表述覆盖当前源码。  
`RAG_EFFECTIVE_DATE_FILTER` 默认关闭；打开后才改变检索候选集。

ADR-0012：[SSE ActiveRun 生命周期与断线回放](0012-sse-active-run-lifecycle.md)，Accepted，2026-10-15 用户批准单进程任务所有权、有界事件回放与鉴权查询/取消；实现与验收以 SSE-001 证据为准。

## 分阶段实施中

| 编号 | 文件 | 状态 | 范围 |
|---|---|---|---|
| ADR-0005 | [0005-rag-adaptive-chunking-context-budget.md](0005-rag-adaptive-chunking-context-budget.md) | Accepted | 2026-10-05 分阶段架构批准；2026-10-06 补充模型预算、来源与摘要生命周期决定；具体费用/质量数值仍须证据；边界辅助与摘要默认关闭 |

## 单实例导入技术细化

[ADR-0009：单实例导入抢占、恢复与发布凭据](0009-single-instance-import-recovery.md)，2026-10-10，Accepted（已批准单实例 RAG-039 范围内的技术细化）。不授权生产操作或迁移，验收以单卡证据为准。

## 对话检索依赖生命周期

ADR-0013：[对话检索依赖按使用时机创建](0013-chat-retrieval-dependency-lifecycle.md)，Accepted，2026-10-18；用户批准按 V1C-001 实施计划和具体范围推进。对话层请求内惰性依赖，不改变既有默认检索策略或知识库读域；实现与验收以本卡证据为准。

## 技能运行时绑定

ADR-0014：[技能快照、运行时工具约束与恢复绑定](0014-skill-runtime-binding.md)，**Accepted**，2026-10-19用户明确批准V1C-003实施计划、允许路径与本决定；架构批准不代表实现验收通过。

## Supervisor 受控工具委派

ADR-0015：[Supervisor 研究节点受控委派工具行动](0015-supervisor-controlled-tool-delegation.md)，**Accepted**，2026-10-19；用户已明确批准计划、允许路径及 ADR-0015，架构批准不代表实现验收通过。

## 有界知识库与工具组合

ADR-0016：[有界知识库与工具组合](0016-bounded-knowledge-tool-composition.md)，**Accepted**，2026-10-19。用户明确批准V1C-005实施计划、允许路径、组合接线、来源参数门禁、首轮计算器支持矩阵和恢复限制；批准不代表已实现或验收通过。

## 格式

ADR-0017：[工作流执行超时与协作取消](0017-workflow-execution-cancellation.md)，Accepted，2026-10-20；用户批准PWFL-002具体实施计划及最小扩围。单进程合作取消、每任务持久化超时、唯一执行所有者收尾；不改执行历史表结构，不表示业务完成。

每份 ADR 至少包含：背景、事实、选项、决定、后果、迁移、验证和回滚。模板见 `docs/governance/agent-harness-engineering.md` §17。

## 非目标

- 不把 `docs/plans/` 中的历史方案当成已批准 ADR。
- 不把 Proposed 草稿当成已交付能力。

ADR-0018：[工作流单进程准入与周期幂等](0018-workflow-concurrency-idempotency.md)，Accepted，2026-10-20；用户批准共享容量、周期消费语义及最小扩围，批准不代表已实现。

ADR-0019：[开发环境 Milvus Lite 启动选择](0019-milvus-lite-runtime-selection.md)，Accepted，2026-10-21；只锁定启动一次的实例选择和归属拒绝。Windows 上尚未证明 Lite 运行库可用。

ADR-0020：[原生结构化工具提议](0020-native-tool-proposals.md)，Accepted，2026-10-22。行动阶段通过兼容接口原生 tools/tool_calls 提议单个函数调用，客户端严格校验后归一为内部执行信封；不放宽文本协议解析。架构批准不代表评测或生产发布完成。
