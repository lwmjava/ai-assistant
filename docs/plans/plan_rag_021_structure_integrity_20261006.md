# RAG-021 结构完整性保护实施计划

日期：2026-10-06。状态：实施中；用户已批准 service.py 与 embeddings/ 扩展，以及 UTF-8 字节数 + 32 预留的保守估算并校准。

任务：[tasks.yaml](../../tasks.yaml) RAG-021。架构依据：[ADR-0005](../adr/0005-rag-adaptive-chunking-context-budget.md)。既有批准继续有效，不重复请求 oversized 行为批准。

## 事实与准入

- 当前工作树已从旧 main 基线 f0d0afb 对齐上一轮已验收提交 16308e7，分支 codex/rag-021；主仓库未提交文件未被复制或修改。
- ADR-0005 已 Accepted：chunk_size 是软目标；代码/盒图保持完整，表格按行拆并关联表头；无法安全拆分的超长结构保留原文、标记 oversized 并说明未向量化原因；旧文档不自动重建。
- paragraph 对长段字符硬切；structured 复用 ingestion.split_text_structured，句子处理不能保护围栏内部标题、换行及盒图。
- parent_child 已改为父块内部生成子块，不能再写成按数量平均分配；结构保护仍未实现。
- app/rag/service.py 的 _persist_chunks 和 reparse_document 都无条件对全部块调用 _embed_texts。仅新增 oversized 元数据不能阻止超限调用。
- EmbeddingProvider 仅提供 model、dim 和 embed，没有输入上限或计数能力。token_aware 的 max_tokens/估算值不能证明真实模型硬限。
- semantic 策略在最终持久化之前调用 Embedding，硬限检查也必须覆盖切分期调用；只过滤 service 的最终批次不足。
- 初始准入时 service.py 与 embeddings/ 不在本卡 allowed_paths，按 AGENTS.md 第 12 节暂停业务实施；随后用户明确批准扩展，并补齐模型限制核对与估算决策，恢复实施。

## 目标与方案

实际配置只读核对：DashScope compatible-mode/v1、text-embedding-v3、1024 维、batch=10；未输出密钥。当前工作树无 .env，运行测试保持 Mock，不复制主仓库凭据。官方同步接口说明（2026-10-06 核对）：每条 8192 tokens、每批最多 10 条，来源 https://www.alibabacloud.com/help/en/model-studio/text-embedding-synchronous-api 。用户已确认采用 UTF-8 字节长度 + 32 安全预留；方法版本 utf8-bytes-margin32-v1，以合成样本与 API usage 校准。它是保守估算，不是精确 tokenizer 或对全部输入的数学保证；未知端点/模型无已验证政策时拒绝外发。

采用共享结构识别与边界保护，复用现有策略入口；不增加新的策略选型。

1. 识别带原文字符半开范围的结构单元：不同长度反引号/波浪线围栏、未闭合围栏、ASCII/Unicode 盒图、Markdown 表格。围栏内标题不参与章节切分。保留空白、换行、缩进与原始顺序。
2. 结构优先于软目标。表格分组以完整行为单位，重复表头单独标为派生上下文；overlap/标题前缀也不混入原文覆盖统计。
3. 为各现有策略记录保护适用性与保守降级；父子策略在父范围内切分，子来源范围转换至文档坐标，并校验包含关系。解析 blocks 路径明确来源坐标对应的文本版本，不能猜原始文件偏移。
4. 硬限必须由明确批准的 Embedding 模型输入限制与计数方式提供；未配置或未知时不宣称已验证可向量化，不猜字符到 token 的安全保证。模型能力的通用治理仍归 RAG-028。
5. 安全拆分不了的结构原文保持可查看，在 chunk metadata 记录 oversized、输入限制依据与未向量化原因。摄取和重解析均过滤 Embedding 输入，保留 embedding=None；禁止填充假向量或截断原文。semantic 的切分期调用同样校验限制，必要时明确回退规则保护。未向量化不等于无法通过稀疏检索或关联父块取回，实际可检索性须分别验证和说明。
6. 上传/重解析结果必须能明确区分保存原文与完整向量化。先检查现有导入结果和分块元数据是否足够表达；不足时列出 API/UI 所需扩展，另获范围确认，不能以导入成功伪称全部内容已向量化。

## 已批准的修改范围

既有：app/rag/chunking/、app/rag/document_parsers/、app/rag/ingestion.py、tests/、evals/、docs/、tasks.yaml。

最小新增候选：app/rag/service.py（过滤上传与重解析 Embedding 输入）；app/rag/embeddings/（输入限制/计数契约及实际 provider 接入，仅本卡所需 Embedding 输入防护）。只改抽象 base.py 不足以获得真实能力；实际模型及限制来源确认后确定具体 provider 文件。若需要配置持久化或 API/UI 暴露，先追加具体方案，不默认为已批准。

上述 service.py / embeddings/ 扩展已由用户于本轮批准。接口核对新增发现：DocumentChunkOut/_chunk_out 未返回元数据，DocumentOut 也没有向量化状态，不能声称现有 API 已呈现部分处理结果。用户随后明确批准 app/api/routes/rag.py 和 README.md：添加兼容的向量化状态/跳过原因字段及摄取结果计数，补契约测试和 Swagger 导出。不新增数据库字段/迁移、不改变既有权限。

API 状态方案：文档区分 vectorized、partial、not_vectorized、unknown；分块区分 vectorized、not_vectorized、unknown，附允许列表内跳过原因和 oversized。存在向量的旧块可确认为已向量化，无向量又无明确记录的旧块标 unknown，不猜原因。Service 对已授权文档批量聚合，避免列表逐文档查询和 API 承载业务 SQL。异步重解析仍返回任务，处理完成后由文档详情查询结果，不宣称创建任务即处理完成。Swagger 使用既有 docs/export_swagger.py 导出到 docs/swagger.json；新增字段默认值兼容已有调用。

备选方案：仅先实现纯切分和 Evaluation，不接入实际导入。这可减少范围，但只算 Partial，不能关闭 RAG-021。推荐完整闭环方案，先批准最小接入范围与能力来源。

## 非目标与风险

不修改 Embedding 模型、检索或融合公式、默认策略；不实施 RAG-022 自动路由、RAG-028 通用模型预算或 LLM 边界/摘要；不重建真实索引、不迁移或删除业务数据、不合并部署。

风险 L1：新导入块边界变化；超长结构可能保存但暂不可检索。回滚代码只影响后续切分，旧索引保留；已重解析数据恢复需单独确认。

## 多 Agent 分工

- 主 Agent：计划、准入、文件归属、摄取接入协调、串行集成验证、任务状态和交付说明。
- 结构实现 Agent：批准后负责共享保护与策略接入；只改分配的 chunking 文件。
- 评测 Agent：独立合成案例与确定性指标，负责分配的 tests/evals 文件；共享数据库测试串行。
- 审查 Agent：整合后只读检查最终 Diff、权限/父子范围/未向量化/回滚证据。

初始两个子 Agent 仅做只读准入核对；用户批准后分别承担结构实现与独立 Evaluation，第三个只读审查 Agent 检查整合 Diff。API 补充继续沿用实现 Agent，主 Agent 同步 README/Swagger 并执行最终集成验证。

## 验收与证据

先写失败测试，覆盖截图同类盒图、围栏内标题、不同围栏长度、未闭合围栏、长表、超长行、父子包含、blocks 降级、上传与重解析超限不调用 Embedding。

切分评测数据使用合成 Smoke/Adversarial，版本 structure-integrity-v0.1；指标包含原文覆盖率、乱序、结构破坏率和长度分布，来源范围可直接按原文复算。不得声称真实检索质量提升，不修改冻结 baseline 或使用 holdout 调参。

使用 D:/DepTooL/anaconda3/envs/ai-assistant/python.exe 运行相关 chunk 测试及 RAG 摄取/重解析回归；执行 Ruff、影响范围 mypy 和 git diff --check。具体命令、退出码和报告在实际执行后记录，当前未运行测试。

独立评审写入 docs/reviews/；实现说明另写 docs/plans/。自动验证后保持人工业务验收待完成状态，不能自动标 done。
