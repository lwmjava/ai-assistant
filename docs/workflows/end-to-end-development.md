# ai-assistant 端到端开发工作流：从需求到发布

> 本项目是已有实现的 Brownfield 系统。每一阶段必须形成证据；AI 不得在未读项目规则、未理解现状或未获必要批准时直接修改。`<填写...>` 表示任务级变量。

固定入口：

- `../../AGENTS.md`
- `../AI辅助开发迭代指导.md`
- `../governance/agent-harness-engineering.md`
- `../../tasks.yaml`
- `../checklists/`

## 0. 启动与工作边界

1. 读取 `AGENTS.md`、适用 Cursor Rules、治理规范、Definition of Done 和当前任务。
2. 确认请求模式：分析、计划、实现、修复、评审或发布；只执行获授权范围。
3. 记录工作目录、分支、未提交改动，保护用户已有修改。
4. 建立证据清单：需求来源、代码路径、运行日志、API/Schema、测试、监控、历史事故。
5. 输出影响范围和 Implementation Plan，获得项目要求的确认后实施。

阶段产物：`<填写计划路径/评审记录>`；退出条件：范围、Non-goals、风险和验收标准明确。

## 1. 需求发现与规格化

1. 先问业务目标、角色、场景、约束、失败成本和成功指标。
2. 分开记录：
   - 事实：有来源的现状；
   - 假设：待验证判断；
   - 决策：有负责人和理由的选择。
3. 编写 Product Spec：Vision、MVP、Non-goals、角色、主/备/失败场景、验收、风险。
4. 把“更智能/更快/更稳定”改写为基线、阈值和测量窗口。
5. 确认高风险业务动作、人工审批和数据权限边界。

退出条件：每项需求至少对应一个可执行验收标准和数据/行为证据。

## 2. Greenfield / Brownfield 分支

### ai-assistant Brownfield 路径

1. 不凭目录名猜架构；读取真实入口、构建文件、依赖、配置、调用链、数据库访问和测试。
2. 使用项目原生命令：后端 pytest/Ruff/mypy，前端 npm typecheck/build。
3. 记录 As-Is：模块、接口、事件、Schema、认证、错误码、事务、任务调度、生产流量和已知约束。
4. 找接缝：Adapter/Facade/Service/API/Event；避免大爆炸重写。
5. 建立 Characterization/Contract Test 固化当前业务行为，区分应保持的契约与待修复缺陷。
6. 选择旁路、适配器或绞杀者迁移；定义 Feature Flag、影子流量、双轨验证和回滚。

退出条件：兼容矩阵、回归基线和迁移路径明确。

## 3. 架构与契约

1. 编写 Context/Container/Component、正常与失败数据流、信任边界和 NFR。
2. 保持 Agent → Skill → Tool → Service → Repository → Database 依赖方向。
3. Agent 只生成结构化 Action；Harness 控制循环、状态、执行、权限、恢复、观测和评估。
4. 为 Tool 定义 Name、Description、Input/Output Schema、Permission、Side Effect、Timeout、Error Model、Audit。
5. 为 Skill 定义 Purpose、Preconditions、Workflow、Tools、RAG、Constraints、Escalation、Examples、Tests。
6. 架构、状态机、RAG、MCP 或审批策略变化写 ADR。

退出条件：关键接口可验证、失败路径可恢复、高风险动作不能绕过审批。

## 4. 任务拆分与排期

1. 把工作拆成可独立验证的任务；使用 `tasks.yaml` 记录依赖、范围、Non-goals、验收、风险、Evaluation 和文档。
2. 优先顺序：测试/契约 → 最小实现 → 集成 → 安全/失败路径 → Evaluation → 文档 → 发布。
3. 每个任务限制修改范围；跨模块变更明确协调点。
4. 禁止“实现全部功能”式任务；每个任务应在一个清晰能力完成后可验收。
5. 使用 `docs/templates/work-breakdown-and-schedule-template.md` 控制容量、关键路径和缓冲。
6. 夜间无人值守任务必须通过 `nightly_ready` 准入，并遵守 `docs/workflows/nightly-autonomous-development.md`；只交付草稿 PR，次日按 `docs/checklists/morning-review-checklist.md` 人工审核。

## 5. 测试驱动实施

1. 为新行为先写失败测试或可复现的 Evaluation Case，确认失败原因正确。
2. 做满足验收的最小实现，不顺手重构无关代码。
3. 按层验证：Unit → Contract/Integration → Agent/RAG → E2E/业务回归。
4. 副作用 Tool 验证权限、幂等、并发、部分失败、补偿和审计。
5. 每个 Agent Step 写 State 和 Trace；错误使用稳定分类，不泄露内部信息。
6. 若发现需求/架构假设错误，暂停实现，更新规格/ADR 并重新确认。

## 6. Evaluation 与真实业务验收

1. 建立基线版本和候选版本；冻结数据集、Prompt、模型、Tool、RAG 索引与运行配置。
2. 覆盖主路径、边界、攻击、依赖故障、人工升级和真实失败回归案例。
3. 评估 Task Success、Tool Selection/Arguments、RAG、正确性、引用、幻觉、安全、步骤、时延、成本、人工升级。
4. 与业务人员在脱敏真实案例中验收；不能只用 Mock 或“看起来合理”的回答。
5. 失败案例沉淀为测试/Evaluation Case；不得删除失败用例或降低门槛掩盖问题。

退出条件：总体和关键风险切片达到预设门槛，业务验收有记录。

## 7. 评审与防漂移

1. 审查需求—架构—任务—代码—测试—Evaluation—文档是否双向可追踪。
2. 审查 Agent/Prompt 是否复制了 Service 业务规则、绕过 Tool/权限或隐式表达状态。
3. 审查安全、数据权限、租户隔离、日志脱敏、依赖与供应链。
4. 审查存量契约、迁移和回滚是否仍成立。
5. 使用 review/release 与 anti-drift 清单，记录每个不通过项的证据和负责人。

## 8. 发布准备

1. 固定版本、构建产物、配置、Schema/索引/Prompt 版本和变更说明。
2. 运行完整质量门禁，保存命令、退出码和报告；禁止引用过期结果。
3. 准备灰度、Feature Flag、自动停止、数据回滚、应用回滚和沟通计划。
4. 验证 Dashboard、告警、On-call、Runbook、审计和人工审批链路。
5. 获得业务、技术、安全和发布授权。

## 9. 灰度发布与验证

1. 从内部/低风险/小流量开始；高风险副作用默认禁用或人工审批。
2. 对比业务成功率、错误、时延、成本、安全违规、人工升级和存量回归。
3. 达到停止阈值立即停止扩量并回滚；不得边观察边突破门槛。
4. 每阶段记录版本、流量、时段、指标、异常、决策人和下一步。

## 10. 全量与复盘

1. 全量后持续观察 `<填写监控窗口>`，确认没有延迟故障。
2. 关闭临时迁移路径前验证无流量、无依赖并获得批准。
3. 更新文档、ADR、运行手册、知识库和弃用说明。
4. 复盘需求偏差、事故、Evaluation 漏洞、成本和人工升级；形成可执行改进任务。
5. 用实际结果更新基线，不把一次成功视为长期保证。

## 11. 项目验证命令

后端基础门禁：

```powershell
pytest
ruff check .
mypy app/
```

RAG 聚焦回归：

```powershell
pytest tests/test_rag.py tests/test_rag_import_jobs.py tests/test_rag_backend.py tests/test_chunking.py -v
```

前端门禁：

```powershell
cd frontend
npm ci
npm run typecheck
npm run build
```

实际任务只运行与影响范围匹配的命令，但必须记录未运行项、原因和风险。
