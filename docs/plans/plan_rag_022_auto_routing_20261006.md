# RAG-022 自动切分策略实施计划（复审修订）

日期：2026-10-06。以本修订替代初稿中“不保存参数、不增加迁移”的缩减方案；完整实现必须符合 tasks.yaml。架构依据 ADR-0005 Accepted，前置 RAG-021 已验收。失败保护的根因与具体修复顺序见 [修复方案](plan_rag_022_failure_and_plan_validation_fix_20261006.md)。

## 目标、现状与方案

原 auto 仅看文本，无法根据解析 blocks/bbox 选择 format_aware/layout_aware；原重解析重新读取当前参数，不能保证同计划重放。初版提前删除旧块，异常提交会丢块；计划加载未校验版本及参数。

1. 提取类型、blocks/bbox、标题、结构单元、CJK 比例、长度信号；保守决策顺序为显式请求、bbox、blocks、标题/结构单元、语言/长度兜底。记录版本、原因及不含正文的信号摘要。
   TextDocumentParser 的逐行辅助块已去空白，不作为 auto 的格式/版式依据；上传和重解析都使用保真的正文，包括显式 format 与历史计划。
2. 增加可空 Document.chunk_plan 与可逆迁移，保存路由版本、策略、原因和完整有效 ChunkParams。服务端 input_policy 不持久化。上传和解析上传均保存计划，重解析优先重放完整计划。NULL 才是历史兼容；存在但损坏、未知版本或非法参数的计划拒绝重解析，保留旧数据。
3. 参数类型/范围/策略及嵌套 child_params 严格校验。计划重放不用当前默认值补齐缺参。旧 NULL 计划复用一致的旧策略，否则走可解释路由并生成新计划。
4. 重解析先校验、切分和 Embedding，再在同一 SQL 事务中替换旧块、计数与快照；LocalVectorStore 不调用会提前 commit 的删除方法。服务及导入任务失败 rollback，单独持久化失败状态。外部清理抛错不能被吞掉；外部系统不宣称跨系统事务一致性。
5. 复用 RAG-021 结构保护及父子源范围协议，显式 parent_child 的子块仍在父块内。auto 不强行覆盖十种策略。
6. 增加实际服务 auto 上传→提交→新 Session→重解析→第三个 Session 的结构 Evaluation，复用 RAG-021 全部样本与评测器，区分冻结基线、同协议重算、同入口控制组和候选。包含 text/blocks/bbox，记录覆盖、结构破坏、乱序、范围及长度，而非只比较块数。

## 非目标与范围

不改默认 structured、Embedding 模型、检索或 RRF，不自动启用 semantic/parent_child，不重建真实索引，不操作开发/生产数据库，不推送、合并、部署。Mock 和合成 blocks 不能证明检索质量、真实解析器或 Milvus 生产一致性。

范围遵循 RAG-022 allowed_paths：app/rag、app/models/rag.py、alembic、tests、evals、docs、tasks.yaml；不改 .env。实际文件映射见实现说明。

## 验收与回滚

路由决策、配置变化后参数重放、旧数据兼容、父子范围、损坏/未知计划、真实导入任务 Embedding/数据库故障保护必须有可复现测试。迁移在临时库升级、降级、再升级，旧文档保留且计划 NULL。复算结构报告，运行相关回归、全库 pytest、ruff、mypy，独立审查后同步任务状态与实现说明。

应用回退时按一个整体恢复模型、factory、service、import_jobs、routing/plan 模块；先备份后才考虑降级 schema。保留 nullable 列一般可兼容旧代码；删除该列会丢失计划，应优先保留。真实数据库降级及索引重建需另行明确授权，本轮仅临时库验证。
