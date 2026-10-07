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
（`bind_deadline` / `current_deadline`）：组合点（后端 `retrieve`）绑定一次，
子调用读取；语义与 Go 的 `context` 传播一致，且 `bind_deadline` 退出即复位，
不跨检索泄漏。

两条**粒度**规则（复审后补，第一版在这里写错了，见 §8 High-01）：

- **一次调用 = 一次远程往返**（Milvus 检索）：未绑定时用
  `current_or_new_deadline()` 按 `RAG_RETRIEVAL_DEADLINE_SECONDS` 新起一个即可，
  上界覆盖的就是这一次调用。
- **一次调用 = N 次远程往返**（`embed()` 的 N 批）：**不能**用它。未绑定时每批
  各拿一个 `RAG_EMBED_BATCH_DEADLINE_SECONDS`，否则 N 批共用一个 8s 检索预算，
  正常导入会被中途打断。

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
`give_up_budget`）。每次成功记一条 `rag_call_succeeded`
（`attempt / elapsed_ms / backend / tenant / outcome=ok`）——**含首次成功**，
否则依赖方抖动期不可观测：看不到「重试了几次才成功」，也分不清「一直很稳」和
「抖完自己好了」。预算耗尽（还没开始调用就没时间了）单独记
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
| `RAG_EMBED_BATCH_DEADLINE_SECONDS` | `20.0` | 复审后新增。嵌入**单批**的写入侧预算，未绑定检索 deadline 时每批各拿一个。远大于单批实测耗时（0.3~1s 量级）但不等于不限时：单批真挂住时仍能自救。`0` = 不限时 |
| `EMBEDDING_TIMEOUT_SECONDS` | `15.0` | 新增。纠正「嵌入复用 `LLM_TIMEOUT=60`」的语义错位：嵌入是短请求，不该按长输出生成留 60s；且它必须能被 deadline 夹住（实际生效值是 `min(15.0, 剩余预算)`）。写入 / 导入 / 重建索引路径同样使用这个超时 |

**约束（启动校验）**：`RAG_RETRIEVAL_DEADLINE_SECONDS` 必须**严格大于**
`min(BACKOFF_BASE, BACKOFF_MAX) × (1 + RETRY_BACKOFF_JITTER_RATIO)`，否则第一次
失败后剩余预算就不够等退避，一次重试都不会发生。判据实现在
`app.core.config.retrieval_retry_effectively_disabled`，`Settings` 装载时打一条
`rag_retry_effectively_disabled` 告警说明「重试实际已禁用」。默认 8.0 / 0.2 / 1.5
满足该约束（`min(0.2,1.5)×1.2 = 0.24 < 8.0`）。

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

反向地，如果某条链路**不要**被外层 deadline 约束（例如后台导入的批量嵌入），
不绑定即可：嵌入会走「每批各拿一个 `RAG_EMBED_BATCH_DEADLINE_SECONDS`」分支
（见 §8 High-01），Milvus 检索会按 `RAG_RETRIEVAL_DEADLINE_SECONDS` 自管。

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
6. **「总时限」的粒度是「一次检索」**：一次用户请求触发 N 次 `retrieve` 时总耗时
   按 `N × deadline` 累加（详见 2.2 与 README）。当前三条线上路径每请求只调一次
   检索，因此现网不受影响；若将来出现「一请求多检索」，需由调用方显式绑定。
7. **SSE 终态事件不带 `reason` 字段**：审查提出的 Low 项涉及
   `app/services/chat_service.py` / `app/agents/*` 的终态事件结构，不在本卡文件
   边界内（本卡只允许改 `app/rag/**`、`app/core/config.py`、`.env.example`、
   `README.md`、`tests/`、`docs/`），已回报 team-lead 待指派。

---

## 8. 复审整改（第二轮，基于 `docs/reviews/2026-10-07-RAG-038检索调用时限重试取消独立审查.md`）

### High-01　写入 / 导入路径拿到 8s 检索总预算（P0，本卡自己引入的回归）

**机制**：`openai_compatible.embed()` 在批次循环**之前**调用
`current_or_new_deadline()`，只创建一次 `Deadline`。写入路径不绑定，于是拿到
`RAG_RETRIEVAL_DEADLINE_SECONDS=8.0`，且这 8s 覆盖**整通调用的所有批次**。

**我在磁盘上亲手复现的改前 / 改后对照**（脚本
`data/pytest-tmp/rag038_h1_repro.py`，每批真实 1.0s、全部成功、无故障注入）：

```
# e978819^（本卡之前，无 deadline 机制）
RAG_RETRIEVAL_DEADLINE_SECONDS = <未定义：改前无此项>
[60 块 / 6 批 × 1.0s] 耗时 6.06s 请求数=6 向量数=60 → ok
[100 块 / 10 批 × 1.0s] 耗时 10.11s 请求数=10 向量数=100 → ok

# e978819（本卡提交后，回归）
RAG_RETRIEVAL_DEADLINE_SECONDS = 8.0
[60 块 / 6 批 × 1.0s] 耗时 6.06s 请求数=6 向量数=60 → ok
[100 块 / 10 批 × 1.0s] 耗时 8.08s 请求数=8 向量数=0
      → DeadlineExceededError: 检索总时限 8.000s 已耗尽，不再发起后续子调用
      → WARNING app.rag.resilience rag_deadline_exhausted elapsed_ms=8078 backend=embedding
```

**修法**（采纳审查者方案 A）：

1. 新增 `RAG_EMBED_BATCH_DEADLINE_SECONDS`（默认 `20.0`，写入侧**单批**预算）。
2. `embed()` 改为：循环外只读一次 `shared = current_deadline()`；循环内
   `deadline = shared or Deadline(settings.RAG_EMBED_BATCH_DEADLINE_SECONDS)`。
   已绑定（检索）→ 各批共享总预算，语义不变；未绑定（写入）→ 每批各拿一个。
3. `app/rag/vectorstore/milvus.py` 的 `current_or_new_deadline()` 我逐个核对过调用
   方：`hybrid_search` 只在**读**路径上（`add` / `delete_by_document` / `count`
   都不含 deadline），一次调用 = 一次远程往返，因此保持原样。
4. `resilience.current_or_new_deadline()` 的 docstring 已改写，明确「上界覆盖的是
   这一次调用本身」，并点名 `embed()` 这类多批入口不得直接用它。

**修后复现**（同一脚本、同一参数）：

```
[100 块 / 10 批 × 1.0s] 耗时 10.08s 请求数=10 向量数=100 → ok
```

### Medium-01　总时限偏小时「有限退避重试」永不生效

**机制**：`retry_async` 在退避前比较 `wait` 与 `deadline.remaining()`，预算不够就
放弃。当 `deadline` 小到放不下**第一次**退避时，每一次可重试故障都直接走
`give_up_budget`，一次都不重试——配置看起来启用着重试，实际等价于
`MAX_ATTEMPTS=1`。

**修法**：

1. `app.core.config.retrieval_retry_effectively_disabled()`：单一判据实现
   ——第一次退避等待的上界 `min(base, max) × (1 + jitter)` 必须**严格小于**总时限；
   总开关关闭 / `MAX_ATTEMPTS<=1` 同样算「重试实际已禁用」。
2. `Settings` 加 `model_validator(mode="after")`，装载时（即启动）打一条
   `rag_retry_effectively_disabled` 告警，写明 `reason` 与处置提示。
3. 抖动比例收敛为单一来源 `RETRY_BACKOFF_JITTER_RATIO`（`config.py`），
   `RetryPolicy.jitter` 默认值与启动校验都取自它，避免两处常量漂移。
4. `resilience.py` 模块 docstring 与 README 都写清这条约束。

### Medium-01'（审查者编号）　取消未穿过真实 `NativeRagBackend.retrieve`

审查者的变异 M14（`native.py` 的 `except TimeoutError` 扩成
`except (TimeoutError, asyncio.CancelledError)`）曾存活，说明最吃重的那处没有
守护。lead 已裁定**代码行为成立、不改**，但覆盖缺口是真缺口，因此补了
`test_native_backend_cancellation_propagates_through_real_retrieve`：嵌入替身直接
`raise asyncio.CancelledError()`，经真实 `NativeRagBackend.retrieve` 后断言
`CancelledError` 上抛且 `store.calls == 0`。M14 现已变红。

### Medium-02　「总时限」粒度

lead 裁定 ① 成立（ContextVar + asyncio 任务 context 的语义），不改代码。按审查者
方案二落地文档：粒度结论写进 `resilience.py` 模块 docstring、README 与本文档 §2.2 / §7。

### Low / Info

| 项 | 处置 |
|----|------|
| Low-01 多批次嵌入无测试守护 | 补 `test_embedding_read_path_batches_share_one_deadline`（绑定 1.0s、4 批，断言读超时被剩余预算逐批压缩且单调不增） |
| Low-02 `EMBEDDING_TIMEOUT_SECONDS` 缺写入路径影响说明 | `.env.example` 注释补「写入 / 导入 / 重建索引 / 语义切分同样使用这个超时」 |
| Low-03 Milvus 复用共享 deadline 无独立断言 | 补 `test_milvus_reuses_bound_deadline_not_settings`：配置给 3.0s、绑定给 0.2s，实测耗时必须 ≈0.2s |
| Low 抖动期不可观测 / 首次成功无 `attempt=1` | 新增 `rag_call_succeeded` 成功事件（含 `attempt=1`），补 `test_success_event_records_attempt` 与 `test_embedding_success_logs_attempt_one` |
| Low SSE 终态事件无 `reason` | **不在本卡文件边界**（`app/services/chat_service.py` 等禁改），已回报 team-lead |
| Info 配置默认值重复 | `RetryPolicy` 的默认 `max_attempts / backoff_base / backoff_max / enabled` 改为读 `settings`，`jitter` 读 `RETRY_BACKOFF_JITTER_RATIO`，不再各写一份数字 |
| Info 实现说明表格漏字段 | §2.7 已补 `tenant` / `outcome` 字段说明 |
| Info 部署耦合 | §3 补「写入路径同样吃 `EMBEDDING_TIMEOUT_SECONDS`」与 deadline/退避约束说明 |

---

## 9. 第二轮验证记录（全部在 detached `git worktree` 内完成）

工作树：`.workbuddy/tmp/wt038fix`（`git worktree add --detach <path> e978819`），
主工作树只在提交时刻被改动；对照基线：`.workbuddy/tmp/wt038base`（`e978819^`）。

- 专项测试：`51 passed in 11.16s`（原 42 条全绿 + 新增 9 条）
- 受影响子集回归（审查者那组 + 我这组并集）：
  `260 passed, 2 skipped in 85.51s`
- `ruff check app/ tests/test_rag_038_deadline_retry.py` → `All checks passed!`
- `mypy app/rag/ app/core/config.py` → `Success: no issues found in 72 source files`

### 变异验证（15 个，全部 RED）

脚本 `data/pytest-tmp/rag038_mutate_r2.py`。每个变异都先
`assert mutated != original`（本批已多次踩到 CRLF 导致替换静默失效、却报告存活的坑），
跑完 `finally` 还原后再断言文件内容 == 原文。

| # | 变异 | 结果 | 抓住它的用例 |
|---|------|------|--------------|
| H1 | 整通调用共用一个检索 deadline（High-01 原写法） | **RED** | `test_embedding_write_path_many_batches_are_not_cut_off` |
| a2 | 嵌入侧不复用共享 deadline | **RED** | `test_embedding_reuses_the_bound_deadline` |
| M14 | `except TimeoutError` → `except (TimeoutError, CancelledError)` | **RED** | `test_native_backend_cancellation_propagates_through_real_retrieve` |
| S1 | 成功事件 `attempt` 写死为 1 | **RED** | `test_success_event_records_attempt` |
| S2 | 成功事件降到 `debug`（等于不记） | **RED** | `test_success_event_records_attempt`、`test_embedding_success_logs_attempt_one` |
| S3 | 「重试实际已禁用」判据恒为假 | **RED** | `test_retry_effectively_disabled_predicate`、`test_startup_warns_when_retry_effectively_disabled` |
| a | 每个子调用各起一个 deadline | **RED** | `test_native_backend_shares_one_deadline_between_embed_and_search` |
| b | 4xx 也重试 | **RED** | `test_4xx_is_not_retried[400]` |
| c | 退避不检查剩余预算 | **RED** | `test_backoff_does_not_sleep_past_deadline` |
| d | 重试次数不设上限 | **RED** | `test_429_retries_up_to_configured_attempts` |
| e | 取消被改写成 unavailable | **RED** | `test_cancellation_propagates_from_retry_async` |
| f | 失败后返回空列表 | **RED** | `test_milvus_slow_search_is_bounded_and_never_empty` |
| g | 日志带 api key / 正文 | **RED** | `test_embedding_failure_log_has_no_key_or_body` |
| h | `ensure_budget` 变空操作 | **RED** | `test_shared_deadline_aborts_second_subcall_before_running` |
| i | 单次超时不夹进剩余预算 | **RED** | `test_deadline_helpers` |

H1 的失败输出原文：

```
=== H1 整通调用共用一个检索 deadline（High-01 回归写法） -> RED（被抓住） ===
FAILED tests/test_rag_038_deadline_retry.py::test_embedding_write_path_many_batches_are_not_cut_off
1 failed, 50 passed in 10.85s
```

M14 的失败输出原文：

```
=== M14 native 把取消也改写成 unavailable -> RED（被抓住） ===
FAILED tests/test_rag_038_deadline_retry.py::test_native_backend_cancellation_propagates_through_real_retrieve
1 failed, 50 passed in 12.13s
```

## 10. 提交窗口被他人宽提交卷入的记录（事实，不粉饰）

本卡第二轮整改**没有产生独立 commit**：7 个文件在本人执行 `git add` 之前，
已被他人提交 `52769db`（`style: 归一化 scripts/milvus_switch_check.py 行尾回 LF`）
以宽提交（含 `git add -A` 性质）的方式一并落盘进 HEAD。

证据（都可复现）：

- `git show --stat 52769db` 列出的文件与行数，与本轮应提交的 7 个文件**逐一吻合**
  （`.env.example` 448 / `README.md` 1472 / `app/core/config.py` 728 /
  `app/rag/embeddings/openai_compatible.py` 25 / `app/rag/resilience.py` 92 /
  实现文档 676 / 测试 278）。
- `git show 52769db -- app/core/config.py | grep RAG_EMBED_BATCH_DEADLINE_SECONDS`
  命中 1 次——即 High-01 的修复确实在该提交里。
- 本人随后 `git add <7 文件>` 时，`git diff --cached --stat` 显示这些文件与 HEAD
  **零差异**（`git hash-object <file>` == `git rev-parse HEAD:<file>`），
  说明内容已完整入库、无丢失。

影响与处置：

- 改动内容完整，功能与测试不受影响；
- 但该提交的 message 与内容不符（写的是行尾归一化，实际夹带了一整张卡的整改），
  历史可读性受损。**建议不改写历史**：其上方已叠了 `6aae8c7`（RAG-029）等提交，
  rebase 会动到他人工作。是否补一个说明性提交由 team lead 决定。
- 教训：多人共用同一个主工作树时，本人 `git add <具体文件>` 并不能防御**他人**的
  宽提交；提交窗口应尽可能短。

补验（在卷入之后的最新 HEAD 上重做， detached worktree `.workbuddy/tmp/wt038final`，
基线 `6aae8c7`）：

- 专项：`51 passed in 10.01s`
- 与 RAG-029 交叉的关键子集（`test_rag_038` + `test_rag_029_context_builder` +
  `test_rag` + `test_rag_backend` + `test_rag_vectorization_api` +
  `test_embeddings` + `test_retrieval_guard` + `test_chat`）：
  `158 passed, 1 skipped, 1 warning in 33.65s`
- `ruff check app tests`（基线 `60d4293` + 本卡改动）→ `All checks passed!`
- `mypy app` → `Success: no issues found in 185 source files`
- `tests/test_rag_033_parse_quality.py` 的 2 条失败，在**不含本卡改动**的干净基线
  `60d4293`（worktree `.workbuddy/tmp/wt038base4`）上同样复现，属 RAG-033 自身，
  与本卡无关。
