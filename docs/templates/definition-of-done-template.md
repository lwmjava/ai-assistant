# Definition of Done 模板

> 仅当适用项均有证据，或“不适用”有明确理由和批准人时，任务才算完成。不得以“之后补充”“基本通过”代替证据。

## 1. 需求与范围

- [ ] 验收标准逐条可执行，并附结果：`<填写测试/演示/业务记录路径>`
- [ ] 范围内与 Non-goals 一致：`<填写核对证据>`
- [ ] 事实、假设和决策已分离，关键假设已验证：`<填写>`
- [ ] 新项目 MVP 或存量项目兼容边界已满足：`<填写>`

## 2. 架构与实现

- [ ] 依赖方向和模块边界符合项目规则：`<填写审查证据>`
- [ ] Agent 只做理解/规划/动作选择；执行经 Tool Executor：`<填写>`
- [ ] 业务规则位于 Tool/Service/Skill/Guardrail/State Machine，不只存在 Prompt：`<填写>`
- [ ] 数据访问经 Repository；MCP 未成为业务规则或权限绕过通道：`<填写>`
- [ ] 重要架构变化已新增/更新 ADR：`<路径或“不适用：原因”>`
- [ ] 无未解释临时绕过、硬编码环境值或空泛 TODO：`<填写扫描/评审证据>`

## 3. Tool / Skill / Agent / RAG

- [ ] Tool 契约含 Name、Description、Input/Output Schema、Permission、Side Effect、Timeout、Error Model、Audit：`<路径>`
- [ ] 副作用 Tool 有幂等、并发、补偿和审批策略：`<填写>`
- [ ] Skill 含 Purpose、Preconditions、Workflow、Tools、RAG、Constraints、Escalation、Examples、Tests：`<路径>`
- [ ] Agent 有显式状态、最大步数、重试上限、失败与恢复路径：`<填写>`
- [ ] Context 有来源标记和预算；Memory 写入经授权：`<填写>`
- [ ] RAG 有权限过滤、版本/有效期、引用和独立 Evaluation：`<填写或不适用原因>`

## 4. 安全与合规

- [ ] Input/Tool/Permission/Output Guard 已实现并验证：`<填写>`
- [ ] 高风险操作需要 Human-in-the-loop，无法绕过：`<填写测试证据>`
- [ ] 跨租户、IDOR、注入、越权、敏感信息泄露用例通过：`<填写>`
- [ ] 日志、Trace、错误、RAG、Memory 已脱敏：`<填写>`
- [ ] 秘密来自安全配置，不在代码、Prompt、测试数据或日志中：`<填写>`
- [ ] 依赖、镜像、许可证和供应链检查通过：`<填写>`

## 5. 测试与 Evaluation

- [ ] Unit：`<命令、结果、报告>`
- [ ] Integration/Contract：`<命令、结果、报告>`
- [ ] Agent Scenario/E2E：`<命令、结果、报告>`
- [ ] RAG Evaluation：`<命令、指标、结果或不适用原因>`
- [ ] 安全/权限/故障注入：`<命令、结果>`
- [ ] 存量 Java/Go/Node/PHP/.NET 关键业务回归：`<命令、真实业务结果>`
- [ ] Evaluation 达到预设门槛，无不可接受切片退化：`<结果链接>`
- [ ] 修复的失败案例已沉淀为测试/Evaluation Case：`<路径>`

## 6. 可观测性与运行

- [ ] 可按 trace_id 还原 task、agent_step、llm、rag、tool、guardrail、final_answer：`<填写>`
- [ ] 指标、日志、Trace 和审计可区分业务失败与系统失败：`<填写>`
- [ ] 告警阈值、负责人、处置手册可用：`<填写>`
- [ ] 容量、P95/P99、成本和依赖故障满足 NFR：`<报告>`
- [ ] Feature Flag、灰度、自动停止和回滚已演练：`<记录>`

## 7. 文档与交付

- [ ] PRD、架构、Agent/Tool/Skill/RAG、运行手册与代码一致：`<填写>`
- [ ] 配置、迁移、部署、回滚、故障处理说明可由非作者执行：`<演练记录>`
- [ ] 版本、变更说明、兼容性和弃用策略已记录：`<填写>`
- [ ] Code Review 问题已关闭或有明确风险接受：`<填写>`

## 8. 最终签署

- 交付版本/提交：`<填写>`
- 未完成项：`<无；如有则不能标记 Done，填写阻塞、风险和负责人>`
- 业务验收人：`<填写姓名/角色、日期>`
- 技术验收人：`<填写姓名/角色、日期>`
- 安全/合规验收：`<填写或“不适用：经谁确认、原因”>`
- 发布决策：`<通过/阻断>`；证据：`<填写>`
