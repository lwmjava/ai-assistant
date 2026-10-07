# RAG-038 检索调用统一总时限、有限退避重试与取消传播

- 分支：`rag-batch-20261007`
- 改动文件：`app/rag/resilience.py`（新增）、`app/rag/embeddings/openai_compatible.py`、
  `app/rag/embeddings/factory.py`、`app/rag/vectorstore/milvus.py`、
  `app/rag/backend/native.py`、`app/core/config.py`、`.env.example`、`README.md`、
  `tests/test_rag_038_deadline_retry.py`（新增）、本文档
- 未触碰（按分工由其它卡负责）：`app/rag/retriever.py`、`app/agents/pipeline.py`、
  `app/services/chat_service.py` 等

---

## 1. 问题复现（改前实测）

复现脚本：`data/pytest-tmp/rag038_repro.py`（一次性脚本，不属于测试套件）。
用 `httpx.MockTransport` 打桩，直接打真实的
`OpenAICompatibleEmbeddingProvider` 调用链，实测输出：

```
[1] 4 个子调用各 0.3s，单次 timeout=0.4 → 总耗时 1.250s，子调用数 4
[1] 结论：总耗时 1.250s 远超单次 timeout 0.4s，无总 deadline
[2] 429 注入 → 抛 HTTPStatusError，attempt 数 = 1（应为 1，未重试）
[3] 500 注入 → 抛 HTTPStatusError，attempt 数 = 1（应为 1，未重试）
[4] 400 注入 → 抛 HTTPStatusError，attempt 数 = 1
```

三条结论：

1. **没有总 deadline。** 改前唯一的约束是 `httpx.AsyncClient(timeout=...)`，
   即**逐次** timeout。嵌入分 N 批就是 N 个独立预算，加上向量检索，一次检索
   的最坏耗时是 `N × timeout + 向量检索耗时`；`app/core/config.py` 里嵌入还
   复用了 `LLM_TIMEOUT=60.0`（对话生成为长输出留的 60s），语义完全错位。
2. **429 / 5xx 不重试。** 依赖方抖一下整条检索就失败，只能靠上层重试整个对话。
3. **慢查询无上界。** `app/rag/vectorstore/milvus.py` 的 pymilvus 调用是同步的，
   连 timeout 都没有包裹，更没有取消语义的约定。

整仓 `app/rag/` 下检索路径对 `retry|backoff|deadline` 零覆盖（只在导入任务 /
OCR 里有零星实现）。

---

## 2. 设计取舍

### 2.1 为什么是「总 deadline」而不是「逐次 timeout」

逐次 timeout 的上界是 `Σ 每次的 timeout`，子调用一多就失控；而且它无法表达
「嵌入已经花了 7s，向量检索只剩 1s」这种真实约束。总 deadline 的做法是：
一次检索在入口（`NativeRagBackend.retrieve`）建**一个** `Deadline`，后续所有
子调用按 `deadline.subcall_timeout(自己的配置)` 夹取单次超时，并在开始新子调用
前 `ensure_budget` 检查预算。这样：

- 上界就是 `deadline`，与子调用个数无关；
- 慢的那一环会**压缩**后面所有环节，而不是拖长整条链路；
- 预算用完后不再发起新调用（这是「有界」的另一半，光有子调用超时不够）。

### 2.2 deadline 怎么传到子调用

`EmbeddingProvider.embed` 与 `VectorStore.hybrid_search` 是两个被多张卡共用的
协议方法，给它们加形参会牵动一堆调用方。因此用 `ContextVar` 传播
（`bind_deadline` / `current_deadline` / `current_or_new_deadline`）：
组合点（后端 `retrieve`）绑定一次，子调用读取；没被绑定时（例如导入任务单独
算一次嵌入）按配置自己新起一个，不会退化成「无人管超时」。
语义与 Go 的 `context` 传播一致，且 `bind_deadline` 退出即复位，不跨检索泄漏。

### 2.3 为什么只对部分故障重试

- 连接错误 / 读超时 / `429` / `5xx`：依赖方抖动，换一次调用可能就好 → 重试。
- `4xx`（除 429）/ 业务错误（维度不符、输入超限）：请求本身有问题，重试只会
  再错一次，还放大服务端压力 → attempt 固定为 1。
- 退避用 `base * 2**(n-1)` 夹到 `max` 再叠 ±20% 抖动（防惊群）。

### 2.4 耗时有界的关键：退避前检查预算

光有「次数上界」不够。`base=1.5 / deadline=1.0` 时，第一次退避就要睡 1.5s，
睡完 deadline 早过了。所以**退避前**比较 `wait` 与 `deadline.remaining()`，
不够就放弃等待直接抛。这一条是「耗时有界」真正成立的支点，专项测试
`test_backoff_does_not_sleep_past_deadline` 与 `test_total_elapsed_is_bounded_across_retries`
专门盯它，变异 (c) 也针对它。

### 2.5 pymilvus 是同步调用：取消无法真正中断

`MilvusVectorStore._remote_search`（连集合 + 检索）是同步的，用
`asyncio.to_thread` + `asyncio.wait_for(deadline.remaining())` 限时。边界必须写明：

- `wait_for` 超时只能让**协程**放弃等待，**无法真正中断**线程里的底层调用，
  线程会继续跑到底才释放工作线程；
- 因此「有界」是「调用方不再等」，不是「服务端调用被取消」；
- 被放弃的调用**不会污染结果**（返回值被丢弃），也**不会**被写成空结果：
  超时走 `DeadlineExceededError` → `MilvusUnavailableError` 向上抛；
- 依赖方持续无响应时线程池 worker 会被占住，运维侧仍需 Milvus 自己的
  连接/查询超时兜底。

Milvus 只加 deadline、不加重试：向量检索重试收益不确定，重复查询反而放大
服务端压力（non_goals 也不允许引入队列依赖）。

### 2.6 取消语义与「不伪造空知识」

- `asyncio.CancelledError` 在 Python 3.8+ 继承 `BaseException`。本模块所有
  `except` 只捕获 `Exception`，并**显式**写 `except asyncio.CancelledError: raise`
  再重抛，绝不把取消改写成「检索不可用」。
- 调用方若把 `except Exception` 放宽成 `except BaseException`（本仓
  `app/rag/import_scheduler.py` 就是这种写法），取消会被静默吞掉。专项测试
  `test_cancelled_error_is_not_converged_to_unavailable` 用真实的
  `HybridRetriever` 断言：注入取消后必须向上传播，且 `last_status` 不能被写成
  `unavailable`。
- 取消也不该记成一次「调用失败」（否则监控把用户主动取消统计成依赖方故障），
  `test_cancellation_from_blocking_call_propagates` 断言取消后没有
  `rag_call_failed` 记录。
- 重试耗尽 / 超时一律向上抛，**绝不返回空列表**。空列表会被上层理解为
  「查过了、没有」（`no_hit`），把故障伪装成知识缺失。收敛为 `unavailable`
  由 RAG-037 的检索状态契约负责。

### 2.7 日志

每次失败 attempt 记一条 `rag_call_failed`：
`attempt / elapsed_ms / error_type / backend / tenant / outcome`
（`outcome` ∈ `retry` | `give_up_not_retryable` | `give_up_attempts_exhausted` |
`give_up_budget`）。预算耗尽（还没开始调用就没时间了）单独记
`rag_deadline_exhausted`。**严禁**写入 Authorization、api key、请求正文或命中
正文——日志一旦落盘就是第二份密钥库，专项测试对「密钥与正文都不出现」有断言。

---

## 3. 配置项与取值理由

| 配置项 | 默认值 | 取值理由 |
|--------|--------|----------|
| `RAG_RETRIEVAL_RESILIENCE_ENABLED` | `true` | 总开关。关闭后 attempt 固定为 1、不记退避等待，行为退回 RAG-038 之前，便于休眠/无网环境排障 |
| `RAG_RETRIEVAL_DEADLINE_SECONDS` | `8.0` | 一次检索的总预算。嵌入 P95 通常在 1s 内、本地向量检索在 0.1s 量级，8s 给 3 次 attempt 与退避留足空间；同时远低于常见网关 30s/60s 超时，保证「检索拖不死整个对话请求」。`0` 或负数 = 关闭时限与重试 |
| `RAG_RETRIEVAL_MAX_ATTEMPTS` | `3` | **含首次**（最多 2 次重试）。>3 的收益递减且放大尾部延迟；3 次 + 0.2/1.5 退避的最坏退避等待为 0.6s，远小于 8s 总时限 |
| `RAG_RETRIEVAL_BACKOFF_BASE_SECONDS` | `0.2` | 太小对短暂抖动无效，太长浪费总预算；第 1 次退避 0.2s 足以覆盖毫秒级的瞬时故障 |
| `RAG_RETRIEVAL_BACKOFF_MAX_SECONDS` | `1.5` | 单次等待不超过总时限的 20%，避免一次退避吃掉大半个预算；剩余预算不足时直接放弃等待 |
| `EMBEDDING_TIMEOUT_SECONDS` | `15.0` | 新增。纠正「嵌入复用 `LLM_TIMEOUT=60`」的语义错位：嵌入是短请求，不该按长输出生成留 60s；且它必须能被总时限夹住（实际生效值是 `min(15.0, 剩余预算)`） |

三处同步：`app/core/config.py`（字段与注释）、`.env.example`（带注释）、
`README.md`（关键配置项表 + 独立章节）。

---

## 4. 验收证据（实测断言，不是测试条数）

专项测试 `tests/test_rag_038_deadline_retry.py`，**42 passed**：

| 验收项 | 用例 | 实测断言 |
|--------|------|----------|
| 429 重试到配置上限 | `test_429_retries_up_to_configured_attempts`、`test_embedding_429_retries_to_configured_limit` | attempt 数 == `RAG_RETRIEVAL_MAX_ATTEMPTS`；退避序列 `[0.2, 0.4]`；真实链路请求数 == 3 |
| 5xx 重试耗尽后收敛 | `test_embedding_5xx_exhausts_then_raises`、`test_retry_never_returns_empty_fallback` | 抛异常，不返回空值 / 空列表 |
| 嵌入复用共享 deadline | `test_embedding_reuses_the_bound_deadline` | 绑定 0.5s deadline 后单次读超时 ≤ 0.5s（不是 8s / 15s） |
| 4xx 不重试 | `test_4xx_is_not_retried[400/401/403/404/422]`、`test_embedding_4xx_is_not_retried` | attempt == 1，退避记录为空 |
| 慢查询超时终止 | `test_slow_query_is_terminated_at_deadline`、`test_embedding_slow_query_aborts_at_deadline`、`test_run_blocking_never_returns_empty_on_timeout` | 实测等待 ≤ deadline + 0.35s；单次读超时被夹到 ≤ 0.3s（不是 15s） |
| 取消传播 | `test_cancellation_propagates_from_retry_async`、`test_cancellation_during_backoff_propagates`、`test_cancellation_from_blocking_call_propagates`、`test_milvus_cancellation_is_not_rewritten_as_empty`、`test_cancelled_error_is_not_converged_to_unavailable` | `CancelledError` 原样上抛；`last_status != UNAVAILABLE`；取消不记 `rag_call_failed` |
| 关闭时限 → 退回普通调用 | `test_deadline_disabled_falls_back_to_plain_call`、`test_switch_off_disables_retry` | `DEADLINE=0` 时不限时也能正常返回；总开关关闭时 attempt == 1 |
| 耗时有界（共享 deadline） | `test_shared_deadline_aborts_second_subcall_before_running`、`test_native_backend_shares_one_deadline_between_embed_and_search`、`test_native_backend_total_elapsed_is_bounded` | 嵌入吃光预算后 `store.calls == 0`；真实时钟下总耗时 ≤ 0.4s + 容差 |
| 退避不睡过 deadline | `test_backoff_does_not_sleep_past_deadline`、`test_total_elapsed_is_bounded_across_retries` | attempt == 1、等待记录为空、实测耗时 ≤ 上界 |
| 日志 | `test_failed_attempt_logs_once_per_attempt_without_secrets`、`test_embedding_failure_log_has_no_key_or_body`、`test_deadline_exhausted_logs_distinct_event` | `rag_call_failed` 条数 == attempt 数；日志中不含密钥 / 正文 / Authorization；预算耗尽记独立事件 |
| 不伪造空知识 | `test_external_failure_converges_to_unavailable_not_no_hit`、`test_unavailable_not_no_hit_through_native_and_retriever`、`test_milvus_slow_search_is_bounded_and_never_empty` | 故障 → `unavailable` + `error_type=RuntimeError`；无故障空结果 → `no_hit` 且 `error_type == ""` |

时间控制一律用「注入 sleep + 可控时钟（`FakeClock`）」，不靠真实睡眠拖慢 CI；
`httpx.MockTransport` 不应用 timeout，因此慢查询用例由 handler 读取 httpx 写入
`request.extensions["timeout"]` 的读超时并模拟真实传输层行为。

---

## 5. 变异验证

脚本：`data/pytest-tmp/rag038_mutate.py`（11 个变异，一次跑完）。每个变异临时改
工作树 → 跑专项测试 → 按原样还原 → 逐项字符串校验零残留。

| # | 变异 | 结果 | 抓住它的用例 |
|---|------|------|--------------|
| a | 每个子调用各起一个 deadline（不共享） | **RED** | `test_native_backend_shares_one_deadline_between_embed_and_search` |
| a2 | 嵌入侧不复用共享 deadline（`current_or_new_deadline` → 新起一个） | **RED** | `test_embedding_reuses_the_bound_deadline` |
| b | 4xx 也重试（`status >= 400` 一律可重试） | **RED** | `test_4xx_is_not_retried[400]` |
| c | 退避不检查剩余预算（可能睡过 deadline） | **RED** | `test_backoff_does_not_sleep_past_deadline` |
| d | 重试次数不设上限（`max_attempts * 10`） | **RED** | `test_429_retries_up_to_configured_attempts` |
| e | 取消被改写成 `DeadlineExceededError`（等同 unavailable） | **RED** | `test_cancellation_propagates_from_retry_async` |
| e2 | 同步调用分支删掉取消重抛 + 改用 `except BaseException`（取消被记成调用失败） | **RED** | `test_cancellation_from_blocking_call_propagates` |
| f | 失败后返回空列表而不是收敛为不可用 | **RED** | `test_milvus_slow_search_is_bounded_and_never_empty` |
| g | 日志里带上 api key / 请求正文 | **RED**（补测后） | `test_embedding_failure_log_has_no_key_or_body` |
| h | `ensure_budget` 变成空操作 | **RED** | `test_shared_deadline_aborts_second_subcall_before_running` |
| i | 单次超时不夹进剩余预算（`subcall_timeout` 直接返回配置值） | **RED** | `test_deadline_helpers` |

**两个变异一度存活，都是真实缺陷，已补测到抓住为止：**

1. **(g) 日志里带 api key**：原断言只扫 `rag_call_failed` 这一种事件，往别处打
   一条带密钥的日志就漏过去了。已改成扫 `caplog.records` **全部**记录——密钥 /
   正文漏到任何一条日志里都是漏。
2. **(a2) 嵌入侧不复用共享 deadline**：原断言只看后端组合点的 `ensure_budget`，
   嵌入自己另起一个 deadline 不影响它。补了 `test_embedding_reuses_the_bound_deadline`：
   绑定 0.5s 的 deadline 后，实测单次请求的读超时必须 ≤ 0.5s（被剩余预算夹住），
   而不是按配置新起的 8s。

另外 (e2) 的第一版构造无效（新加的 `except BaseException` 排在
`except asyncio.CancelledError: raise` 之后，是不可达代码，行为没变所以测试
不会红）。已改成「删掉取消重抛 + 把 `except Exception` 改成 `except BaseException`」
这个真正会改变行为的变异，随即变红。

所有变异均已还原，工作树零残留（13 项字符串校验通过）。

> 过程教训：变异脚本被中断（SIGTERM）会把工作树留在变异状态，之后的运行结果
> 全部不可信（曾据此误判 (f) 锚点未命中）。补跑前先逐项校验文件是否已还原。

---

## 6. 后续接线（retriever 层怎么用）

本轮**没有**改 `app/rag/retriever.py`（总装接线由 team lead 在 RAG-029 落地后
统一接）。`HybridRetriever.retrieve()` 现有的 `except Exception` 已经能正确
收敛：本模块抛出的 `DeadlineExceededError` 与 httpx 异常都继承 `Exception`，
会被收敛为 `unavailable`；而 `CancelledError` 继承 `BaseException`，不会被吞
——这条契约已被 `test_cancelled_error_is_not_converged_to_unavailable` 断言。

**即：只要 `NativeRagBackend.retrieve` 是后端，`HybridRetriever` 无需任何改动
就自动获得总时限与有限重试。**

如果 RAG-029 之后要在 `retriever` 层再加一层**请求级**预算（例如整个对话请求
给检索 5s，而不是每条查询 8s），按下面这样包一层即可，`NativeRagBackend`
会复用外层已绑定的 deadline（`current_deadline() or 新建`），不会各起一个：

```python
from app.rag.resilience import Deadline, bind_deadline

async def retrieve(self, query: str, plan: str) -> str:
    # 请求级总预算：同一请求内的多次检索共用，跨子调用不重复计时
    with bind_deadline(Deadline(settings.RAG_REQUEST_DEADLINE_SECONDS)):
        ...  # 现有逻辑不变，backend.retrieve 会自动复用这个 deadline
```

单条检索要单独限时（不复用外层）时，显式新建并绑定即可覆盖：

```python
with bind_deadline(Deadline(settings.RAG_RETRIEVAL_DEADLINE_SECONDS)):
    raw = await self.backend.retrieve(...)
```

反向地，如果某条链路**不要**被外层 deadline 约束（例如后台导入的批量嵌入，
它有自己的任务级超时），不绑定即可：子调用会走
`current_or_new_deadline()` 的「新起一个」分支，按
`RAG_RETRIEVAL_DEADLINE_SECONDS` 自管。

---

## 7. 已知边界（不假装已解决）

1. **pymilvus 取消无法真正中断底层调用**（见 2.5），ThreadPool worker 可能被
   占住；Milvus 侧的连接/查询超时仍需运维配置。
2. **Milvus 只加 deadline、不加重试**（见 2.5）。
3. **本地向量库（`local`）未单独套 deadline**：它是进程内同步 SQL，整条链路的
   总时限由 `NativeRagBackend.retrieve` 的 `asyncio.wait_for` 兜住。
4. **`ensure_budget` 的阈值是「剩余 ≤ 0」**：剩余 0.05s 时仍会发起子调用，由
   该子调用自己的 `subcall_timeout` 兜底。没有引入「最小可用预算」启发式，
   避免为不存在的场景增加复杂度。
5. **退避抖动用 `random.uniform`**，未做可复现播种；测试把 `jitter` 置 0 或用
   区间断言。
