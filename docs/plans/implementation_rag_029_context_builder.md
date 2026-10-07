# RAG-029 按块上下文组装与真实来源 — 实施说明

## 1. 问题复现（改前实测输出）

复现脚本：`data/pytest-tmp/rag029_repro.py`（保留在工作区，未纳入提交）。
解释器 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`（3.12.0），设置
`RAG_CONTEXT_CHARS=400` / `RAG_MEMORY_CONTEXT_CHARS=20`。

| 场景 | 改前实测输出 | 期望 |
|---|---|---|
| A 长 RAG 硬切 | `merge` 后长度 401，`含 [/UNTRUSTED_SOURCE] = False`，尾部 `'AAA…A…'` | 围栏闭合 |
| B 块内代码围栏 | 尾部 `` ```python\nprint('hello world, this is a long code b… ``，`代码块被切成一半 = True` | 不成半截 |
| C last_hits vs payload | `last_hits=3`，payload 里只有 `[资料 1]`；对外 sources 会多出 **2** 个来源 | 差 0 |
| D 记忆裁剪 | `'## 会话记忆\n用户问过退款时限。\n\n用户问过发票抬头。…'` —— 保留**最旧**且留下半条 | 保留最新、无半条 |

对应的代码事实：`app/rag/context_merge.py:15` 的 `body[:limit].rstrip() + "…"`
作用在「已经拼好的整段字符串」上，切完之后无从知道哪些块被丢掉；
`app/services/chat_service.py:518` 的 `_reply_sources` 读 `last_hits`
（阈值过滤后的**全部**命中），因此对外来源 ≠ 实际进模型的证据。

## 2. 设计取舍

| 决策 | 选择 | 理由 / 依据 |
|---|---|---|
| 预算作用点 | **按块**累加，装不下就停止 | 字符硬切会破坏围栏且丢失「谁被切掉」的信息 |
| 装不下时的行为 | **停止**，其后全部 dropped，**不补位** | 跳过小块去装后面的 = 自动补位改变排名，与 RAG-037 的 non_goal 冲突 |
| 首块就装不下 | 按预算截断 + 补齐代码围栏与不可信围栏闭合标记 | ADR-0005 §9「不截坏围栏」；整块丢弃会丢掉唯一证据 |
| 围栏闭合 | 硬要求：含 `[UNTRUSTED_SOURCE]` 必以 `[/UNTRUSTED_SOURCE]` 结尾 | acceptance「围栏闭合」 |
| 记忆裁剪 | 按条目从旧到新丢弃，保留最新；单条超预算则整条丢弃 | ADR-0005 §9「无关/较旧历史先缩减」；半条记忆不可用 |
| `selected` 语义 | **仅表示实际选入**，不宣称答案引用正确 | non_goal「不把 selected 宣称逐答案点 cited」 |
| `last_hits` | 语义**不变** | 4 个既有测试断言它，改坏会连带破坏 RAG-037 / 阈值 / 日志最小化 |
| `last_selected` 初值 | `None`（未组装过）而非 `[]` | 短路径不跑 `AgentPipeline`，若默认 `[]` 会把快路径的来源清空造成回归 |
| 新增配置 | 无，复用 `RAG_MEMORY_CONTEXT_CHARS` / `RAG_CONTEXT_CHARS` | 能复用就不新增 |

### 2.1 编号与 selected 的一致性

`[资料 N]` 的 N 是命中在原始列表中的位置（1 起）。选块是**前缀**选择（装不下就停），
所以文本里出现的编号恰好是 `1..len(selected)`，`selected[k-1]` 对应 `[资料 k]`。
测试 `test_marker_count_equals_selected_count_across_budgets` 在 5 个预算值下断言这一点。

### 2.2 降级路径

自定义 Retriever 协议实现只返回字符串时拿不到 `ChunkResult`，此时
`selected` 恒为空（不声明来源），但围栏闭合要求不变：
`_trim_rag_text` 先给 `"…\n[/UNTRUSTED_SOURCE]"` 留位置再截断；
空间不足以留下开标记时**整段丢弃**，不产出没有开标记的孤立闭合标记。

## 3. 改动文件

| 文件 | 改动 |
|---|---|
| `app/rag/context_builder.py` | **新增**。纯函数：按块预算组装、`ContextPayload`、围栏/代码围栏补齐、记忆按条裁剪 |
| `app/rag/retriever.py` | 前导说明与块渲染统一走 `context_builder`（单一来源）；新增 `last_selected`，`_settle` 每次检索重置为 `None` |
| `app/rag/context_merge.py` | `merge_memory_and_rag` 改为委托 `build_from_rag_text`，不再按字符硬切 |
| `app/agents/pipeline.py` | `_fill_retrieval` → `_assemble_context`：命中可得走 `build_context` 并回写 `last_selected`；否则降级但保证围栏闭合 |
| `app/services/chat_service.py` | `_reply_sources` 优先 `last_selected`，为 `None` 才回落 `last_hits` |
| `tests/test_rag_029_context_builder.py` | **新增**，21 条 |
| `tests/test_context_merge.py` | 新增 `test_merge_never_leaves_unclosed_fence`（改前必然失败的行为） |

未改动 `app/core/config.py` / `.env.example` / `README.md`：复用既有预算，不需要新开关。

## 4. 验收证据（行为断言，非通过数）

`tests/test_rag_029_context_builder.py` 21 条，覆盖：

1. `test_long_rag_truncated_but_fence_still_closed` — 400 字符预算下 `text.rstrip().endswith("[/UNTRUSTED_SOURCE]")`，`len(text) <= 400`，`selected == ["c1"]`
2. `test_first_block_truncation_keeps_code_fence_balanced` — 首块截断后 `text.count("```") % 2 == 0`，`drop_reasons["c1"] == "budget_truncated"`
3. `test_oversized_code_block_is_dropped_whole_when_not_first` — 非首块装不下则整块丢弃
4. `test_safe_truncate_never_leaves_odd_fences` — 对 `limit` 从 1 扫到全文长度，逐点断言围栏成对
5. `test_selected_matches_rendered_markers_one_to_one` / `test_marker_count_equals_selected_count_across_budgets`
6. `test_dropped_chunk_absent_from_selected` — 反补位：`[c1]` 入选，`c2/c3` dropped 且 `"更小的一块" not in text`
7. `test_reply_sources_prefers_last_selected_over_last_hits` / `_when_nothing_selected` / `_falls_back_to_last_hits_when_not_assembled`
8. 记忆三条：`test_memory_trim_keeps_newest_entries_and_never_half_entry`（`"退款时限" not in text`、`"…" not in body`、`body.endswith("。")`）、单条超预算整条丢弃、预算内不裁剪
9. `test_memory_plus_long_rag_stays_within_budgets` — 记忆段 ≤ 200、总长 ≤ `len(head)+200+2+400`
10. 降级路径三条：`test_degraded_string_path_keeps_fence_closed`、`test_merge_memory_and_rag_keeps_fence_closed`、`test_degraded_path_drops_rag_rather_than_stray_close_tag`
11. 端到端三条：`test_pipeline_last_selected_matches_final_context`、`test_pipeline_degraded_string_retriever_keeps_fence`、`test_pipeline_reply_sources_excludes_budget_dropped`

端到端走真实 `HybridRetriever(_FakeBackend) + AgentPipeline._fill_retrieval`，
断言 `state.context` 里的 `[资料 N]` 与 `retriever.last_selected` 严格一致，
且 `ChatService._reply_sources` 返回的 `chunk_id` 序列等于 `last_selected`、少于 `last_hits`。

## 5. 变异验证（6 项，逐个注入 → 必须变红 → 按 HEAD 原样还原）

脚本：`data/pytest-tmp/rag029_mutate.py`。**先跑无变异基线**（21 passed）确认红色不是环境故障，
再逐项注入；每个变异跑完立即还原，脚本 `finally` 中再按启动时的备份全量还原一次
（曾因测试超时被 SIGTERM 中断导致残留，故加此保险）。

| # | 变异 | 文件:位置 | 结果 |
|---|---|---|---|
| a | 去掉围栏补齐：`return head + "\n" + FENCE_CLOSE` → `return head` | `app/rag/context_builder.py` `_trim_rag_text` | **红** 3 failed, 18 passed |
| b | `selected` 直接返回全部命中：`selected=selected` → `selected=list(hits or [])` | `app/rag/context_builder.py` `build_context` | **红** 8 failed, 13 passed |
| c | 记忆裁剪改尾部硬切：插入 `return joined[:limit].rstrip() + "…"` | `app/rag/context_builder.py` `trim_memory_entries` | **红** 2 failed, 19 passed |
| d | 忽略预算：`rag_limit = 10 ** 9` | `app/rag/context_builder.py`（2 处） | **红** 12 failed, 9 passed |
| e | 选块改成「跳过继续装后面的」：`for rest in hits[idx-1:]: ... break` → `continue` | `app/rag/context_builder.py` `_select_rag_blocks` | **红** 2 failed, 19 passed |
| f | `_reply_sources` 优先 `last_hits`：`isinstance(selected, list)` → `... and False` | `app/services/chat_service.py` | **红** 3 failed, 18 passed |

存活变异数：**0**。还原后 `git diff --exit-code HEAD -- app/services/chat_service.py` 通过，
并对 `context_builder.py` 逐串扫描确认无变异残留（`10 ** 9` / `list(hits or [])` / `and False` 均无命中）。

> 过程记录：第一次跑变异脚本时子进程 env 只传了 `PATH`，pytest 直接
> `OSError: [WinError 10106]`，6 个「红」全部是假红。改为继承 `os.environ`
> 并加基线校验后重跑，结论才成立。

## 6. 回归与静态检查

| 项 | 命令 | 结果 |
|---|---|---|
| 专项 + 归口测试 | `pytest tests/test_rag_029_context_builder.py tests/test_context_merge.py -q` | `25 passed` |
| 关联回归（10 个文件） | `pytest tests/test_context_merge.py test_rag_threshold.py test_rag_037_retrieval_status.py test_rag_langchain_score_contract.py test_rag_log_minimization.py test_rag_sources.py test_retrieval_guard.py test_fast_route.py test_agent.py test_agent_chain.py -q` | `135 passed` |
| 全量回归 | `pytest -q`（`DATABASE_URL=sqlite:////.../rag029-full-002.db`） | `1 failed, 1037 passed, 3 skipped` |
| ruff | `python -m ruff check app/` | 我改动的 5 个文件 `All checks passed`；`app/` 全量剩 3 条告警，均出自并行卡 RAG-038 的 `backend/native.py`、`embeddings/openai_compatible.py`、`resilience.py`，非本卡引入 |
| mypy | `python -m mypy app/rag/` | `Success: no issues found in 71 source files`；`mypy app/agents/pipeline.py` 亦通过 |

全量回归唯一的失败 `tests/test_supervisor.py::test_supervisor_error_skips_subtask_and_hides_exception`
与本卡无关：在 `git worktree add --detach HEAD`（3aaa310）里单独跑同一测试同样失败
（`1 failed, 15 passed`），属既有缺陷。本卡未触碰 `app/agents/supervisor.py`。

## 7. 已知边界

- `selected` **不等于**「答案引用了它」。逐答案点的引用核验属于 RAG-030，本卡不做。
- 被截断的首块仍在 `selected` 里（它的前半段确实进了模型），`drop_reasons[id] = "budget_truncated"`
  标记其内容不完整。这是有意的：若把它移出 `selected`，`selected` 与文本里的 `[资料 N]` 就不再一一对应。
- 记忆条目按 `\n{2,}` 切分并归一化为一个空行分隔。若压缩器输出不含空行，整段视为**一条**记忆，
  超预算则整段丢弃 —— 不会出现半条，但会一次丢较多。
- 降级路径（`build_from_rag_text`）拿不到结构化命中，`selected` 恒为空，
  此时 `_reply_sources` 回落 `last_hits`（因为 `last_selected` 为 `None`）。
  这意味着**只有字符串**的自定义 Retriever 仍存在「来源 ≠ 证据」的旧缺口；
  接 `HybridRetriever` 的真实链路不存在。
- `drop_reasons` 以分块 id 为键；同一批命中出现重复 id 时后者覆盖前者（实际检索不会产生）。
- 预算只约束记忆段与 RAG 段各自的长度，不含后续追加的 `state.retrieval_notice`
  与工具结果——它们由 `app/llm/budget` 的整体预算另行约束。
