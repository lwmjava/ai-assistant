# ai-assistant 项目 AI 辅助开发迭代指导

> 文档状态：项目推进基线  
> 基线日期：2026-09-22
> 适用项目：`ai-assistant`  
> 适用对象：个人开发者及参与规划、实现、测试、评审的 AI Coding Agent  
> 说明：本文记录当前代码事实、差距、推进顺序、Evaluation 数据构建方法和可直接使用的提示词。代码持续变化时，应重新核对本文与真实实现。`GOV-001`～`RAG-006` 已在各自验收范围内完成。

## 1. 目的

本文解决五个问题：

1. 当前项目真实实现到了哪里。
2. 产品、设计文档和代码存在哪些漂移。
3. 当前正在打磨的 RAG 应先做什么、后做什么。
4. 没有现成业务测试集时，如何建立可信的 Evaluation。
5. 如何让 Cursor、Codex、Trae、WorkBuddy、ChatGPT 等 AI 工具在明确边界内推进，而不是无指标地继续堆功能。

本文不是新的产品需求，也不替代：

- `AGENTS.md`
- `docs/product/项目产品需求方案.md`
- `docs/product/项目设计方案.md`
- 已批准的 ADR、设计稿和实现计划

如果本文与代码不一致，必须先调查原因，不得直接假定本文或代码任一方正确。

## 2. 当前代码事实基线

### 2.1 技术栈

- 后端：Python 3.11+、FastAPI、SQLModel、Pydantic v2。
- 前端：React、TypeScript、Vite。
- 默认 Agent 编排：自研五阶段 `AgentPipeline`。
- 可选编排：LangGraph Supervisor。
- 默认 RAG 后端：`native`。
- 默认向量库：`local`，基于 SQLite 与 NumPy。
- 可选向量库：Milvus。
- 测试：pytest；静态检查包含 Ruff 和 mypy。

关键配置位于 `app/core/config.py`：

```text
RAG_ENABLED=false
RAG_VECTOR_STORE=local
RAG_BACKEND=native
RAG_CHUNK_STRATEGY=structured
AGENT_ORCHESTRATION=self
SECURITY_BLOCK_ON_INJECTION=false
RAG_KB_SCOPE=tenant
RAG_EFFECTIVE_DATE_FILTER=false
RAG_DROP_INJECTED_CHUNKS=true
```

注意：开发环境在没有真实 Embedding 配置时可能使用 Mock Embedding。Mock 结果只能验证程序流程，不能作为检索质量结论。

### 2.2 真实主调用链

```text
/api/chat
→ ChatService
→ Input Security
→ Conversation Memory
→ RAG Retriever
→ Tools / MCP
→ Skills
→ AgentPipeline 或 Supervisor
→ Output Security
→ SQLModel 持久化
→ Async Reflection
```

当前 `ChatService` 同时承担上下文组装、能力装配、安全调用、编排选择、持久化和反思触发等职责，事实上承担了部分 Harness 功能。

### 2.3 RAG 已实现能力

真实 RAG 主链已经覆盖：

```text
文档上传/导入
→ 多格式 Parser / OCR
→ ParsedDocument / ParsedBlock
→ 多种 Chunking 策略
→ Embedding
→ Document / DocumentChunk
→ Local 或 Milvus VectorStore
→ Dense + BM25 + RRF Hybrid Search
→ HybridRetriever
→ Agent Context
```

已存在的主要能力：

- 同步摄取和异步 ImportJob/Batch。
- PDF、DOCX、XLSX、PPTX、文本、OCR 等解析能力。
- structured、paragraph、recursive、semantic、parent-child、format-aware、layout-aware 等切分策略。
- 文档版本链、去重、重解析和当前版本过滤。
- Local/Milvus 适配。
- Dense、BM25 与 RRF 混合检索。
- 读路径默认同租户当前版本（`RAG_KB_SCOPE=tenant`）；写路径成员仅自己的文档。
- 检索上下文 `[UNTRUSTED_SOURCE]` 围栏；高置信度注入块剔除。拒工具不是通用白名单，也不是只在围栏内部查找：`[UNTRUSTED_SOURCE]` 只是开关，随后对整段 `context`（含记忆和之后追加的 critique）做子串匹配；整段里没有的工具名不会被拒绝。
- Memory 与 RAG 按字符预算合并，检索不再覆盖记忆。`_trim` 只作用于这次合并。QualityGate 的 critique 在截断之后拼进 `state.context`，不再按 `RAG_CONTEXT_CHARS` 截断；长度随自纠错轮数增长，上限是 `AGENT_MAX_REVISIONS`。
- 租户级过滤；`RAG_EFFECTIVE_DATE_FILTER` 默认关闭。
- 知识库管理前端。

### 2.4 现有测试资产

重点测试包括：

```text
tests/test_rag.py
tests/test_rag_backend.py
tests/test_rag_import_jobs.py
tests/test_rag_access.py
tests/test_rag_permission_semantics.py
tests/test_retrieval_guard.py
tests/test_context_merge.py
tests/test_vectorstore_policy.py
tests/test_chunking.py
tests/test_document_parsers.py
tests/test_document_parser_blocks.py
tests/test_embeddings.py
tests/test_document_storage.py
tests/test_ocr.py
tests/test_cloud_ocr.py
tests/test_legacy_office.py
tests/test_p0_regression.py
tests/eval/
```

现有 pytest 覆盖实现和接口行为。版本化 RAG Evaluation 已有：`evals/datasets/rag-v0.1/`（44 条，其中 Gold v0.1 24 条）与 2026-09-19 检索基线。尚未覆盖真实 LLM 生成层，也没有统一的 Agent/Skill Evaluation Harness。

## 3. 已确认的文档与实现漂移

| 文档声明 | 代码现实 | 处理建议 |
|---|---|---|
| 原 `AGENT.md` 写无前端 | 存在完整 `frontend/` | 已在 `AGENTS.md` 修订为当前事实 |
| 原 `AGENT.md` 写只支持 TXT/MD | 已有 PDF、Office、OCR 等 Parser | 已在 `AGENTS.md` 改为能力矩阵和支持级别 |
| PRD 将 LangGraph 五阶段作为主路径 | 默认主路径是自研 `AgentPipeline` | 明确默认与可选编排 |
| 设计文档使用 `src/ai_assistant/` | 真实目录为 `app/` | 更新 As-Is 路径 |
| 文档宣称多种 Rerank | 当前只有 RRF，无独立 Reranker | 标记为规划，不得写已实现 |
| 文档宣称 Recall/MRR/NDCG Evaluation | 已有 rag-v0.1 检索基线，语料很小 | 可引用分数但必须带有效性限制，不得当生产质量 |
| 文档将 Milvus 表述为主要能力 | 默认 Local；ADR-0002 定为实验/`Partial` | 升格须另开 ADR 并补闭环证据 |
| README 表述回答可追溯 | 当前引用主要是 source 文本 | 补结构化 Citation 后再升级声明 |

文档修订时使用以下状态标签：

- `Implemented`：代码、测试和运行证据齐全。
- `Partial`：部分主链可用，但存在明确缺口。
- `Planned`：已设计或列入计划，尚未实现。
- `Deferred`：明确暂缓，有进入条件。
- `Deprecated`：不再推荐，保留兼容期。

禁止把 `Planned` 写成当前能力。

## 4. 当前关键差距与优先级

> 截至 2026-09-22：`tasks.yaml` 中 `GOV-001`～`RAG-006` 已在各自验收范围内完成。已关闭项不得再写成当前 P0。

### 4.1 已关闭（不得再当当前缺口）

- 版本化 Evaluation Schema、Gold v0.1（24 条）与 rag-v0.1 检索基线（真实 `text-embedding-v3`，2026-09-19 冻结）。
- 读路径列表/详情/检索对齐为同租户当前版本（`RAG_KB_SCOPE=tenant`，ADR-0001）。
- 检索上下文 `[UNTRUSTED_SOURCE]` 围栏、高置信度注入块剔除、脚本化 LLM 拒工具。拒工具边界见 §2.3：围栏标记是开关，匹配范围是整段 context，不是通用白名单。RAG-010 只补记该边界，不改拒绝条件。
- Local 摄取 → 检索 → 重解析旧向量失效 → 删除清理。
- Memory 与 RAG 按字符预算合并，检索不再覆盖记忆。critique 追加发生在预算截断之后，不在 `RAG_CONTEXT_CHARS` 内；RAG-010 只补记，不改管线拼接。
- ADR-0002：正式 Local，Milvus 实验。ADR-0003：生效日期 Flag 默认关闭。

残余限制仍有效：语料仅 13 篇 / 37 分块，不得当生产检索质量；真实 LLM 生成层未测；资源 ACL 仍为 `Planned`。

### 4.2 当前优先：单变量 RAG 优化

`RAG-005` 基线已冻结，可以进入指标驱动实验。约束：

- 不同时更换 Embedding、Chunking 和 Reranker。
- 不用 Mock Embedding 声称检索质量提升。
- 不得用 holdout 调参。
- 每轮只改一个主要变量，并用同一数据集对比 Recall/MRR/nDCG、权限和安全切片。

没有固定数据集和指标时，无法判断新 Chunking、Rerank、Embedding 或 Top-K 是否真的改善业务答案。基线已有，但后续实验仍必须带失败切片，不得只展示成功样本。

### 4.3 P1：Citation 过弱

检索和回答至少应能追踪：

```text
document_id
chunk_id
title
source
version
page/section
effective_date
score
```

只有 `[资料 N]（来源：xxx）` 不足以支持企业级审计。

### 4.4 P1：缺少独立 Reranker

RRF 是融合，不等于独立 Rerank。是否增加 Cross-Encoder、LLM 或 API Reranker，必须由同一 Evaluation 数据集的结果决定。

### 4.5 P1：资源级 ACL 仍为 Planned

ADR-0001 明确本阶段不做资源级/受众级 ACL。同租户当前文档命中不记检索越权；生成层仍不得返回 `forbidden_answer_points`。另开任务，不阻塞单变量优化。

### 4.6 P1：Harness 治理边界

当前缺少独立：

- Agent Harness。
- Tool Executor。
- 完整 Tool Contract。
- State Manager。
- Context Builder。
- 持久化 Trace。
- 分层 Guardrail。

这是渐进治理任务。`RAG-005` 基线已冻结，仍禁止无指标大爆炸重构。

### 4.7 仍为实验：Milvus 主链闭环

ADR-0002 已决定本阶段正式后端为 Local，Milvus 为实验/`Partial`。升格必须另开 Accepted ADR，并证明：

```text
摄取 → Embedding → Milvus Upsert → Search
→ 重解析删除旧向量 → 文档删除清理向量
```

在此之前不为非当前目标投入生产切换。

## 5. 推荐推进顺序

```text
事实对账
→ 产品/架构关键决策
→ Evaluation 数据与基线
→ 权限、安全和主链正确性
→ 指标驱动 RAG 优化
→ 结构化 Citation
→ 渐进 Harness 治理
```

阶段 0～2（事实与决策、Evaluation 基线、P0 正确性与安全）已完成。当前从阶段 3（指标驱动 RAG 优化）开始；阶段任务说明保留作为历史方法和约束，不得把已完成阶段再当待办。

### 阶段 0：事实与决策，预计 1～2 天

任务：

1. 持续核对 `AGENTS.md`、产品方案和设计方案的事实状态。
2. 建立 As-Is 架构和 RAG 能力矩阵。
3. 决定知识库共享/私有权限。
4. 决定当前正式向量库。
5. 固定 Evaluation 使用的 Embedding、索引和配置。

退出证据：

- 文档能力状态与代码一致。
- 两项关键决策有 ADR。
- 评测配置可重复。

### 阶段 1：Evaluation 基线，预计 2～4 天

任务：

1. 定义 Case Schema。
2. 建立 30～50 条 Silver 候选集。
3. 生成无答案、冲突、过期、权限和注入案例。
4. 独立 AI 复核。
5. 人工抽查 20～30 条形成 Gold v0.1。
6. 实现 Recall@K、MRR、nDCG 和 Citation 基础评分。
7. 保存 baseline report。

退出证据：

- 数据集有版本和来源。
- 每个答案可回指原文。
- 评测命令可重复。
- 基线报告包含失败案例，而不是只展示成功样本。

### 阶段 2：P0 正确性与安全，预计 4～7 天

任务：

1. 统一列表、详情、检索和 Agent 使用的权限语义。
2. 将 RAG 上下文标记为不可信资料。
3. 增加 Prompt Injection、跨租户和无权限测试。
4. 修复或验证 Memory/RAG Context 合并。
5. 对正式向量库补摄取—检索—删除闭环。
6. 增加 `RAG_ENABLED=true` 的 Chat+RAG 集成测试。

退出证据：

- 跨租户泄露率为 0。
- 安全案例违规率为 0。
- 真实主链集成测试通过。
- 每个修复均有回归用例。

### 阶段 3：指标驱动质量优化，预计 5～7 天

实验顺序：

1. Chunk size/overlap。
2. Chunking strategy。
3. Dense/BM25/RRF 参数。
4. Top-K 与上下文预算。
5. Reranker。
6. Embedding。
7. Query Rewrite。

每轮只修改一个主要变量，并记录：

```text
实验 ID
基线版本
候选版本
数据集版本
模型/索引/参数
指标差异
失败切片
成本与延迟
保留或回滚结论
```

### 阶段 4：Citation 与 Harness 渐进治理，持续推进

推荐提取顺序：

1. Citation Builder。
2. Context Builder。
3. Tool Contract。
4. Tool Executor。
5. Permission Guard。
6. Persistent Trace。
7. State Manager。
8. Agent Harness Facade。

每次只迁移一个边界，保留现有调用路径和回退开关。

## 6. 四周建议排期

> 第 1～2 周对应 `GOV-001`～`RAG-006`，2026-09-22 已在验收范围内完成。当前任务 `RAG-007`（RRF `k`）已写入 `tasks.yaml`。其余单变量实验与 Citation 尚未拆分，不得直接开工。

### 第 1 周：事实和评测基线

- 完成文档对账。
- 固定正式评测配置。
- 生成 Silver/Adversarial 数据。
- 人工确认 Gold v0.1。
- 输出首份 baseline report。

### 第 2 周：P0 安全与正确性

- 权限 ADR 和一致性实现。
- RAG Prompt Injection 防护。
- Memory/RAG Context 合并。
- 正式 VectorStore 闭环。
- Chat+RAG 集成测试。

### 第 3 周：检索质量和引用

- 开展单变量实验。
- 增加结构化 Citation。
- 评估是否需要独立 Reranker。
- 输出基线—候选对比报告。

### 第 4 周：Harness 第一阶段

- 提取 Context Builder。
- 扩充 Tool Contract。
- 引入最小 Tool Executor。
- 持久化关键 Trace。
- 完成防漂移审查和发布前回归。

只按 60%～70% 理论产能排任务，其余留给环境、联调、返工和文档修订。

## 7. 没有真实测试集时如何构建 Evaluation

### 7.1 数据来源优先级

1. 权威政策、合同、产品说明和 SOP。
2. 当前知识库文档和 FAQ。
3. 脱敏工单、客服对话、搜索日志和失败 Trace。
4. 已确认的 Bug 和验收案例。
5. AI 基于上述资料生成的数据。
6. 完全虚构数据。

完全虚构数据只能验证管线，不得用于宣称真实业务质量。

### 7.2 数据集分层

#### Silver

AI 根据权威文档生成，并附准确原文证据。尚未逐条人工确认。

#### Gold

业务或项目负责人确认问题、答案、引用、权限和预期行为后的数据。

#### Adversarial

用于验证无答案、冲突、过期、越权、注入、工具故障和人工升级。

推荐启动流程：

```text
盘点权威文档
→ AI 生成 50 条 Silver
→ 第二个 AI 独立复核
→ 程序检查引用和重复
→ 人工抽查/修订 20～30 条
→ 冻结 Gold v0.1
→ 余下 Silver 用于扩展测试
```

### 7.3 RAG Case 最小契约

```json
{
  "id": "rag-001",
  "dataset_version": "0.1.0",
  "category": "fact",
  "query": "问题",
  "tenant_id": "tenant-a",
  "user_id": "user-a",
  "expected_document_ids": ["doc-001"],
  "expected_chunk_evidence": [
    {
      "document_id": "doc-001",
      "source": "policy.md",
      "version": "v1",
      "page": null,
      "section": "收费标准",
      "exact_quote": "可在原文中准确定位的证据"
    }
  ],
  "expected_answer_points": ["必须包含的事实"],
  "forbidden_answer_points": ["禁止出现的断言"],
  "should_answer": true,
  "should_escalate": false,
  "synthetic": true,
  "generation_provenance": {
    "generator": "模型及版本",
    "generated_at": "ISO-8601",
    "source_snapshot": "文档版本或哈希"
  },
  "tags": ["billing", "easy"]
}
```

### 7.4 必须包含的对抗切片

- `no_answer`：资料中没有答案，应明确未知。
- `conflict`：多个文档冲突，应引用并升级。
- `stale_version`：旧版本不得覆盖当前版本。
- `cross_tenant`：不得命中其他租户资料。
- `private_resource`：无 ACL 不得命中。
- `prompt_injection`：不得执行资料内指令。
- `ambiguous`：条件不足时应澄清。
- `effective_date`：按生效时间选择正确版本。
- `citation_mismatch`：答案不能引用不支持其结论的资料。

### 7.5 防止 AI 生成“看起来正确”的假数据

必须满足：

- 每条答案有 `exact_quote`。
- 校验器重新读取原文。
- 生成者和校验者不共享生成结论。
- 程序检查 quote 是否真实存在。
- Gold 集必须有人工确认记录。
- 调参集和最终保留集分离。
- 失败案例不能删除，只能修订标签或记录接受理由。

## 8. 可直接粘贴的项目提示词

完整提示词示例应与项目规则一同版本化。以下是当前推进最关键的五个。

### 8.1 项目事实对账

```text
你正在审计仓库 <ai-assistant绝对路径>。只读分析，不修改文件。

先读取 AGENTS.md、产品需求、设计方案、pyproject.toml、README、
核心配置、API入口、ChatService、AgentPipeline、RAG、Tools、Skills、
测试和部署文件。

要求：
1. 从真实入口沿调用链取证，禁止只根据文档或文件名猜实现。
2. 分开记录“代码事实、文档声明、推断、未知”。
3. 每个事实附文件路径、类/函数/配置或测试证据。
4. 输出当前技术栈、模块职责、关键调用链、已实现/部分/规划能力。
5. 对照文档找漂移，不把规划项报成已实现。
6. 按 P0/P1/P2 输出缺口、影响和最小验证动作。
7. 不提出大爆炸重构。

最终输出：
- As-Is架构；
- 文档漂移表；
- 风险清单；
- 建议推进顺序；
- 尚未验证的事项。
```

### 8.2 从知识文档生成 RAG Silver 集

```text
你正在为 ai-assistant 生成 RAG Evaluation 候选数据。

先读取项目规则、RAG实现、权限模型、Evaluation Schema，以及：
<填写权威知识文档路径>

只生成候选数据，不修改代码。

要求：
1. 只能使用指定文档中的事实，不得用模型常识补充。
2. 每个答案必须附 document_id、source、version、page/section 和 exact_quote。
3. exact_quote 必须能在原文中逐字定位。
4. 覆盖 fact、multi_hop、no_answer、conflict、stale_version、
   cross_tenant、permission、prompt_injection、ambiguous。
5. 同义改写不算新案例，避免重复。
6. synthetic=true，并记录模型、时间和文档快照。
7. 无可靠证据的候选项直接拒绝，不得猜测。

按本文“RAG Case最小契约”输出 JSON。
最后报告文档覆盖率、类别分布、歧义项和需人工确认项。
```

### 8.3 独立校验候选数据

```text
请作为独立审核者校验 ai-assistant 的 RAG Evaluation 候选集。

你必须重新读取原始知识文档，不得只阅读候选答案。
逐条验证问题可答性、exact_quote、答案边界、版本、页码/章节、
权限预期、无答案真实性、注入预期和重复情况。

每项只能给：
- PASS
- REVISE
- REJECT

REVISE/REJECT 必须附原文证据和理由。
禁止使用模型常识修补资料缺失。

最后输出通过率、拒绝项、重复项、文档覆盖缺口和推荐人工抽查项。
不要修改原始数据集。
```

### 8.4 执行 RAG 基线

```text
请为 ai-assistant 建立可重复的 RAG Evaluation baseline。

开始前读取项目规则、RAG代码、测试配置、数据集Schema和固定评测配置。
先只输出实施计划，得到确认后再修改。

要求：
1. 固定数据集、Embedding、索引、Chunking、检索参数和随机性。
2. 实现 Recall@1/5/10、MRR、nDCG、Citation Accuracy 的确定性评分。
3. 分别报告主路径、无答案、版本、权限和注入切片。
4. 报告失败Case，禁止只展示成功结果。
5. 结果保存为机器可读JSON和人类可读Markdown。
6. 不使用Mock Embedding证明生产检索质量。
7. 不设置未经实测的伪精确发布阈值；先跑基线再提门槛。

输出影响范围、拟修改文件、测试命令、风险和回滚。
```

### 8.5 RAG 单变量优化

```text
请基于已冻结的 Evaluation 数据集优化 <填写变量>。

本轮唯一允许改变的主要变量：
<chunk_size / overlap / strategy / RRF参数 / top-k / reranker / embedding>

禁止同时修改其他主要检索变量。

开始前记录基线版本、数据集、模型、索引和配置。
完成后使用同一数据集比较 Recall、MRR、nDCG、Citation、
Answer Correctness、安全、延迟和成本。

只有证据显示总体或目标切片改善、且关键安全切片不退化时才保留。
否则回滚并记录实验结论。

先输出计划，不直接修改。
```

更多 Agent、Skill、Workflow 和夜间开发提示词，应引用项目采用的通用 Agent Harness Engineering 模板包，不在多处复制维护。

## 9. AI 执行单项任务的标准契约

每次只交给 AI 一个可独立验收的任务：

```text
任务ID：
业务目标：
证据来源：
允许修改：
禁止修改：
Non-goals：
风险等级：
依赖：
验收标准：
测试命令：
Evaluation：
真实业务验收：
停止条件：
回滚方式：
```

AI 开始前必须：

1. 读取规则和关联文档。
2. 检查工作区和已有改动。
3. 复述事实、假设和未知。
4. 给出影响范围和计划。
5. 等待要求的确认。

AI 完成后必须：

1. 列出实际修改文件。
2. 报告真实执行命令和退出结果。
3. 报告 Evaluation 前后差异。
4. 报告未验证事项和残余风险。
5. 不自动宣称业务验收通过。

## 10. 夜间无人值守边界

允许夜间执行：

- 文档对账。
- 确定性测试补充。
- 无副作用评测脚本。
- 小范围 L0 只读能力。
- 已批准、边界清晰、可回滚的低风险任务。

禁止夜间无人值守：

- 权限模型决策。
- 数据迁移。
- 生产配置和生产数据操作。
- 删除或覆盖业务数据。
- 大规模目录重构。
- 新供应商和重大架构选型。
- 无验收标准的 RAG“优化”。

夜间产出只允许进入独立分支或草稿 PR，次日必须人工检查：

- Diff 是否越界。
- 测试是否只验证实现细节。
- Evaluation 是否使用同一数据集。
- 权限和安全是否退化。
- 真实业务路径是否可验收。

## 11. 当前应暂停的工作

在阶段 1 完成前暂停：

- 新增更多 Chunking 策略。
- 同时接入更多 RAG 框架。
- 无基线地更换 Embedding。
- 直接实现文档中宣称但未验证价值的 Reranker。
- 大规模迁移 `app/`。
- 一次性重写 `ChatService` 和 `AgentPipeline`。
- 用 Mock 数据得出生产质量结论。

## 12. 每周防漂移审查

每周执行：

```text
需求/设计声明
↔ 代码实现
↔ 配置默认值
↔ 测试
↔ Evaluation
↔ README/运行文档
```

输出：

- 新增但未记录的能力。
- 文档声称但代码未实现的能力。
- 代码变化但未更新的测试/Evaluation。
- 权限、版本、模型和索引漂移。
- 已过期例外。
- 下周必须优先收敛的三项问题。

## 13. 当前下一步

`GOV-001`～`RAG-006` 已完成。当前可执行任务：

1. `RAG-007`：单变量实验 RRF `k`（对照 60，候选 40/80；查询期，不重建索引）。见 `tasks.yaml`。

之后须再拆任务再实施：

1. 切分大小或 Chunking 策略（索引期，二选一；不同时改）。
2. Embedding / 独立 Reranker / Query Rewrite（各自单开）。
3. 结构化 Citation。
4. 渐进提取 Context Builder、Tool Executor 和 Harness。

Top-K 实验须先扩大评测语料。资源级 ACL 仍为 `Planned`。不得用 Mock 或 holdout 宣称质量提升。不得改写 2026-09-19 基线 JSON。
