# ai-assistant 评审与发布检查清单

> 当前项目尚处治理建设阶段。不能满足的生产项必须标记阻断或登记有期限的例外，不能直接删除。

## 1. 需求和任务

- [ ] 关联 Product Spec、设计、ADR 和 `tasks.yaml`。
- [ ] 验收标准可执行。
- [ ] Non-goals 明确且未被实现。
- [ ] 业务、技术、安全和发布责任人明确。

## 2. 代码评审

- [ ] Diff 范围与任务一致。
- [ ] 公开 API/Event/Schema/错误码兼容。
- [ ] API、Service、Agent、Tool、RAG、MCP 分层合理。
- [ ] 无 Agent/Prompt/Skill/MCP 直连数据库。
- [ ] 无未批准依赖、架构或供应商变化。
- [ ] 无未解释的临时绕过和硬编码。

## 3. 权限和安全

- [ ] 身份、租户、角色、资源和动作权限均验证。
- [ ] 跨租户和 IDOR 测试通过。
- [ ] RAG/Tool/MCP/网页内容按不可信数据处理。
- [ ] L2/L3 策略和人工审批不可绕过。
- [ ] 密钥、PII、连接串和堆栈未泄露。
- [ ] 日志、Trace、审计和 Evaluation 数据已脱敏。

## 4. RAG

- [ ] 正式 VectorStore 摄取、检索、更新和删除一致。
- [ ] Embedding 模型、维度和索引版本兼容。
- [ ] 文档版本、生效时间和权限过滤正确。
- [ ] Citation 可回指 document/chunk/version/page/section。
- [ ] RAG baseline 和候选使用同一数据集。
- [ ] Recall、Ranking、Citation、正确性、幻觉和安全达到已批准门槛。

## 5. Agent、Skill、Tool、Workflow

- [ ] Tool Schema、权限、风险、超时、错误、审计和版本完整。
- [ ] 副作用 Tool 具备幂等、冲突和补偿。
- [ ] Skill Preconditions、Workflow、Constraints、Escalation 完整。
- [ ] Agent 步骤、Tool轮次、时间和成本受限。
- [ ] Workflow 状态、重试、取消、失败和恢复可验证。
- [ ] 最终业务状态正确。

## 6. 验证命令

按影响运行并记录：

```powershell
pytest
ruff check .
mypy app/
cd frontend
npm run typecheck
npm run build
```

- [ ] 实际命令、退出码和报告已保存。
- [ ] 未运行项有原因、风险、Owner 和截止日期。
- [ ] Mock 测试没有替代必要的真实集成。
- [ ] 失败案例已沉淀，未删除或弱化。

## 7. Evaluation

- [ ] 数据集、评分器和阈值有版本。
- [ ] Gold 有人工确认。
- [ ] 关键权限、安全和高风险切片为阻断门禁。
- [ ] 模型非确定性有重复运行或方差说明。
- [ ] 报告包含失败 Case 和残余风险。

## 8. 可观测性

- [ ] request/task/trace/step 可关联。
- [ ] 能区分模型、RAG、Tool、权限、数据和外部依赖失败。
- [ ] SLI/SLO、告警、Owner 和 Runbook 可用。
- [ ] Trace 和审计能还原高风险动作。

## 9. 发布和回滚

- [ ] 制品、代码、配置、Prompt、模型和索引版本固定。
- [ ] Feature Flag、灰度租户/流量和观察窗口明确。
- [ ] 自动/人工停止阈值明确。
- [ ] 应用、配置、数据库、模型和索引回滚已验证。
- [ ] 回滚不会重复副作用或破坏数据。
- [ ] 生产写入和高风险操作已取得授权。

## 10. 决策

- 版本/提交：
- 评审人：
- 业务验收：
- 安全验收：
- 未关闭问题：
- 已批准例外及到期日：
- 发布结论：`通过 / 条件通过 / 阻断`
