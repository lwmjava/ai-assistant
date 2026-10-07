# RAG-028 实现说明：模型能力契约与全部调用预算

日期：2026-10-07
分支：`rag-batch-20261007`，基线 HEAD `671901f`
契约出处：`tasks.yaml` RAG-028；`docs/adr/0005-*.md` §9；推进清单 §8
风险：L2

---

## 1. 目标与现状差距

| 契约要求 | 现状（基线） | 差距 | 证据 |
|---|---|---|---|
| provider / model / deployment 的能力**版本化** | 只有裸字符串配置（`LLM_DEFAULT_MODEL` 等），没有任何能力实体 | **缺** | `app/core/config.py:74`；`app/llm/openai_compatible.py:26` |
| 优先适用官方计数器，缺失时用经校准的保守估算 | 全仓无生成侧计数；只有 RAG 摄取侧的字节上限 | **缺** | `app/rag/embeddings/input_limits.py:16` |
| 每次实际 payload 的 Guard，含工具与 critique | 请求直接发出，无任何预算核对 | **缺** | `app/llm/openai_compatible.py:57,71` |
| 未知模型无批准配置时拒绝 | 任何模型名都照发 | **缺** | 同上 |
| 最终 payload + 输出预留 + 余量 ≤ 已验证限制 | 无「输出预留」「余量」概念 | **缺** | 同上 |

---

## 2. 非目标（本卡不做）

- **不向 LLM 自报窗口**：护栏只在本地判，不把窗口写进 prompt，也不问模型「你能吃多少」。
- **不猜未知上限**：`deepseek-chat` 一类查不到可靠依据的模型不登记，由运营者声明或拒绝。
- **不换供应商**：不改兜底链、不改路由顺序、不新增 provider。
- 不引入新依赖：`tiktoken` **未**写进 `requirements`，未安装也不影响运行（见 §5）。

---

## 3. 决策与依据（2026-10-07 实取厂商公开页面）

| 部署 | 模型 | 上下文窗口 | 最大输出 | 依据来源 |
|---|---|---|---|---|
| `api.openai.com` | `gpt-4o-mini` | 128000 | 16384 | <https://platform.openai.com/docs/models/gpt-4o-mini> |
| `api.deepseek.com` | `deepseek-flash` | 1000000 | 384000 | <https://api-docs.deepseek.com/quick_start/pricing> |
| `api.deepseek.com` | `deepseek-v4-pro` | 1000000 | 384000 | <https://api-docs.deepseek.com/quick_start/pricing> |

用户已批准的裁决（本卡按此执行，未自行扩张）：

1. **保留 `deepseek-chat` 为默认模型**；
2. **护栏默认开启，且未知即拒绝**；
3. 新增「运营者显式声明」通道：运营者必须**同时**填写窗口 / 最大输出 / 依据来源才放行。

`deepseek-chat` 已不在厂商在售模型表内，各来源数字互相矛盾，因此**不登记**：留着默认模型名是为了不改既有部署行为，代价是使用它必须先声明。这是「可用但要自证」，不是「猜一个数让它跑」。

---

## 4. 设计

### 4.1 能力契约（`app/llm/capabilities.py`）

`GenerationCapability` 是 frozen dataclass，改一条就是换一个版本：

```
deployment / model / context_window / max_output_tokens
counter / counting_method / safety_margin / source / verified_on / version / notes
```

- **部署参与身份**：`(deployment, model)` 才是主键。归一化规则：去凭据、host 小写、
  去尾斜杠。同一个模型名在自建网关上跑，不等于厂商官方接口上的同一模型，窗口
  不能跟着模型名一起被借走（与 RAG-021 在 Embedding 侧立下的规矩一致）。
- `__post_init__` 校验：窗口与最大输出必须为正整数，来源 / 核对日期 / 版本必填，
  计数器必须可调用——**不完整的契约直接构造失败**，而不是带着空洞跑进护栏。
- 解析顺序：内置已核对 → 运营者声明 → `None`（= 无批准配置）。
- 声明条目 `counting_method="operator-declared"`、`verified_on="operator-declared"`，
  明确不是本仓库核对过的条目。

### 4.2 计数（`app/llm/counters.py`）

- 官方计数器优先。`official_counter_for()` 惰性 `import tiktoken`，
  **只在 OpenAI 官方域名下**返回计数器（拿 OpenAI 的 tokenizer 去数别家的 token
  是伪造精度）；未安装或模型未登记 → `None`。
- 退回时用的是 RAG-021 已批准的保守估算：**UTF-8 字节数**，并加上每条消息的框架开销
  （口径取自 OpenAI cookbook：`tokens_per_message=3` + 回复引导 3，本实现按 4/条计）。
  只算正文会系统性偏低，而工具调用与自纠错恰好都是「条数多、每条不大」的形态。
- 局限必须如实披露：字节数是**估算**，不是精确 tokenizer 输出。一个汉字 3 字节 ≥ 1 token，
  英文约 4 字符 1 token，因此偏保守——宁可拦下刚好够用的请求，也不放行超限请求。

### 4.3 预算（`app/llm/budget.py`）

```
实际 payload + 输出预留 + 余量 ≤ 已验证上下文窗口
```

原因码：`capability_unverified`（无批准配置）/ `output_reserve_exceeded`
（预留输出 > 该模型最大输出）/ `context_window_exceeded`。

`ContextBudgetExceeded` 的消息**只含计数数字与原因码**，不持有 messages、不含工具返回
原文——异常会顺着 `logger.exception` 进日志，也会被转成面向用户的提示，两端都不能泄漏正文。

### 4.4 接入点（`app/llm/openai_compatible.py`）

`chat()` 与 `stream_chat()` 在**真正发出请求前**各调一次 `_guard()`：

- payload = 本次真正要发的 messages（工具返回、critique 轮次、历史都在里面，天然被计入）；
- 输出预留 = `options.max_tokens`，未给则用 `LLM_OUTPUT_RESERVE_TOKENS`；
- 余量 = `LLM_BUDGET_SAFETY_MARGIN` + 能力自带 margin；
- 违反 → 抛 `ContextBudgetExceeded`，请求**不发出**；
- `LLM_CAPABILITY_GUARD_ENABLED=false` 时完全跳过，行为与 RAG-028 之前一致；
- 意图 / 兜底各跳按各自 base_url + model 解析能力，不共用对话主模型的窗口。

`FallbackChain` 不把预算错误当 failover：`ContextBudgetExceeded` 既不是网络错误也不是
对方状态码，换家只是拿另一家的窗口再判一次同样的 payload，属于白跑
（`app/llm/routing.py:27` 已注明；测试有断言）。

### 4.4b 建链时的「未批准」告警（`app/llm/factory.py`）

护栏默认开启且未知即拒绝，如果什么都不说，运维升级后只会看到「对话只剩固定提示」
而找不到原因。因此 `_client_for()` 在构造完每一跳后核对一次：该 profile 的模型
拿不到已核对或已声明的能力，就打一条 warning：

```
llm_capability_unapproved profile=<chat|intent|fallback> model=<模型名>：
该模型没有已核对或已声明的能力配置，护栏开启后调用会被拒绝。
请登记 LLM_CAPABILITY_DECLARED 与 LLM_CAPABILITY_DECLARED_SOURCE。
```

字段只有 `profile` 与 `model`：**不带 base_url、不带密钥、不带任何请求内容**。
护栏关闭（`LLM_CAPABILITY_GUARD_ENABLED=false`）时不打——那时本来就不会拒绝。

### 4.5 调用方（`app/agents/pipeline.py`、`app/services/chat_service.py`）

两端都捕获 `ContextBudgetExceeded` 并转固定中文提示
「本次请求内容超出模型已登记的上下文预算，请缩短输入或拆分后重试。」
异常原文、模型名、计数数字都不给用户。日志沿用既有的管线失败事件名，
不新增会带正文的日志（`app/security/log_redaction.py` 的事件名映射无需改动）。

---

## 5. 局限（如实记录，不为了凑验收去补）

- **计数不是精确 tokenizer**：官方计数器 `tiktoken` 未随本仓库安装
  （本机 2026-10-07 实测 `No module named 'tiktoken'`），当前**一律**走保守估算。
  装上 `tiktoken` 后 `official_counter_for()` 会自动返回计数器并被优先使用，无需改代码；
  本卡**未**把它加进 `requirements`，因为那是一次未经授权的依赖变更。
  因此「官方计数器优先」这条在本机**没有真实官方数字可比对**，只有接线与条件断言。
- **只做事前估算、无事后校准**：厂商返回的 usage 没有被回收，也没有与估算值比对过。
  「保守」的依据是「字节数一般 ≥ token 数」这一推理，缺少实测数据支撑。
- 内置表只有三条、只在两家：其余模型一律走运营者声明。
- 工具**输出**在写回 `state.tool_results` 后的下一跳才被计入——同一轮内工具刚返回、
  尚未发出请求的那一跳不受影响，这符合预期（那一跳的 payload 本来就还没包含它）。

---

## 5.1 生成侧与 Embedding 侧是两张表（决定：不并入）

卡片 deliverable 提到「生成/Embedding 能力」，本卡**只**落了生成侧。Embedding 侧沿用
RAG-021 已批准、已版本化的策略（`app/rag/embeddings/input_limits.py`，同样带
`counter` / `counting_method` / `safety_margin` / `source` / `version`）。

不并入的理由：

1. 两侧都已版本化，重复建表会造出第二个事实源；
2. `app/rag/` 此刻正被同批次另一张卡改动，合并会引入冲突；
3. 两侧的失败语义不同（生成侧是「拒绝发出请求」，Embedding 侧是「摄取前截断/拒绝」），
   合成一张表会把两种语义压成一个开关。

后续若要统一，应**单独开卡**：先定统一身份与失败语义，再做迁移，不在本卡内顺手合并。

---

## 6. 文件清单

| 文件 | 动作 |
|---|---|
| `app/llm/counters.py` | 新增：字节估算 + 官方计数器惰性加载 |
| `app/llm/capabilities.py` | 新增：版本化能力契约与解析 |
| `app/llm/budget.py` | 新增：预算核对与 `ContextBudgetExceeded` |
| `app/llm/openai_compatible.py` | 改：`chat` / `stream_chat` 发请求前 Guard |
| `app/llm/factory.py` | 改：建链时对未批准能力打 `llm_capability_unapproved` |
| `app/llm/routing.py` | 改：注释说明预算错误不是 failover |
| `app/agents/pipeline.py` | 改：`_failure_text` 转固定提示 |
| `app/services/chat_service.py` | 改：同步 / 流式两处捕获转固定提示 |
| `tests/test_rag_028_model_capability.py` | 新增 |
| `.env.example` / `README.md` | 补配置项与说明 |

`app/core/config.py` 的五个配置项由 team-lead 一并加好，本卡未改动。

---

## 7. 验证

```
DATABASE_URL="sqlite:///.../data/tmp-rag028-N.db" \
  python -m pytest tests/test_rag_028_model_capability.py tests/test_llm_route.py -q \
  --basetemp=data/pytest-tmp/rag028-N
```

- RAG-028 用例 **29 条** + `test_llm_route.py` 16 条全通过；
- `tests/ -k "llm or agent or chat"` 97 passed, 2 skipped
  （`tests/test_p0_regression.py` 因同批次另一张卡正在改 `app/rag/vectorstore/milvus.py`
  而收集失败，与本卡无关，本卡未触碰 `app/rag/`）；
- 变异验证 6 处，全部变红后还原：

| # | 变异 | 变红的测试 | 条数 |
|---|---|---|---|
| M1 | 去掉 `chat()` 里的 Guard | 未知模型拦截、声明无来源、窗口超限、输出预留超限、工具计入、critique 计入、兜底链不换家 | 7 |
| M2 | 运营者声明不要来源也放行 | `test_declaration_without_source_is_still_blocked` | 1 |
| M3 | 只算内容、不算消息框架开销 | `test_message_frame_overhead_is_counted`、`test_message_frame_overhead_can_block_a_request` | 2 |
| M4 | `stream_chat` 不做 Guard | `test_stream_path_is_guarded` | 1 |
| M5 | 去掉建链时的未批准告警 | `test_factory_warns_when_capability_is_unapproved` | 1 |
| M6 | 把 intent 的声明窗口改成 128000 | `test_each_profile_is_judged_by_its_own_window` | 1 |

### 验收点对应方式

| acceptance | 证据 |
|---|---|
| 模型切换按对应窗口 | `test_each_profile_is_judged_by_its_own_window`：走真实 `get_llm_provider("chat"/"intent")`，同 payload 一个放行一个被拦；M6 变红 |
| 未知模型无批准配置拒绝 | `test_unknown_model_is_blocked_without_sending_request`（0 次 HTTP）；建链告警 `test_factory_warns_when_capability_is_unapproved` |
| payload + 输出预留 + 余量不超已验证限制 | `test_payload_plus_reserve_plus_margin_over_window_is_blocked` 与 `test_payload_exactly_at_budget_is_allowed`（边界两侧） |
| 包含工具与 critique | `test_tool_result_blocks_the_next_round_end_to_end`（真实工具循环，工具返回 30000 字符把下一跳顶出窗口）+ 消息级的 `test_tool_results_are_counted` / `test_critique_round_is_counted` |
