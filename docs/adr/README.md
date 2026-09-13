# ADR 目录

> 状态：已有两份 Accepted ADR
> 更新日期：2026-09-13

本目录只存放已批准或评审中的架构决策记录。

## 已批准

| 编号 | 文件 | 状态 | 决定 | 任务 |
|---|---|---|---|---|
| ADR-0001 | `0001-knowledge-base-permission.md` | Accepted | 租户共享当前版本；写操作成员仅自己的文档 | `RAG-002` |
| ADR-0002 | `0002-official-vectorstore.md` | Accepted | 正式 Local；Milvus 实验/Partial；评测固定 Local | `RAG-003` |

权限过滤实现在 `RAG-006`。本阶段不切换默认向量库。

## 格式

每份 ADR 至少包含：背景、事实、选项、决定、后果、迁移、验证和回滚。模板见 `docs/governance/agent-harness-engineering.md` §17。

## 非目标

- 不把 `docs/plans/` 中的历史方案当成已批准 ADR。
- 不把 Proposed 草稿当成已交付能力。
