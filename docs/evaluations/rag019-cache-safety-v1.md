# 检索与生成缓存安全 Evaluation v1

版本：cache-safety-v1，2026-10-11；数据分级 Smoke / Adversarial / Observed Regression，非 Gold。依据原始 RAG-019 卡。无质量指标调参，不使用 holdout。

## 环境与可观察结果

E2：临时独立 SQLite、实际 SQLModel/LocalVectorStore、3 维合成 Embedding acceptance-synthetic-v1，及 localhost HTTP acceptance-model、真实 OpenAICompatibleProvider 与 ChatService。本地请求计数代替外部费用证据；不声称检索/生成质量、生产性能或真实计费结果。MockEmbeddingProvider 只用于补充入口回归。机器规格未用于性能结论，本 Evaluation 没有性能/质量达标指标。

| 类别 | 输入与操作 | 判定 | 正式回归 |
|---|---|---|---|
| 热点检索 Smoke | 同主体同查询同参数两次 | 向量调用 1，结果副本隔离；search/对话检索共用 | test_rag_cache.py、test_rag_019_acceptance.py |
| 生成 Smoke | 默认 self 五阶段两个新对话的完全相同请求 | 首次 6 HTTP，第二次保持 6 | default_chatservice_actual_http_count |
| 授权 Adversarial | 跨租户、跨 DB、warm 后禁用/改角色/改上传者 | 隔离或拒绝；命中最新授权 | permissions_mutate_in_new_session 等 |
| 版本 Adversarial | 发布、删除、重解析、索引/模型/配置变更、时间边界跨越 | 旧结果不复用；实际入口读到新内容或拒绝身份错误 | lifecycle_publish_reparse_delete、index_identity_mutation_not_masked、temporal_expiry |
| 数值 Adversarial | NaN/Inf/巨大/负值/0/bool 限额，非法 temperature，变更持久化污染/零向量 | 不用旧缓存掩盖错误/配置变化 | unsafe_numeric_llm_not_cached、numeric_vector_change_never_hidden、bad_limits_bypass_actual_vector、test_response_cache.py |
| 失败 Adversarial | HTTP503/截断/实际模型错、无DONE、取消再入、兜底跳 | 失败不存；重试实际请求；完整匹配模型方可复用 | response_identity_completion_rejected、stream_done_and_cancel_boundaries、fallback_binds_actual_provider |
| 事务 Observed Regression | 缓存空结果后 flush/显式DML/directConnection/rawDriver/嵌套回滚，未commit发布 | 绕过已提交态缓存，实际检索事务内文档；外层回滚恢复原可见结果 | test_rag_cache_review.py |
| 请求快照 Observed Regression | AsyncClient等待期间修改messages/options/端点/模型/凭据，再恢复原输入重入 | 键与实际HTTP共用冻结快照；原输入不收到变更上下文响应，chat/stream均覆盖 | mutable_request_during_transport_not_wrong_key |
| 回滚 Smoke | TTL到期、关闭开关、失败重解析后新Session读取 | 调用原后端、旧有效内容保留，不伪装新版本成功 | ttl_expiry_and_disable、failed_reparse_preserves_actual_old_read |

运行：项目解释器 `python -m pytest -k cache`；精确入口验收 `python -m pytest tests/test_rag_019_acceptance.py tests/test_rag_cache_review.py tests/test_rag_cache.py -q`。实际结果、指纹和独立身份见 `docs/reviews/2026-10-11-RAG-019-verification.md` 与代码审查；关闭对账及剩余人工判断见实现说明。E3 原语测试只支撑内存/TTL边界，不替代必交付项 E2。
