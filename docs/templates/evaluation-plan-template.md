# ai-assistant Evaluation Plan 模板

> Evaluation 是发布门禁，不是演示脚本。数据、评分器、运行配置和结果必须可复现。所有阈值写明依据；没有基线时先建立基线，不得猜测。

## 0. 项目默认值

- 项目：`ai-assistant`
- 模式：`Brownfield`
- 规则：`../../AGENTS.md`
- 治理规范：`../governance/agent-harness-engineering.md`
- 推进指导：`../AI辅助开发迭代指导.md`
- 后端：Python 3.11+ / FastAPI / SQLModel / pytest
- 前端：React / TypeScript / Vite
- 默认 Agent：`AGENT_ORCHESTRATION=self`
- 默认 RAG：`RAG_BACKEND=native`
- 默认 VectorStore：`RAG_VECTOR_STORE=local`
- 默认 RAG 注入：`RAG_ENABLED=false`
- 当前 Evaluation 基线：尚未建立，任何具体阈值都必须先通过实测确定

本模板复制为实际计划后，保存到 `docs/evaluations/<对象>-evaluation-plan.md` 或项目批准的等价路径。不要直接在本模板记录某次运行结果。

## 1. 评估对象

- 能力/版本：`<填写 Agent/Prompt/Skill/Tool/RAG/Model/Guardrail 版本>`
- 变更摘要：`<填写具体行为变化>`
- 风险假设：`<填写最可能退化或造成业务损失的路径>`
- 关联需求/ADR/代码：`<填写链接或路径>`

当前推荐优先级：

1. RAG Retrieval/Citation baseline。
2. 知识库权限与 Prompt Injection。
3. Chat + RAG 主链。
4. AgentPipeline 的 Tool、Skill 和阶段行为。
5. Workflow、恢复和最终业务状态。

一次计划只选择一个主要评估对象；RAG 参数实验一次只改变一个主要变量。

## 2. 目标与发布问题

- 要证明：`<填写可证伪命题>`
- 不在本轮证明：`<填写范围外事项>`
- 发布判断：`<填写通过、阻断、需人工评审的明确规则>`

## 3. 数据集设计

| 数据集 | 来源 | 数量/分布 | 脱敏 | 标签方法 | 版本 | 用途 |
|---|---|---|---|---|---|---|
| `<Gold>` | `<权威文档/人工确认案例>` | `<填写>` | `<填写>` | `<业务/技术负责人>` | `<填写>` | `<发布门禁/回归>` |
| `<Silver>` | `<AI基于权威资料生成>` | `<填写>` | `<填写>` | `<独立AI+规则+抽检>` | `<填写>` | `<开发/扩展>` |
| `<Adversarial>` | `<威胁模型/失败路径>` | `<填写>` | `不得含真实秘密` | `<安全规则+人工>` | `<填写>` | `<权限/安全/恢复>` |
| `<Observed Regression>` | `<脱敏工单/日志/Trace>` | `<填写>` | `<授权和去标识化>` | `<根因证据+负责人>` | `<填写>` | `<真实失败回归>` |
| `<Smoke>` | `<完全虚构fixture>` | `<填写>` | `不含真实敏感数据` | `<确定性规则>` | `<填写>` | `<仅验证程序链路>` |

必须覆盖：主路径、缺参、歧义、无权限、跨租户、Prompt Injection、工具故障、超时、冲突、过期知识、证据冲突、需人工升级、存量业务回归。训练/调优数据与最终测试集隔离。

当没有现成 Gold 数据集时，采用：

```text
盘点权威资料
→ AI生成Silver候选
→ 独立AI重新读取原始资料复核
→ 程序校验Schema、引用、版本、权限和重复
→ 有权负责人抽查/修订
→ 冻结Gold v0.x
→ 留出未参与调参的holdout
```

MUST：

- 记录资料快照、生成模型、生成 Prompt 版本、时间、审核状态和审核人。
- 缺少业务依据时标记 `HUMAN_REVIEW` 或 `POLICY_DECISION_REQUIRED`。
- AI 生成数据默认不是 Gold。
- Smoke 只能证明管线可运行，不能证明真实业务正确。
- 未授权客户数据、密钥和完整敏感载荷不得进入数据集。

配套可复制提示词：

- `../ai-prompts/RAG测评数据集生成与校验提示词.md`
- `../ai-prompts/Agent-Skill-Workflow测评数据集生成与校验提示词.md`

## 4. Case 契约

每个 Case 包含：

- `case_id`：`<稳定唯一 ID>`
- 输入与可信上下文：`<填写，并标注来源>`
- 初始状态：`<填写>`
- 允许/禁止 Tool：`<填写>`
- 期望状态迁移：`<填写>`
- 期望业务结果：`<填写可判定条件>`
- 期望引用：`<填写文档/字段级证据>`
- 期望升级/拒绝：`<填写触发条件>`
- 标签：`<场景/风险/语言/租户/难度/来源>`
- 数据层级：`<Gold/Silver/Adversarial/Observed Regression/Smoke>`
- 数据来源：`<资料/契约/Trace的版本或哈希>`
- 生成信息：`<模型、Prompt版本、时间；人工原生案例写N/A>`
- 审核信息：`<状态、审核人、日期、理由>`
- Split：`<development/validation/holdout>`

## 5. 指标与阈值

| 指标 | 定义/分母 | 基线 | 门槛 | 分片 | 评分方式 |
|---|---|---|---|---|---|
| Task Success Rate | `<填写>` | `<实测>` | `<填写>` | `<场景/风险>` | `<确定性/人工/LLM judge>` |
| Tool Selection Accuracy | `<填写>` | `<实测>` | `<填写>` | `<Tool>` | `<填写>` |
| Tool Argument Accuracy | `<字段级定义>` | `<实测>` | `<填写>` | `<必填/敏感>` | `<Schema+语义>` |
| RAG Recall/Ranking | `<Recall@k/MRR/nDCG>` | `<实测>` | `<填写>` | `<知识域>` | `<填写>` |
| Answer Correctness | `<填写 rubric>` | `<实测>` | `<填写>` | `<场景>` | `<填写>` |
| Citation Accuracy | `<引用支持率>` | `<实测>` | `<填写>` | `<来源>` | `<填写>` |
| Hallucination Rate | `<无证据断言比例>` | `<实测>` | `<上限>` | `<高风险>` | `<填写>` |
| Safety Violation Rate | `<违规次数/总安全案例>` | `<实测>` | `<通常为 0>` | `<风险类型>` | `<规则+人工>` |
| Avg Steps/Latency/Token Cost | `<填写口径>` | `<实测>` | `<预算>` | `<模型/场景>` | `<遥测>` |
| Human Escalation Rate | `<应升级与误升级分别统计>` | `<实测>` | `<区间>` | `<风险>` | `<填写>` |

## 6. 评分器与可信度

- 确定性评分：`<Schema、状态、Tool、引用 ID、错误码>`
- 规则评分：`<填写规则及误报风险>`
- LLM Judge：`<模型、Prompt 版本、温度、rubric、盲评、偏差校准>`
- 人工评分：`<角色、抽样、盲评、冲突仲裁>`
- 一致性：`<重复运行次数、方差、置信区间>`

禁止只用同一模型自评；关键业务正确性和安全结论必须有确定性或人工证据。

## 7. 执行配置

- 环境与数据快照：`<填写>`
- 模型/参数/Prompt 哈希：`<填写>`
- Tool/RAG/索引版本：`<填写>`
- 随机种子与重复次数：`<填写>`
- 并发、超时、重试：`<填写>`
- 命令/流水线：`<填写可复制命令>`
- 结果产物：`<填写路径、保留期、负责人>`

ai-assistant 最小命令候选（按影响选择，不得声称未运行命令通过）：

```powershell
pytest
pytest tests/test_rag.py tests/test_rag_import_jobs.py tests/test_rag_backend.py tests/test_chunking.py -v
ruff check .
mypy app/
cd frontend
npm run typecheck
npm run build
```

## 8. 对比与回归

- 对照组：`<当前生产/基线版本>`
- 候选组：`<新版本>`
- 分片差异：`<填写按风险、语言、业务域、长尾切片>`
- 统计规则：`<填写显著性/置信区间/最小样本量>`
- 不可接受下降：`<填写总体与关键切片阈值>`

## 9. 线上验证

- 影子流量：`<采样、隔离副作用、判定>`
- 灰度：`<比例、时长、租户、开关>`
- 监控：`<业务指标、错误、成本、安全、人工升级>`
- 自动停止：`<填写明确阈值>`
- 回滚：`<填写版本、配置、数据和索引回退步骤>`

## 10. 结果与决策记录

- 结果摘要：`<填写实测数据，不写“表现良好”>`
- 失败 Case：`<填写 case_id、trace、根因、修复/接受理由>`
- 决策：`<通过/阻断/条件通过>`
- 决策人和日期：`<填写>`
- 新增回归资产：`<填写测试或 Evaluation Case 路径>`
