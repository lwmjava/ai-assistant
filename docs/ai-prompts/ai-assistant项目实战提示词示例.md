# ai-assistant 项目实战提示词示例

> 这些示例已按当前仓库根目录（https://github.com/lwmjava/ai-assistant）的代码事实填写，可直接复制到 Cursor、Codex、Trae、WorkBuddy 或 ChatGPT。文中路径均相对仓库根目录。
> 使用前仍需确认本地分支、未提交改动和最新代码。涉及修改的提示词均要求先输出计划、人工确认后实施。

## 1. 当前事实

本示例基于以下现状：

- 后端为 Python 3.11+、FastAPI、SQLModel、Pydantic v2。
- 前端为 React、TypeScript、Vite。
- 默认编排是 `app/agents/pipeline.py` 的自研 `AgentPipeline`。
- LangGraph Supervisor 是可选编排。
- RAG 默认 `native + local`，Milvus 可选。
- `ChatService` 负责组装 Memory、RAG、Tools、Skills、安全和 Pipeline。
- RAG 已有多格式解析、OCR、多策略 Chunking、混合检索、导入任务和版本管理。
- 当前没有正式 RAG/Agent Evaluation 数据集。

AI 必须重新读取代码确认这些事实仍然成立。

## 2. 提示词：重新建立项目事实基线

```text
请对当前仓库根目录做只读事实审计。
不要修改任何文件。

开始前读取：
- AGENTS.md
- docs/product/项目产品需求方案.md
- docs/product/项目设计方案.md
- pyproject.toml
- README.md
- app/core/config.py
- app/main.py
- app/services/chat_service.py
- app/agents/pipeline.py
- app/agents/tools/
- app/agents/skills/
- app/rag/
- app/models/rag.py
- tests/ 中相关测试

从真实API入口沿调用链取证。输出必须分为：
1. 已确认代码事实；
2. 文档声明；
3. 文档与实现漂移；
4. 待验证项；
5. P0/P1/P2差距。

每项附文件路径和类、函数、配置或测试证据。
禁止把docs/plans中的计划项报告成已实现。
禁止提出大爆炸重构。
```

## 3. 提示词：修订项目事实文档

```text
请基于已确认的代码事实修订 ai-assistant 项目文档。

目标文件：
- AGENTS.md
- docs/product/项目产品需求方案.md
- docs/product/项目设计方案.md

开始前：
1. 读取上述文档和真实代码；
2. 输出文档漂移清单；
3. 标记每项为Implemented、Partial、Planned、Deferred或Deprecated；
4. 输出拟修改章节和保留内容；
5. 等待我确认，不要直接修改。

修订要求：
- 将前端、目录、Agent主路径、RAG格式、VectorStore默认值写成当前事实；
- Rerank、Evaluation、Admin、CLI等未完成内容不得写成已实现；
- 保留最终愿景，但必须与当前实现状态分开；
- 不改变产品范围，不修改代码；
- 若发现产品决策冲突，停止并列出待确认问题。

完成后报告实际修改、引用证据和仍未解决的决策。
```

## 4. 提示词：建立 RAG Evaluation 设计文档

```text
请为当前仓库根目录设计第一版RAG Evaluation。
本轮只编写设计，不实现评测代码。

先读取：
- AGENTS.md及相关规则；
- docs/AI辅助开发迭代指导.md；
- app/rag/；
- app/models/rag.py；
- app/services/chat_service.py；
- tests/test_rag.py；
- tests/test_rag_backend.py；
- tests/test_rag_import_jobs.py；
- tests/test_chunking.py；
- 现有知识资料或测试fixture。

设计必须包含：
1. Gold/Silver/Adversarial/Smoke数据分层；
2. Case JSON Schema；
3. development/validation/holdout划分；
4. Recall@1/5/10、MRR、nDCG、Citation Accuracy；
5. Answer Correctness、Hallucination、安全、权限、延迟和成本；
6. 文档版本、生效时间、租户和资源权限；
7. 生成Prompt、独立校验、人工确认流程；
8. baseline与候选版本对比；
9. 结果JSON和Markdown报告格式；
10. CI与发布门禁的后续接入方式。

不要猜阈值；没有基线时先规定如何实测。
输出拟新增文件、实施顺序和验证方式，等待确认。
```

## 5. 提示词：从项目现有资料生成 RAG 候选集

```text
请在当前仓库根目录中盘点可用于
RAG Evaluation的现有资料和fixture。

只读，不修改文件。

先检查：
- 项目知识文档；
- tests中的RAG/Parser/Chunking fixture；
- README、产品和设计文档；
- data目录中可合法读取的测试数据；
- 已有失败测试和Bug记录。

不要读取或输出密钥、未授权客户数据或生产数据。

输出：
1. 可作为权威知识源的资料；
2. 只能作为程序Smoke的虚构资料；
3. 缺少版本/权限/来源的资料；
4. 可生成的RAG案例类别和数量；
5. 无法建立真实业务Gold集的缺口。

然后基于可用资料生成最多50条Silver候选，按项目Evaluation Schema输出。
每个答案必须有可逐字定位的exact_quote。
没有可靠资料时不要硬生成业务答案，应改为提出最小测试fixture方案。
```

## 6. 提示词：独立校验 RAG 候选集

```text
你是独立RAG Evaluation审核者，不是候选数据生成者。

仓库根目录：https://github.com/lwmjava/ai-assistant

请重新读取原始知识资料、权限规则、版本规则和候选数据。
不要根据候选答案自行补全事实。

逐条检查：
- exact_quote是否真实存在；
- 答案是否超出原文；
- document/version/page/section是否准确；
- 无答案案例是否真的无答案；
- 用户是否有权访问预期文档；
- 旧版本是否错误覆盖当前版本；
- 注入案例是否期望拒绝文档指令；
- 是否存在重复、泄露答案或依赖模型常识。

每项只能给PASS、REVISE、REJECT或HUMAN_REVIEW。
输出独立报告，不要直接覆盖候选集。
```

## 7. 提示词：实现 RAG Evaluation Harness

```text
请在当前仓库根目录中实现已批准的
RAG Evaluation Harness。

开始前必须读取：
- AGENTS.md；
- docs/AI辅助开发迭代指导.md；
- 已批准的Evaluation设计；
- RAG实现和现有pytest配置。

先输出Implementation Plan，未经确认不要修改。

边界：
- 不修改生产RAG行为；
- 不更换Embedding或VectorStore；
- 不实现Reranker；
- 不降低现有测试门禁；
- 不使用Mock结果证明生产质量。

先写数据Schema和失败测试，再实现：
- 数据加载和版本校验；
- Recall@K、MRR、nDCG；
- Citation确定性检查；
- 分片统计；
- baseline JSON/Markdown报告。

完成后运行影响范围内pytest、ruff和mypy命令。
报告命令、退出码、失败Case、未验证项和回滚方式。
```

## 8. 提示词：生成 Agent Evaluation 候选

```text
请为 ai-assistant 当前 AgentPipeline 生成 Agent Evaluation Silver 候选。

先读取：
- app/services/chat_service.py；
- app/agents/pipeline.py；
- app/agents/prompts.py；
- app/agents/tools/base.py和builtin.py；
- app/agents/skills/；
- app/security/及相关配置；
- tests中的chat、agent、tool、skill和security测试。

不要假设项目已经有独立Harness、ToolExecutor或正式StateMachine。
按真实实现生成候选。

覆盖：
- 普通问答；
- RAG开启/关闭；
- Skill匹配；
- Tool不存在、参数错误、执行异常和最大调用轮次；
- Memory存在时的上下文；
- 输入/输出安全；
- Prompt Injection；
- 缺少权限；
- 需要澄清或停止的场景；
- LangGraph关闭时的默认self路径。

每个Case包含：
identity、input、initial_state、available_skills、allowed/forbidden_tools、
expected_actions、expected_answer_points、forbidden_behaviors、max_steps、
max_tool_calls、risk和provenance。

不能确认的行为标记HUMAN_REVIEW或POLICY_DECISION_REQUIRED，不得猜。
```

## 9. 提示词：生成 Skill Evaluation 候选

```text
请为 ai-assistant 的内置Skills生成Evaluation Silver候选。

先读取：
- app/agents/skills/base.py
- app/agents/skills/loader.py
- app/agents/skills/manager.py
- app/agents/skills/builtin/translator.yaml
- app/agents/skills/builtin/code_review.yaml
- 相关测试

分别根据每个Skill真实Manifest生成：
- Trigger命中与不命中；
- Preconditions满足与缺失；
- Workflow正常和失败分支；
- 允许/禁止Tools；
- RAG要求；
- Constraints反例；
- Escalation；
- 输出语义断言。

检查Manifest是否完整包含Purpose、Preconditions、Workflow、Tools、
RAG、Constraints、Escalation、Examples和Tests。
缺失规格不能由AI自行补业务规则，应生成规格缺口报告。
```

## 10. 提示词：生成 AgentPipeline Workflow Evaluation

```text
请为 ai-assistant 的Agent执行Workflow生成Evaluation候选。

先读取ChatService、AgentState、AgentPipeline、Supervisor、ToolRegistry、
安全模块、Trace和相关测试。

以真实阶段和异常行为为准，不要强行套用通用状态机名称。

覆盖：
- 正常非流式；
- 正常流式；
- RAG检索成功/空结果/异常；
- Tool调用成功/不存在/异常/达到最大轮次；
- 质量门开启/关闭；
- Supervisor开启/关闭；
- 输入安全阻断/仅告警；
- 输出安全失败；
- 取消、超时和不可恢复错误中当前已实现的行为；
- Memory和RAG Context同时存在。

每项记录初始状态、事件、预期阶段顺序、Tool副作用、
Trace、最终技术状态和最终业务结果。

对代码未定义的取消、恢复或状态持久化行为标记能力缺口，
不得生成虚假的通过标准。
```

## 11. 提示词：修复权限语义前的 ADR 分析

```text
请分析 ai-assistant 知识库的权限语义冲突，只读，不修改。

检查：
- app/api/routes/rag.py；
- app/rag/service.py；
- app/rag/vectorstore/local.py；
- app/rag/vectorstore/milvus.py；
- app/models/rag.py；
- 用户、租户和权限模型；
- 产品/设计文档和相关测试。

比较两个选项：
A. 知识库在租户内共享；
B. 文档按用户或资源ACL私有。

对每个选项说明：
- 列表、详情、检索、Agent Context、删除和重解析语义；
- 数据模型变化；
- 兼容和迁移；
- 安全风险；
- 测试和回滚。

给出推荐，但不要替产品负责人作最终决定。
输出ADR草案和需要确认的问题。
```

## 12. 提示词：单变量 RAG 优化

```text
请对 ai-assistant 的 <填写唯一变量> 做一次可复现RAG实验。

允许改变：
仅 <填写变量>

禁止改变：
- Evaluation数据集；
- 其他Chunking参数；
- Embedding模型；
- VectorStore；
- Reranker；
- Agent Prompt；
- 权限和安全策略。

记录基线/候选配置、代码版本、索引版本和运行环境。
使用同一数据集比较Recall、MRR、nDCG、Citation、Answer Correctness、
Hallucination、安全、延迟和成本。

关键风险切片下降时不得仅用总体平均提升判定成功。
输出保留/回滚建议和失败Case。
```

## 13. 提示词：渐进提取 Tool Executor

```text
请分析在不重写AgentPipeline和ChatService的前提下，
为 ai-assistant 渐进引入ToolExecutor的方案。

先读取Tool、Pipeline、ChatService、MCP、权限、安全、审计和测试。
本轮只设计，不修改。

目标Tool Contract至少包含：
name、description、input/output schema、permission、risk_level、
side_effect、timeout、retry、idempotency、error_model、audit、version。

要求：
- 保留现有ToolRegistry兼容入口；
- Agent只提出调用，Executor统一执行；
- 不把核心业务规则移入Prompt；
- 不一次性改造所有Tool；
- 先选择一个L0只读Tool做垂直切片；
- 提供Feature Flag、适配层、契约测试和回滚。

输出As-Is/To-Be、接口草案、迁移步骤、测试、ADR影响和停止条件。
```

## 14. 提示词：次日独立审查

```text
请作为独立审查者检查 ai-assistant 当前分支的完整diff。
只做报告，不修改文件。

先读取原始任务、AGENTS.md、相关设计/ADR、完整调用方、测试和Evaluation报告。

检查：
1. 是否满足真实业务验收，而不只是pytest通过；
2. 是否超范围或顺手重构；
3. 是否破坏self/langgraph、local/milvus兼容路径；
4. 是否造成Memory/RAG Context覆盖；
5. 是否扩大租户或资源权限；
6. 是否把外部文档指令当作可信命令；
7. 测试是否过度Mock或只断言状态码；
8. Evaluation是否使用相同数据集和配置；
9. 文档声明是否与代码一致；
10. 是否可以安全回滚。

每个发现给严重度、文件位置、触发条件、业务影响、证据和最小修复。
结论只能是：进入人工验收 / 修改后再审 / 拒绝合并。
```

## 15. 推荐使用顺序

```text
事实审计
→ 文档对账
→ Evaluation设计
→ 资料盘点
→ Silver生成
→ 独立校验
→ 人工Gold
→ Evaluation Harness
→ P0权限/安全/正确性
→ 单变量RAG实验
→ Harness渐进治理
→ 独立审查
```

任何一步发现规格冲突、数据无授权或风险边界不明，都应停止并请求人工决策。
