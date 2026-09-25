# ADR 目录

> 状态：已预留，**尚无已批准 ADR**  
> 更新日期：2026-09-13

本目录只存放已批准或评审中的架构决策记录。创建目录不等于已经做出决策。

## 待决策（禁止在此文件中选择）

| 候选编号 | 主题 | 负责任务 | 当前约束 |
|---|---|---|---|
| ADR-KB | 知识库权限：租户共享 vs 用户/资源 ACL | `RAG-002` | 批准前不修改列表/检索过滤 |
| ADR-VS | 正式 VectorStore：Local vs Milvus | `RAG-003` | 批准前不切换默认 `RAG_VECTOR_STORE` |

## 格式

每份 ADR 至少包含：背景、事实、选项、决定、后果、迁移、验证和回滚。模板见 `docs/governance/agent-harness-engineering.md` §17。

## 非目标

- 不在本 README 中选定权限模式或正式向量库。
- 不把 `docs/plans/` 中的历史方案当成已批准 ADR。
