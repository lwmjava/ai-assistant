# RAG-021 结构完整性保护实现说明

日期：2026-10-06。状态：自动验证、独立审查与负责人业务验收已完成；RAG-021 为 done。API/README 范围已由用户明确批准并实施。

计划：[结构完整性实施计划](plan_rag_021_structure_integrity_20261006.md)。架构依据：[ADR-0005](../adr/0005-rag-adaptive-chunking-context-budget.md)。分支 codex/rag-021，基线提交 16308e75137dcdae59d246ae26d06759db09d370。

## 已实现的行为

所有现有策略的直接 split 和注册入口共享确定性结构保护。识别 fenced code（围栏长度、反引号/波浪线、未闭合围栏）、保守 ASCII/Unicode 盒图和 Markdown 表格；结构文档采用明确的规则降级，保留空格、缩进、换行和源顺序。软 chunk_size 不再字符切断完整代码/盒图；表格按完整行分组，表头重复单独记录派生上下文。

结构文档的来源范围为输入文本的 Python 字符半开区间，记录文本 hash 与 structure-integrity-v0.1。ParsedBlock 路径坐标绑定拼接后的阅读流（layout 按阅读顺序），附解析块来源，不冒充 PDF/Office 二进制文件偏移。标题来源与章节层级绑定真实范围；围栏内标题不改变章节。父子源范围绑定同一全文，派生表头不进入原文覆盖统计。结构路径关闭旧 overlap；普通无结构路径保持旧策略，未声称该旧路径统一具备完整来源范围。

输入护栏覆盖 semantic 切分期及上传/重解析最终向量化：未知政策不外发；超限结构保持原文并记录 oversized/跳过原因，embedding=None；不会伪造零向量或使后续向量错位。Mock 的不限输入政策显式标为离线用途，不能证明生产输入限或检索质量。

当前已核对 DashScope text-embedding-v3、1024 维、batch=10。官方每条输入 8192 tokens、每批 10 条；用户批准的 UTF-8 字节数 + 32 保守估算版本为 dashscope-v3-utf8-margin32-20261006。仅为核对过的端点/模型启用；自定义端点/其他模型没有已验证政策时拒绝外发，后续通用模型能力归 RAG-028。未修改 .env 或复制主仓库凭据。

原文保存与向量化是不同状态：未向量化块可能仍通过稀疏检索或父块关联取回，不能统称不可检索。权限、租户与版本过滤继续沿用现有路径。

## 修改与任务映射

| 文件 | 交付 |
|---|---|
| app/rag/chunking/base.py、registry.py、structure.py | 共享保护、软目标、原文范围/章节来源、ParsedBlock 降级 |
| app/rag/chunking/strategies/parent_child.py、semantic.py | 父子来源一致性与切分期输入护栏 |
| app/rag/ingestion.py | 历史文本切分入口的结构保护 |
| app/rag/embeddings/base.py、input_limits.py、mock.py、openai_compatible.py | 输入政策、端点/模型限制、保守计数及批量限制 |
| app/rag/service.py | 摄取/重解析筛选输入并持久化未向量化原因 |
| app/api/routes/rag.py、README.md、docs/swagger.json | 兼容的文档/分块状态响应、公开原因与契约说明 |
| tests/test_rag_vectorization_api.py | HTTP 结果、旧数据、原因脱敏、授权/租户与批量统计契约 |
| tests/test_chunk_structure_integrity.py、test_rag_chunk_embedding_limits.py | 结构破坏与超限不外发、保留原文、向量对齐回归 |
| tests/test_embeddings.py、test_rag.py、test_rag_log_minimization.py | 原故障/HTTP 模拟提供者声明测试输入政策，保留原故障与日志断言 |
| evals/chunk_structure_integrity/、tests/test_chunk_structure_evaluation.py | 独立结构数据、评分器、修改前与候选报告 |
| evals/embedding_input_calibration.py | 仅合成样本的真实模型 usage 校准，不输出密钥/正文 |
| tasks.yaml、docs/plans/、docs/adr/、docs/reviews/ | 范围批准、实现/验证/审查和残余限制 |

## 验证记录

解释器：D:/DepTooL/anaconda3/envs/ai-assistant/python.exe。测试数据库在当前工作树 data/ 下；最终组合使用 sqlite:///./data/test_ai_assistant_rag021_delivery.db，与生产和主仓库数据库隔离。临时目录使用 --basetemp=data/pytest-tmp/rag021-*；未删除旧测试库。

- 修改前 tests/test_chunking.py：22 passed，退出 0。首次基线命令误引用不存在的 tests/test_rag_parent_child_integrity.py，退出 1、未运行测试；纠正到真实测试文件后才记录基线。
- 结构失败案例先出现 13 failed；新增标题/hash/表头案例又先出现 3 failed，再实施修复。
- 摄取新增测试首次因尚无输入契约导入失败；纠正测试调用后明确出现 3 个外发超限断言失败，证明上传与重解析缺少过滤。
- 初轮组合 129 passed、1 skipped、1 failed；故障模拟提供者补显式政策后，重复默认测试库出现残留与去重干扰，改独立测试数据库隔离，不删除业务数据、不弱化故障断言。
- 组合回归（chunking、结构评价、输入限、embeddings、rag、import_jobs、backend、parent_mapping、chunks、safe_parent_expansion、log_minimization、document_parsers、document_parser_blocks）：203 passed、1 skipped，退出 0。JUnit：data/pytest-tmp/rag021-delivery-results.xml。日志模拟测试新增输入政策后原 18 条全部通过，退出 0。
- `python -m pytest -k chunk -q --basetemp=data/pytest-tmp/rag021-chunk-gate`：75 passed、2 skipped、597 deselected，退出 0；有既有 ast.NameConstant 弃用警告。
- `python -m ruff check .`：退出 0。
- `python -m mypy app/`：174 source files，无错误，退出 0。
- 独立结构评分器 6 条单测 --noconftest：退出 0，未写共享数据库。

以上组合运行后，独立审查发现并修复父子标题继承 P2。指定失败用例先 1 failed；最终主 Agent 运行 chunking、structure_integrity、structure_evaluation、input_limits、parent_mapping 五组专项：68 passed，退出 0；候选两个报告重新生成，Ruff 与 git diff --check 再次退出 0。独立复审复现原案例，章节 Outer/Inner 和原文范围正确、无新阻断代码问题。正式记录：[独立审查](../reviews/2026-10-06-RAG-021结构完整性独立审查.md)。扩大组合在 P2 修复前运行，修复后用上述影响范围验证，不将未重复项写成再次执行。

## Evaluation 与校准

数据集 structure-integrity-v0.1，SHA256 83f77e0940d7be92a7d9ddb5491f44a88119a11291769d7c027a4d21ec9dce3a；8 个 AI 合成 Smoke/Adversarial 案例，非 Gold。10 策略及 parent_child 父子分层每份 88 行，父层不能掩盖子层缺陷。

修改前报告 evals/chunk_structure_integrity/baseline.json，从固定 16308e7 的 chunking/ingestion 临时快照执行，78/121 个受保护单元发生破坏（跨策略重复计数，不是独立文档数量）。候选 candidate.json、合成字符硬限 100 的 candidate_test_limit.json：结构破坏、原文丢失、乱序与非法范围为 0，超长代码保留并显式 oversized。缺失字符基线可包括旧 strip 删除的空白及无法 exact 重建来源的内容，不能解释为真实检索质量。

真实合成校准 reports/embedding_input_calibration.json：6 例（中文、代码、盒图、表格、emoji/组合字符、接近字节阈值）。每例 byte+32 均覆盖 API 返回的实际 token 数；近阈值样本估算 8112、实际 7069。仅有限样本支持保守方案，不是精确 tokenizer，也不是全输入数学保证。未进行真实检索质量或 LLM 生成层 Evaluation，不更改冻结 baseline、不使用 holdout 调参。

## 人工验收与验证边界

用户已批准 API/README 扩展。文档摄取、上传、列表、详情和发布响应返回 vectorization_status、vectorized_chunk_count、not_vectorized_chunk_count、unknown_chunk_count、embedding_skip_reason_counts；分块返回 embedding_status、embedding_skip_reason、oversized。新增字段带兼容默认值；已有向量的旧块判 vectorized，无向量无明确记录的旧块判 unknown。任意历史错误正文不公开，原因只允许三种稳定代码或 unknown_reason。

Service 在重新校验权限后按 (document_id, tenant_id) 批量统计，每 200 文档一批，只读 ID、向量存在状态和元数据；5 文档一条统计查询、201 文档两条，避免逐文档 N+1。计数失配、空文档和未知旧块不会误报完整。分块列表也按已授权文档的租户过滤，损坏跨租户关联行不输出内容或原因，合法系统管理员跨租户权限保持。异步重解析仍返回 202/pending，任务完成后再查询存储结果，不把提交任务写成完成。

API 契约先出现缺字段等 5 项失败，数量失配 3 项失败，错误跨租户分块暴露又实际 1 failed；修复后 13 项私有内存库测试通过。主 Agent 最终串行组合新增 API 契约在内共 15 个文件：217 passed、1 skipped，退出 0，数据库 sqlite:///./data/test_ai_assistant_rag021_api_delivery.db；命令 `python -m pytest tests/test_chunking.py tests/test_chunk_structure_integrity.py tests/test_chunk_structure_evaluation.py tests/test_rag_chunk_embedding_limits.py tests/test_embeddings.py tests/test_rag.py tests/test_rag_import_jobs.py tests/test_rag_backend.py tests/test_rag_parent_mapping.py tests/test_rag_chunks.py tests/test_rag_safe_parent_expansion.py tests/test_rag_log_minimization.py tests/test_document_parsers.py tests/test_document_parser_blocks.py tests/test_rag_vectorization_api.py -q --basetemp=data/pytest-tmp/rag021-api-delivery --junitxml=data/pytest-tmp/rag021-api-delivery-results.xml`。最终 Ruff 全库退出 0，mypy app/ 174 files 退出 0；docs/export_swagger.py 实际导出 docs/swagger.json，变更仅三个相关响应 Schema 及两个接口说明。

2026-10-06 负责人新上传合成样本，提供详情接口截图：6 个分块中 4 个向量化、2 个 input_limit_exceeded，unknown 为 0，状态 partial；并明确回复“RAG-021可以验收了”。业务验收通过，直接截图证据范围及未单独提供人工截图的项目见 [人工验收记录](../checklists/rag-021-business-acceptance.md)。当前前端没有专用向量化状态展示，通过 API 验收；不把接口新增声称为 UI 完成。现有文档不会自动重建；需要重解析时另行明确选择，重要索引/生产迁移继续单独授权。

最终 1 skipped 为 tests/test_rag_backend.py 整个模块收集跳过：当前环境 langchain_core 已安装、llama_index 未安装，该文件模块级 importorskip 导致整文件跳过，因此其中 LangChain/Native 案例也未实际执行，不能称这些多后端专项已通过。未安装新依赖或降低门禁；实际部署后端由负责人验收补充真实操作证据。前端没有修改，本轮未执行前端构建；真实检索质量和生成层评测未执行。

保守识别不覆盖所有任意 ASCII 艺术或所有 Office 表格语义；不无损强拆任意代码/盒图；不实施自动选型、摘要、Reranker、生成预算或生产部署。回滚代码不修改既有索引；已重解析文档若需恢复旧索引，须人工批准。
