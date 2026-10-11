# RAG-019 独立业务验证

验证身份：独立子 Agent `/root/cache_verification`，未参与业务实现；从 `tasks.yaml` 原始 RAG-019 契约和 `task-card-delivery/SKILL.md` v1.2.0 构造测试，仅维护本报告与 `tests/test_rag_019_acceptance.py`。基线 HEAD：`7709d7bdb67290726f3e66200fab642577f23704`，最终工作区代码以文末 SHA256 为准。

## 证据性质与范围

全部业务验收使用 E2 隔离集成证据：临时独立 SQLite 数据库、实际 SQLModel Session、实际 LocalVectorStore、3 维合成 Embedding `acceptance-synthetic-v1`、实际 OpenAICompatibleProvider、localhost HTTP 服务 `acceptance-model`。服务器使用无费用的合成响应及请求计数，不调用外部付费模型、不接触生产数据。它证明真实入口的调用避免、授权绑定与失败行为，不能证明付费账单、检索质量、生成质量或生产性能。测试数据为 Smoke/Adversarial，不是 Gold。

## 从原始契约构造的验收

| 原始要求 / 失败边界 | 独立操作及观察 | 证据与结果 |
|---|---|---|
| 同查询与参数避免重复向量调用 | 摄取真实文档，重复调用 RAGService.search，比较结果和实际 LocalVectorStore.hybrid_search 次数 | `real_local_query_avoids_second_vector_call`：1 次，通过 E2 |
| 确定性 LLM 请求省调用，实际消息参与身份 | 默认 self 编排，完整 ChatService 五阶段真实 HTTP，另建同用户新对话重复请求 | `default_chatservice_actual_http_count`：首次 6 次、重复后仍 6 次；跨租户新增调用，通过 E2 |
| 完整模型/上下文/端点/凭据绑定 | 同 prompt 命中，再分别改变系统上下文、请求模型、凭据和 endpoint | `http_failure_retry_context_model_endpoint_keys`：各变化新增请求，通过 E2 |
| 异步期间实际 HTTP 请求与缓存键绑定 | 首次 transport await 后变更 messages/options/model/url/key，恢复原请求后再请求；HTTP 服务回显实际 payload | `mutable_request_during_transport_not_wrong_key`：chat/stream 两条入口均不把变更后的响应错误重放到旧键，通过 E2 |
| 缓存命中再授权，租户/数据库隔离 | 暖缓存后另一 Session 修改用户角色/禁用状态；相同 ID 在独立数据库检索；另一租户检索 | `permissions_mutate_in_new_session`、`llm_reauthorizes_before_warm_hit`、`cross_tenant_and_database_binding`：旧请求拒绝，重新鉴权后缩权，没有串结果，通过 E2 |
| 发布/删除/重解析/改权失效 | 真实摄取旧版和草稿，发布新版本、重解析内容、删除，再实际检索；另 Session 修改上传者 | `lifecycle_publish_reparse_delete`、`retrieval_copy_config_and_uploader`：读到当前内容/空结果，不复用旧结果，通过 E2 |
| 索引身份/配置绑定 | 暖缓存后同维异模型变更；RRF 配置变化、tenant→uploader 范围变化 | `index_identity_mutation_not_masked`、`retrieval_copy_config_and_uploader`：身份或旧授权请求拒绝；配置变化重新向量调用，通过 E2 |
| 非确定性、数值边界不缓存 | temperature NaN/±Inf/极大/负/0.7；TTL/容量 NaN/Inf/极大/负/0/bool/entry>total | `unsafe_numeric_llm_not_cached`、`bad_limits_bypass_actual_vector`：不出现一次调用后成功旧命中，通过 E2 |
| 持久化向量 NaN/Inf/溢出/零变化不能被旧缓存掩盖 | 暖缓存后另一 Session 修改实际 chunk.embedding，再查询 | `numeric_vector_change_never_hidden`：每次变更重新调用向量库；非有限结果拒绝，通过 E2 |
| HTTP失败/部分成功/响应身份 | 两次503，然后恢复；模型不匹配、finish_reason=length | `http_failure_retry_context_model_endpoint_keys`、`response_identity_completion_rejected`：失败/不完整不缓存，恢复才缓存，通过 E2 |
| 流式取消/无DONE/实际兜底模型 | provider 流取消后重新进入、完整结束后命中、无DONE重复调用、两次 HTTP503 流请求；实际 FallbackChain 失败跳→HTTP成功跳 | `stream_done_and_cancel_boundaries`、`fallback_binds_actual_provider`：取消/不完整/异常不存，完整缓存，实际响应模型归属正确，通过 E2 |
| 真正 ChatService 流取消重入 | langgraph 简单短路径以真实服务流请求，消费首 token 后关闭，再新对话重入 | `chatservice_stream_cancel_then_reenter`：取消后重新请求、完成后命中，通过 E2；默认五阶段另有非流式覆盖 |
| 回滚/关闭/副本与重试 | 修改调用者返回对象；TTL到期；关闭检索缓存；注入重解析 Embedding 超时，关闭缓存用新 Session实际检索旧块 | `retrieval_copy_config_and_uploader`、`ttl_expiry_and_disable`、`failed_reparse_preserves_actual_old_read`：副本不污染、到期/关闭回原路径、失败后旧 chunk ID/199 内容保留，通过 E2 |

## 实际命令与结果

解释器均为项目规定的 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`。

1. 初始检索 RED：`-m pytest tests/test_rag_019_acceptance.py::test_cache_acceptance_real_local_query_avoids_second_vector_call -q --basetemp data/pytest-tmp/rag019-independent-red`，退出 1：结果正确但实际向量调用 2 次而非 1。这是有效业务失败。
2. 初始 LLM RED：`-m pytest tests/test_rag_019_acceptance.py::test_cache_acceptance_default_chatservice_actual_http_count -q --basetemp data/pytest-tmp/rag019-independent-llm-red`，退出 1：首次 6 次、重复请求累计 12 次而非 6。这是有效业务失败。
3. 首轮独立验证：`-m pytest tests/test_rag_019_acceptance.py -q --basetemp data/pytest-tmp/rag019-independent-green-1`，退出 0：34 passed，43.65 秒。该轮之后授权事务跟踪与流式生命周期实现有修订，不用于关闭最终代码。
4. 请求冻结修复前验证：`-m pytest tests/test_rag_019_acceptance.py -q --basetemp data/pytest-tmp/rag019-independent-final`，退出 0：34 passed，29.12 秒。在事务写入旁路防护、流式关闭与缓存开关变更修复代码上复跑全部旧验收，并补入 streaming HTTP503 不缓存断言。事务 flush/直接 SQL 等新反例由独立审查 Agent 另行验证，本测试文件不冒充该组覆盖。该轮之后发现 F3，不用于关闭其修复。
5. 最终完整独立验证：`-m pytest tests/test_rag_019_acceptance.py -q --basetemp data/pytest-tmp/rag019-independent-frozen-final`，退出 0：**36 passed，38.22 秒**。包含新增 F3 chat/stream 回归，在冻结实际请求后的最终实现上通过。完整输出位于本地忽略目录 `data/rag019-independent-frozen-final.log`；纳入 Git 的测试可复现证据。秒数是测试运行时间，不是缓存性能评测。
6. `-m ruff check --no-cache tests/test_rag_019_acceptance.py`，退出 0。

记录的环境/fixture问题：首次默认 basetemp `run` 因 Windows 权限在 setup 阶段失败，改用新隔离目录，不当业务 RED；默认 Ruff 缓存目录权限失败，`--no-cache` 检查通过。localhost provider 的空 Authorization token 会触发既有 httpx 协议错误，fixture 改用非空合成值。首轮生命周期 fixture 缺少 ParsedDocument 必填 metadata 已补；NaN/Inf 后端结果被实现拒绝，断言接受该安全失败并保留重新调用断言，没有降低业务要求。

两条 NumPy RuntimeWarning 来自显式污染向量的反例：Inf 除法与极大值平方和溢出。测试验证污染不会被旧缓存掩盖；此卡未重写既有向量算法，不把这些反例作为质量结果。

## 结论及限制

原有 34 项和后续 F3 的 2 项回归在最终冻结请求实现上共 **36 项通过**。F3 已修复并独立重验，没有未修复的业务验证缺陷。独立代码审查、全仓门禁、人工业务验收与最终任务关闭由主 Agent 对账；本报告不自行将任务标为 done。未进行真实收费模型、Milvus或生产多实例验证，无前端变更故不要求页面验收。

### 后续入口反例 F3：实际 HTTP 与缓存键绑定（P2，已修复）

原始请求对象在 `AsyncClient.__aenter__` 的 await 期间变化时，首次缓存键仍对应旧 messages/options/provider，而实际 HTTP 使用 await 后的可变对象。响应在旧 context/model 键下被存储。等级最终统一为 **P2**：反例证明进程内可变请求对象造成上下文绑定错误，未证明跨租户越权；最初临时 P1 分类已按主 Agent 与独立审查的收敛决定修正。

独立新增 `test_cache_acceptance_mutable_request_during_transport_not_wrong_key`，对 chat/stream 两条入口分别执行：真实授权范围绑定；localhost HTTP 回显实际 payload 内容/temperature/model；首次 transport await 后变更 messages、temperature、max_tokens、provider model/base_url/api_key，完成后恢复所有原参数，第二次请求必须返回原上下文对应内容。

有效 RED：`-m pytest tests/test_rag_019_acceptance.py -k mutable_request -q --basetemp data/pytest-tmp/rag019-independent-request-red2`，退出 1，**2 failed / 34 deselected，5.25 秒**。原请求的第二次响应错误复用 `mutated-context|0.7|mutated-model`，真实 HTTP 仅发生一次。日志 `data/rag019-independent-request-red2.log`。首轮 fixture 将 temperature 重置为 int 0 而非原 float 0.0，导致 key 变化和字符串表示差异，不当有效缺陷证据；已修复 fixture 后得到上述真实错误复用。

实际最小修复：首个 await 前深复制有效 options/payload 并冻结 headers/url/timeout，键与真实 HTTP 发送使用同一快照；响应身份与冻结 payload model 比较。独立完整 36 项最终验证通过，两个反例不再出现错误重放。最终代码与测试指纹见下表；旧 34 项仅作历史证据。

## 验证时 SHA256

| 文件 | SHA256 |
|---|---|
| tests/test_rag_019_acceptance.py | 81584F16EA2F4198F934FBD101C261CB0E6A7AFBDEC6A5F37F3FE454A8B51642 |
| app/core/response_cache.py | 6704CF0CB6574E737B8D92407481C9A165D4BDA07770EBDC623C9AE03E56588C |
| app/services/cache_scope.py | 1F31BD5CEABB5719CB90DA7C8C00023416AF258B6C9EC848D4774D9F932ED9F8 |
| app/rag/cache.py | C650E4DD4E359DA4FF02ED8CA0A2A485B2355725D31334660FD8BD507A3D5D48 |
| app/rag/service.py | 61A4654A5760734AF26E3AE8E2DF8C9EA866A13CA3EE1489620A254FDD667337 |
| app/llm/openai_compatible.py | 422BBD6C965264FB5EF275F83431E99DBBE8E35C79E4D1CBE041CECA1E20F3AC |
| app/services/chat_service.py | FB35C6AE1DF7F29548AFA1536936384D9F6CAAA117F80BA390C0C566CFADA1E3 |

