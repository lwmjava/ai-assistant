# ai-assistant 防漂移检查清单

> 建议每周、每次架构/RAG/Prompt/Tool/Skill 变更以及发布前执行。

## 1. 基线

- [ ] 当前提交、分支和未提交改动已记录。
- [ ] 已读取 `AGENTS.md`、产品/设计、治理规范和当前任务。
- [ ] 文档声明已区分 Implemented/Partial/Planned/Deferred。
- [ ] 结论来自代码、配置、测试、Trace 或运行证据，不凭文件名猜测。

## 2. 需求和范围

- [ ] Diff 只覆盖 `tasks.yaml` 的允许路径。
- [ ] Non-goals 没有被实现。
- [ ] 新增能力在产品或任务中有来源。
- [ ] 未批准决策没有被 AI 静默选择。
- [ ] 临时修复没有变成永久架构。

## 3. 架构

- [ ] API 路由没有新增业务规则。
- [ ] Agent/Prompt/Skill 没有直接访问数据库。
- [ ] MCP 没有绕过 Tool/Service/权限。
- [ ] `ChatService` 和 `AgentPipeline` 的职责没有无计划继续膨胀。
- [ ] 新代码没有复制 Service 中的业务规则。
- [ ] 架构变化已更新 ADR 和治理映射。

## 4. RAG

- [ ] `RAG_ENABLED`、Backend、VectorStore、Embedding 和索引实际版本已记录。
- [ ] Local/Milvus 文档声明与真实主链一致。
- [ ] Chunking/RRF/Top-K/Rerank 变化使用同一 Evaluation 比较。
- [ ] 权限过滤覆盖 tenant、user/resource 和版本。
- [ ] RAG Context 标记为不可信。
- [ ] Citation 能回指实际文档和版本。
- [ ] Memory 和 RAG Context 没有互相覆盖。
- [ ] Mock Embedding 未被用来证明生产质量。

## 5. Agent、Tool、Skill、Workflow

- [ ] Tool 名称、Schema、风险、权限、错误和实现一致。
- [ ] Skill Manifest、加载器、Prompt 和测试一致。
- [ ] Workflow 阶段/状态、超时、重试和最终业务状态一致。
- [ ] 高风险动作仍进入人工审核。
- [ ] 最大步骤和 Tool 轮次没有被静默放宽。
- [ ] 失败、取消和恢复声明没有超过当前真实能力。

## 6. Evaluation

- [ ] 数据集、Prompt、模型、Tool、Skill、索引和评分器均有版本。
- [ ] AI 生成数据仍标记 Silver/Adversarial，未冒充 Gold。
- [ ] Gold 有人工审核记录。
- [ ] Development/Validation/Holdout 没有样本泄漏。
- [ ] 失败 Case 没有被删除或弱化。
- [ ] 总体提升没有掩盖权限、安全和高风险切片退化。
- [ ] 报告包含失败、未验证项和残余风险。

## 7. 文档和运行

- [ ] README、`AGENTS.md`、产品/设计和代码目录一致。
- [ ] 配置文档与 `app/core/config.py`、`.env.example` 一致。
- [ ] API/Schema 变化已更新 Swagger 和前端契约。
- [ ] Prompt、Workflow、Checklist 和 `tasks.yaml` 引用路径存在。
- [ ] 运行命令在当前仓库真实可用。
- [ ] 旧文档没有继续宣称过期能力。

## 8. 结论

- 检查提交：
- 检查日期：
- 执行人：
- 已确认漂移：
- 待验证风险：
- 阻断项：
- 修复任务 ID：
- 结论：`通过 / 修改后再审 / 阻断`
