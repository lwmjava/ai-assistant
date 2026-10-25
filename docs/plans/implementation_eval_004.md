# 受控检索策略对照实施计划

## 基线与范围

- 基线候选包含 EVAL-003 未提交改动；EVAL-004 开始前隔离 SQLite 下 `pytest tests/test_evaluation_api.py tests/test_rag_backend.py tests/test_rag.py -q` 为 60 passed、退出码 0。
- 只复用 RagService 的受权检索入口；不新增算法、重排、Query Rewrite、向量库或 RRF 默认值，不调用生成模型。

## 接口与可比性

1. `GET /api/v1/evaluations/retrieval/strategies` 返回当前进程真实可构造且不会降级的后端，以及允许的 `top_k` 范围。
2. `POST /api/v1/evaluations/retrieval/compare` 接受一个查询和 baseline/candidate 两个 `{backend, top_k}` 变体；字段严格白名单，查询和变体有界。
3. 两侧复用同一 User、Tenant、ReadScope、Embedding/VectorStore 和 RAGService，顺序执行并记录各自耗时。
4. 执行前后绑定当前生效索引 ID、Embedding 身份与向量存储类型；身份变化、请求后端静默降级或同参数重复比较返回冲突/不支持，不生成伪对照。
5. 返回候选排名、分数、相似度、来源、文档/块/父块/版本及截断预览；不修改进程配置或线上默认值。

## 错误、验证与回滚

- 权限沿用 evaluations:read 和 RagService reader，跨租户内容不会进入候选；后端故障明确返回 503。
- 测试覆盖参数拒绝、后端回退拒绝、身份漂移、候选映射、耗时、租户隔离和默认配置不变；补 Evaluation Case、OpenAPI、页面与说明。
- 回滚移除比较入口与页面区块，不改现有 `/rag/search` 和默认检索。
