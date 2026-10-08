# 跨向量库混合语义实施计划

日期：2026-10-08；任务：RAG-036。RAG-015 有历史真实服务通过证据；本轮重新核验发现 RAG-032 的 Milvus 重建/回退未闭合，须先整改并复验 RAG-015，不能先关闭本卡。

## 目标与现状

Local 在授权、当前版本与索引过滤后的全部有效块上计算 Dense/BM25；Milvus 只在远端 Dense 的 `max(top_k*4,20)` 个候选经 SQL 可见性过滤后计算 BM25。两个候选宇宙不同，不能宣称等价混合召回。复核 `milvus.py`，BM25 全零时仍使用候选自然顺序作为稀疏排名，导致 Dense-only 的 RRF 分数翻倍；Local 已使用空排名。

## 方案与允许范围

先用真实两适配器与有限远端替身写失败测试，断言全零的单路分数；最小修复仅将 Milvus 全零稀疏排名置空。保留原 RRF、窗口与过滤逻辑。追加合成固定向量语义切片与真实 Milvus 对照脚本，记录词面命中在 Dense 窗口之外的遗漏，以及租户/上传者/当前版/软删/索引过滤与候选耗尽。

允许 `app/rag/vectorstore/`、`evals/`、`scripts/`、`tests/`、`docs/`、`tasks.yaml`；本实施者不改任务状态。默认 local、Milvus Partial 保持。

## 非目标与风险

不引入独立稀疏召回、不改 RRF、不调 holdout、不重建或删除既有 collection。固定向量只能证明检索语义，不证明真实 Embedding 质量。真实服务写入只用唯一新测试 collection，保留 SQLite 与远端 collection。

用户本轮批准 `proposal_rag_real_evaluation_authorization_20261008.md` 的费用与外发范围：既有 DashScope text-embedding-v3、1024维，仅合成语料，累计最多20次 HTTP 尝试、100,000输入 tokens、人民币0.10元。`scripts/rag_hybrid_embedding_check.py` 将4条合成文本一次批量提交；请求前按每条 UTF-8 字节数加32保守预占 tokens 和费用，SQLite 事务持久化累计尝试，不退款失败预占，无 SDK 隐式重试，usage 未知/超预占即失败并停止。价格再次核对官方页面0.0005元/千tokens。保留实际usage、请求标识、模型版本、预算台账与向量，后续可复用向量零费用复验；不输出密钥、完整失败响应或内部连接串。真实模型只核对小规模词面与过滤切片，不宣称总体质量提升。

## 验收与回滚

全零（空查询词/空文档词/无交集）精确返回 `1/(k+rank)`；正分仍双路融合。对照包含关键词窗口遗漏与过滤后少返回；真实 Milvus 报告固定数据集、向量来源、服务版本、参数、结果与未验证项。pytest 每次 UUID basetemp 与独立数据库，使用指定 conda Python；独立审查由未参与实施的子 Agent 执行，主 Agent 整合。回滚仅恢复 Milvus 全零分支代码；无 schema 或重要数据迁移。测试产物保留，不自动删除。
