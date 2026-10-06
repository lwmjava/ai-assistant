# ADR 目录

> 状态：六份 Accepted ADR；架构批准不等于能力实现
> 更新日期：2026-10-05

本目录只存放已批准或评审中的架构决策记录。

## 已批准

ADR-0006：[保守清洗与到期删除补偿](0006-rag-cleaning-and-retention-compensation.md)，Accepted，2026-10-05 用户批准按 RAG-014 计划实施；真实数据迁移/删除未授权。

| 编号 | 文件 | 状态 | 决定 | 任务 |
|---|---|---|---|---|
| ADR-0001 | `0001-knowledge-base-permission.md` | Accepted | 租户共享当前版本；写操作成员仅自己的文档；资源 ACL 为 Planned | `RAG-002` |
| ADR-0002 | `0002-official-vectorstore.md` | Accepted | 正式 Local；Milvus 实验/Partial；评测固定 Local | `RAG-003` |
| ADR-0003 | `0003-effective-date-versioning.md` | Accepted | 默认只检索已生效的当前版本；带未来日期的查询才打开 scheduled 预告 | `RAG-006` |
| ADR-0004 | `0004-skill-scope-and-admin.md` | Accepted | 私有技能只对创建者生效；系统管理员可跨租户管理；系统全局技能启用后所有人可见可选用 | `SKILL-001` |

权限过滤与生效日期过滤实现在 `RAG-006`。本阶段不切换默认向量库。  
`RAG_EFFECTIVE_DATE_FILTER` 默认关闭；打开后才改变检索候选集。

## 分阶段实施中

| 编号 | 文件 | 状态 | 范围 |
|---|---|---|---|
| ADR-0005 | [0005-rag-adaptive-chunking-context-budget.md](0005-rag-adaptive-chunking-context-budget.md) | Accepted | 2026-10-05 用户批准分阶段实施；边界辅助与摘要默认关闭；真实迁移与生产启用另授权 |

## 格式

每份 ADR 至少包含：背景、事实、选项、决定、后果、迁移、验证和回滚。模板见 `docs/governance/agent-harness-engineering.md` §17。

## 非目标

- 不把 `docs/plans/` 中的历史方案当成已批准 ADR。
- 不把 Proposed 草稿当成已交付能力。
