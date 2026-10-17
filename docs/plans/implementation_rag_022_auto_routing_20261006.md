# RAG-022 自动切分策略与失败保护实现说明

日期：2026-10-06。计划：[主计划](plan_rag_022_auto_routing_20261006.md)、[复审修复方案](plan_rag_022_failure_and_plan_validation_fix_20261006.md)。架构依据 ADR-0005 Accepted。此说明替代初稿中提前删除旧块、坏计划静默降级、仅回退三个文件等过时描述。

## 实际行为

有知识库写权限的主体使用现有上传/导入/重解析入口时，auto 依据解析 blocks/bbox、标题、结构单元、语言与长度选择已有策略，记录路由原因和版本。显式策略仍优先；默认仍 structured，不自动选 semantic/parent_child。TextDocumentParser 的逐行 blocks 去除了空白，不能作为保真结构：auto 对它按正文路由，显式 format/layout 或旧计划也对保真正文 split。Office/PDF 的结构块仍按原规则处理。

Document.chunk_plan 保存版本、策略、原因及完整有效 ChunkParams；input_policy 每次由服务端注入。同计划重解析使用保存值，配置变化不补入当前默认。历史 NULL 计划复用一致旧策略，否则可解释路由并生成计划。非空损坏、缺字段、未知版本/策略、非法类型或范围及非法嵌套参数直接拒绝，不伪称精确重放。

重解析先验证、切分及生成 Embedding，成功后才替换旧块。在 Local 同库路径，旧块、计数、计划及原始解析快照一起提交；失败 rollback。导入任务 rollback 后另行记录 failed，避免 finally 把旧块删除提交。外部清理抛错不再吞掉；外部向量库与 SQL 跨系统原子性仍没有证明。

## 文件与验收映射

| 实际文件 | 交付与证据 |
|---|---|
| app/rag/chunking/routing.py、factory.py | 可解释路由、text parser 事实信号；tests/test_chunk_routing.py |
| app/rag/chunking/plan.py | 完整版本化计划与严格递归校验；tests/test_chunk_plan_validation.py |
| app/models/rag.py、alembic/versions/a1b2c3d4e5f6_add_document_chunk_plan.py | nullable 计划列；tests/test_chunk_plan_migration.py 临时库往返保留旧行 |
| app/rag/service.py | 保存/重放参数，保护替换事务，文本 parser 正文保真；路由与真实解析器测试 |
| app/rag/import_jobs.py | 失败回滚后持久化任务状态；tests/test_rag_reparse_failure_safety.py |
| tests/test_chunk_routing_real_parser.py | 实际 TextDocumentParser 上传→新 Session→重解析→新 Session 完整正文相等，包含缩进/末尾换行；auto 与显式 format |
| evals/chunk_auto_routing/、tests/test_chunk_auto_routing_evaluation.py | RAG-021 8 样本 × text/blocks/bbox/真实 parser，32 组结构对照与计划重放；评测器反例检测 |
| docs/plans、docs/reviews、tasks.yaml | 修复方案、实现说明、正式验收与最终任务状态 |

父子范围仍由 RAG-021 协议保障，test_parent_child_source_ranges_still_contained 验证子范围在父内；本轮未另造父子策略。

## 复现与本轮证据

统一解释器：D:/DepTooL/anaconda3/envs/ai-assistant/python.exe。以下命令前置 python 均指该解释器，数据库使用单独 test_ai_assistant_rag022_*.db 或内存库，未访问生产库。

- 先红：真实 _process_job 的 Embedding、坏参、缺参、未知版本及插入故障 5 项失败，复现旧块被删或 Session 不可提交；修复后通过。外部清理抛错另加回归，失败仍保留旧 SQL 数据。
- 独立审查追加真实文本解析器问题；扩充评估先红：1 failed/1 passed，首 parser 样本覆盖 11/85，结构破坏 1、无效范围 2。修复后转绿。专项测试编写期间修正了错误调用参数及末尾换行预期，属于测试自身问题，不作为业务故障证据。
- 最终 auto 专项命令：python -m pytest tests/test_chunk_plan_validation.py tests/test_chunk_plan_migration.py tests/test_rag_reparse_failure_safety.py tests/test_chunk_routing.py tests/test_chunk_routing_real_parser.py tests/test_chunk_auto_routing_evaluation.py -q --basetemp=data/pytest-tmp/rag022-target；47 passed，退出 0。DATABASE_URL=sqlite:///./data/test_ai_assistant_rag022_target.db、RAG_CHUNK_STRATEGY=auto。
- python -m evals.chunk_auto_routing.run --output evals/chunk_auto_routing/report.json；退出 0。报告包含数据集/冻结报告/最终代码 SHA256。32/32 计划和文本/源范围重放一致，2308/2308 字符覆盖，44 个保护单元破坏 0，乱序/无效范围 0。同入口控制组与候选相等；冻结 RAG-021 的 CRLF 长度差由服务换行清洗造成，不是质量提升。
- python -m ruff check --no-cache .：All checks passed，退出 0。首次启用默认缓存因 .ruff_cache 拒绝访问而未完成，使用标准 --no-cache 后发现并修正一个 import 顺序问题，再全库检查通过。
- python -m mypy app/：Success: no issues found in 176 source files，退出 0。
- 首轮全库 pytest：729 passed、2 skipped，退出 0；随后独立审查发现真实 parser 问题，新增修复与回归，因此不能把首轮当最终结论。最终 python -m pytest -q --basetemp=data/pytest-tmp/rag022-final2 --junitxml=data/rag022-final2-pytest.xml：733 passed、2 skipped、1 warning，退出 0（231.40 秒），DATABASE_URL=sqlite:///./data/test_ai_assistant_rag022_acceptance2.db、RAG_CHUNK_STRATEGY=structured。跳过可选 MCP/llamaindex 未安装，既有 ast.NameConstant 废弃警告；证据见正式验收报告及 data/rag022-final2-pytest.log、data/rag022-final2-pytest.xml。

## 边界、发布与回滚

评估为 AI-authored Smoke/Adversarial，8 维 Mock，不是 Gold。真实 Text parser 已验证；Office/PDF 的 blocks/bbox 仍是合成输入，未证明真实多栏解析。正文 source 坐标属于保守清洗后的文本或解析阅读流，不推广成所有格式原始字符偏移。保护单元可超过软目标，此指标不认证真实 Embedding 硬限。

未改 Embedding 模型、检索/RRF、默认策略、权限及前端，未宣称检索质量提升或真实 Milvus 跨系统替换成功。Milvus 仍 Partial：远端删除成功而 SQL 失败的原子性、适配器内部吞错等既有问题不因本轮通过而消失，须在正式向量库治理范围验证。没有推送/合并/部署或自动重建真实索引。迁移仅临时库执行；实际环境上线需先备份并执行批准的 schema 升级。

回滚应整体恢复模型与业务模块，优先保留 nullable 计划列以兼容旧应用。schema downgrade 会删除计划，必须先备份并获得真实库操作授权；临时库已验证 upgrade→downgrade→upgrade 保留旧文档。真实索引重建需另行授权。
