# ADR 目录

> 状态：八份 Accepted ADR；架构批准不等于能力实现
> 更新日期：2026-10-06

本目录只存放已批准或评审中的架构决策记录。

## 已批准

ADR-0006：[保守清洗与到期删除补偿](0006-rag-cleaning-and-retention-compensation.md)，Accepted，2026-10-05 用户批准按 RAG-014 计划实施；真实数据迁移/删除未授权。

| 编号 | 文件 | 状态 | 决定 | 任务 |
|---|---|---|---|---|
| ADR-0001 | `0001-knowledge-base-permission.md` | Accepted | 默认 tenant；可选 uploader 检索隔离；控制面权限按第 4 节；引用核验见 ADR-0007；资源 ACL 为 Planned | `RAG-002`、`RAG-026/027` |
| ADR-0002 | `0002-official-vectorstore.md` | Accepted | 正式目标 Milvus、当前默认 Local；Milvus 仍 Partial；冻结评测保持 Local；索引切换见 ADR-0008 | `RAG-003`、`RAG-015/032/036` |
| ADR-0003 | `0003-effective-date-versioning.md` | Accepted | 默认只检索已生效的当前版本；带未来日期的查询才打开 scheduled 预告 | `RAG-006` |
| ADR-0004 | `0004-skill-scope-and-admin.md` | Accepted | 私有技能只对创建者生效；系统管理员可跨租户管理；系统全局技能启用后所有人可见可选用 | `SKILL-001` |
| ADR-0006 | [0006-rag-cleaning-and-retention-compensation.md](0006-rag-cleaning-and-retention-compensation.md) | Accepted | 保守清洗、原始快照和到期清理补偿 | `RAG-014` |
| ADR-0007 | [0007-rag-source-verification-permission.md](0007-rag-source-verification-permission.md) | Accepted | 有权命中块只读核验，不扩大整文件下载/管理权；功能仍 Planned | `RAG-027` |
| ADR-0008 | [0008-embedding-index-identity-and-switching.md](0008-embedding-index-identity-and-switching.md) | Accepted | 索引身份、准备后切换、旧索引回退；实际重要数据重建另授权 | `RAG-032` |

权限过滤与生效日期过滤实现在 `RAG-006`。本阶段不切换默认向量库。  
`RAG_EFFECTIVE_DATE_FILTER` 默认关闭；打开后才改变检索候选集。

## 分阶段实施中

| 编号 | 文件 | 状态 | 范围 |
|---|---|---|---|
| ADR-0005 | [0005-rag-adaptive-chunking-context-budget.md](0005-rag-adaptive-chunking-context-budget.md) | Accepted | 2026-10-05 分阶段架构批准；2026-10-06 补充模型预算、来源与摘要生命周期决定；具体费用/质量数值仍须证据；边界辅助与摘要默认关闭 |

## 单实例导入技术细化

[ADR-0009：单实例导入抢占、恢复与发布凭据](0009-single-instance-import-recovery.md)，2026-10-10，Accepted（已批准单实例 RAG-039 范围内的技术细化）。不授权生产操作或迁移，验收以单卡证据为准。

## 格式

每份 ADR 至少包含：背景、事实、选项、决定、后果、迁移、验证和回滚。模板见 `docs/governance/agent-harness-engineering.md` §17。

## 非目标

- 不把 `docs/plans/` 中的历史方案当成已批准 ADR。
- 不把 Proposed 草稿当成已交付能力。
