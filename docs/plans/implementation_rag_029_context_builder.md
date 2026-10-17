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
| 首块就装不下 | **整块丢弃**，不截断（首轮曾授权截断，已被撤销，见 §7） | ADR-0005 §9 批准的是「**完整证据块**」；截一半既违背批准决定，又破坏 `selected` 与 payload 的一致性 |
| 围栏闭合 | 硬要求：含 `[UNTRUSTED_SOURCE]` 必以 `[/UNTRUSTED_SOURCE]` 结尾 | acceptance「围栏闭合」 |
| 代码围栏配对 | 按**行**识别围栏字符与宽度，闭合标记沿用开围栏原文 | 固定补三个反引号会把四反引号围栏误闭合（审查 M-01） |
| 记忆裁剪 | 按条目从旧到新丢弃，保留最新；单条超预算则整条丢弃 | ADR-0005 §9「无关/较旧历史先缩减」；半条记忆不可用 |
| `selected` 语义 | **仅表示实际选入**，不宣称答案引用正确 | non_goal「不把 selected 宣称逐答案点 cited」 |
| `last_hits` | 语义**不变** | 4 个既有测试断言它，改坏会连带破坏 RAG-037 / 阈值 / 日志最小化 |
| `last_selected` 回写 | 三条真实路径都回写；拿不到就写 `[]` | 留 `None` 会让来源回落全量 `last_hits`，重现「来源多于证据」（审查 H-03） |
| `_reply_sources` | **不回落** `last_hits` | 欠声明是安全的，夸大来源才是本卡要修的缺陷 |
| 新增配置 | 无，复用 `RAG_MEMORY_CONTEXT_CHARS` / `RAG_CONTEXT_CHARS` | 能复用就不新增 |

### 2.1 编号与 selected 的一致性（硬不变量）

`[资料 N]` 的 N 是命中在原始列表中的位置（1 起）。选块是**前缀**选择（装不下就停），
所以文本里出现的编号恰好是 `1..len(selected)`，`selected[k-1]` 对应 `[资料 k]`，
**包括「两边都为空」**。测试 `test_marker_count_equals_selected_count_across_budgets`
（6 个预算值，含 `_FENCE_OVERHEAD + 1 = 110`）与
`test_markers_selected_and_sources_agree_even_when_all_empty` 钉住这一点。

### 2.2 唯一组装入口

`app/rag/retriever.py::assemble_retrieval(retriever, snippet)` 是检索结果进模型的
**唯一入口**。首轮只把它接到了 `AgentPipeline`，导致 Fast RAG（知识库问答的
**默认**路径）与 LangGraph Supervisor 完全绕过预算（审查 H-01）。现在三条路径共用它：

| 路径 | 位置 | 组装后写回 |
|---|---|---|
| 自研管线 | `app/agents/pipeline.py::_assemble_context` | `last_selected = payload.selected` |
| Fast RAG 短路径 | `app/agents/fast_path.py:iter_fast_path` | 经 `assemble_retrieval` 写回 |
| LangGraph Supervisor | `app/agents/supervisor.py::_retrieve` | 经 `assemble_retrieval` 写回 |

一块都装不下时 `assemble_retrieval` 记 `rag_context_budget_exhausted`
（含预算值、块数、最大块长度），调用方据此向用户说明「超预算」，
而不是伪装成「没有可用的检索结果」。

### 2.3 降级路径

自定义 Retriever 协议实现只返回字符串时拿不到 `ChunkResult`，此时
`selected` 恒为空（不声明来源），但围栏闭合要求不变。

**「没超预算」不等于「结构有效」**：`_trim_rag_text` 的所有出口都要过一次围栏不变式——
要么原样返回**已闭合**文本，要么补齐闭合标记，要么整段丢弃。输入本身只有
`[UNTRUSTED_SOURCE]` 没有闭合标记时（审查 H-03 实测证据 A）必须补齐，
不得原样进入模型。空间不足以留下开标记时整段丢弃，不产出孤立的闭合标记。

结构化命中的判定放宽到任意 `Sequence[ChunkResult]`（含 `tuple`，审查 H-03 实测证据 B），
但不接受字符串/字节——那是「只有整段文本」的降级信号。

## 3. 改动文件

首轮（`e246fde` + `7668ddd`）：

| 文件 | 改动 |
|---|---|
| `app/rag/context_builder.py` | **新增**。纯函数：按块预算组装、`ContextPayload`、围栏补齐、记忆按条裁剪 |
| `app/rag/retriever.py` | 前导说明与块渲染统一走 `context_builder`（单一来源）；新增 `last_selected`，`_settle` 每次检索重置为 `None` |
| `app/rag/context_merge.py` | `merge_memory_and_rag` 改为委托 `build_from_rag_text`，不再按字符硬切 |
| `app/agents/pipeline.py` | `_fill_retrieval` → `_assemble_context`：命中可得走 `build_context` 并回写 `last_selected`；否则降级但保证围栏闭合 |
| `app/services/chat_service.py` | `_reply_sources` 优先 `last_selected` |
| `tests/test_rag_029_context_builder.py` | **新增** |
| `tests/test_context_merge.py` | 新增 `test_merge_never_leaves_unclosed_fence`（改前必然失败的行为） |

审查整改轮（本轮）：

| 文件 | 改动 |
|---|---|
| `app/rag/context_builder.py` | 证据块**整块丢弃不再截断**（H-02）；`_FENCE_OVERHEAD` 进入块容量核算；按行解析 Markdown 围栏、闭合宽度沿用开围栏（M-01）；`_trim_rag_text` 所有出口强制围栏不变式（H-03）；新增 `structured_hits()` 接受任意 `Sequence[ChunkResult]`；新增 `CONTEXT_BUDGET_EXHAUSTED` |
| `app/rag/retriever.py` | 新增 `assemble_retrieval()` —— 检索结果进模型的**唯一入口**，两条分支都回写 `last_selected`，装不下时记 `rag_context_budget_exhausted` |
| `app/agents/fast_path.py` | `snippet` → `assemble_retrieval(...).text`；一块都装不下时说「超预算」而非「没有资料」（H-01） |
| `app/agents/supervisor.py` | 同上（H-01）。**未**触碰 `_failure_text(exc, generic=_SUPERVISOR_GENERIC_FAILURE)`（`b2432e4` 引入） |
| `app/services/chat_service.py` | `_reply_sources` **不再回落** `last_hits`：无法证明来源时声明为空（H-03） |
| `tests/test_rag_029_context_builder.py` | 删掉锁定「半块入选」的旧断言，补齐极小预算三边一致、四反引号/`~~~`、短小未闭合降级串、tuple 命中、Fast RAG / Supervisor 端到端 |
| `docs/plans/implementation_rag_029_context_builder.md` | 本文件 |

未改动 `app/core/config.py` / `.env.example` / `README.md`：复用既有预算，不需要新开关。

## 4. 验收证据（行为断言，非通过数）

`tests/test_rag_029_context_builder.py` 34 条，覆盖：

1. **围栏闭合**：`test_long_rag_dropped_but_fence_still_closed`（预算 400 → `endswith("[/UNTRUSTED_SOURCE]")`、`len<=400`、`selected==["c1"]`）、`test_every_budget_produces_closed_fence`（6 个预算值扫描）、`test_oversized_everything_means_no_evidence`
2. **整块丢弃不截断**：`test_oversized_first_block_is_dropped_whole_not_truncated`（`selected==[]`、`text==""`、无 `[资料 1]`）、`test_complete_block_never_loses_its_tail`（`BEGIN-...-END-SENTINEL` 尾部哨兵必须在）、`test_oversized_code_block_is_dropped_whole_when_not_first`
3. **代码围栏按宽度配对**：`test_safe_truncate_never_leaves_unclosed_fence`（limit 从 1 扫到全文，逐点 `unclosed_fence_marker(out) is None`）、`test_four_backtick_fence_is_not_closed_by_three`（`out.rstrip().endswith("````")`）、`test_tilde_and_mixed_fences`（`~~~`／五反引号／缩进围栏／行内反引号）
4. **硬不变量**：`test_selected_matches_rendered_markers_one_to_one`、`test_marker_count_equals_selected_count_across_budgets`（含 110）、`test_markers_selected_and_sources_agree_even_when_all_empty`（预算 110 时 markers/selected/sources 全空）
5. **反补位**：`test_dropped_chunk_absent_from_selected`（`c2` 装不下时更小的 `c3` 不得顶替）
6. **来源**：`test_reply_sources_prefers_last_selected_over_last_hits`、`_when_nothing_selected`、`test_reply_sources_never_falls_back_to_unproven_last_hits`、`test_structured_hits_accepts_tuple_and_rejects_string`
7. **记忆**：`test_memory_trim_keeps_newest_entries_and_never_half_entry`、`test_memory_single_oversized_entry_dropped_whole`、`test_memory_not_truncated_when_within_budget`、`test_memory_plus_long_rag_stays_within_budgets`
8. **降级路径**：`test_degraded_string_path_keeps_fence_closed`、`test_degraded_path_closes_short_unclosed_input`、`test_degraded_path_leaves_plain_text_untouched`、`test_degraded_path_drops_rag_rather_than_stray_close_tag`、`test_merge_memory_and_rag_keeps_fence_closed`
9. **三条真实路径端到端**：`test_pipeline_last_selected_matches_final_context`、`test_fast_rag_path_applies_block_budget_and_aligns_sources`、`test_supervisor_path_applies_block_budget_and_aligns_sources`，以及两条「超预算」口径用例、`test_fast_rag_custom_string_retriever_stays_fence_closed`

端到端断言模型真正收到的文本：`_CaptureLLM` 记下 user 消息，从 `## 知识库\n` 段取出
payload，断言 `len(payload) <= RAG_CONTEXT_CHARS`、围栏闭合、
`[资料 N]` 与 `last_selected` 与 `ChatService._reply_sources()` 三方一致。

### 4.1 审查 7 条独立反例的复刻（全部由绿）

临时探针（只在验证 worktree，不入库）复刻审查报告第 7.3 节的 7 条反例，
实测 `7 passed`。与变异的对应关系：

| 审查反例 | 探针 | 对应变异（变红即证明守护有效） |
|---|---|---|
| 短小未闭合降级字符串 | `test_p1_short_unclosed_degraded_string_is_closed` | N2 |
| tuple 降级来源错配 | `test_p2_tuple_hits_are_treated_as_structured` | f |
| 极小预算 marker/selected/source 错配 | `test_p3_minimal_budget_keeps_markers_selected_sources_aligned` | H02 |
| 四反引号围栏 | `test_p4_four_backtick_fence_is_closed_with_four` | M01 |
| 完整证据被截断 | `test_p5_selected_block_is_never_truncated` | H02 |
| Fast RAG 绕过 Builder | `test_p6_fast_rag_respects_budget` | H01a |
| Supervisor 绕过 Builder | `test_p7_supervisor_respects_budget` | H01b |

> 说明：把探针放回改前代码会因 `assemble_retrieval` / `unclosed_fence_marker`
> 不存在而变成**导入错误**，按门禁规则「导入错误不单独证明业务问题」，
> 故改用上表对应的 7 个行为变异来证明这些行为已被测试真正守护。

## 5. 变异验证（整改轮 12 项，逐个注入 → 必须变红 → 按备份还原）

脚本：`data/pytest-tmp/rag029_mutate2.py`，全部在**独立 worktree**
`data/pytest-tmp/rag029-fix-wt` 中执行，不碰主工作树。
**先跑无变异基线**（`34 passed`）确认红色不是环境故障；每个变异注入后
`assert mutated != original` 自证生效（本机工作副本是 CRLF，读回文本用 `\n`
匹配可能假生效），跑完立即还原，`finally` 再按启动时的备份全量还原一次。

| # | 变异 | 文件:位置 | 结果 |
|---|---|---|---|
| **N1** | 去掉 `_FENCE_OVERHEAD`：`room = limit - _FENCE_OVERHEAD` → `room = limit` | `context_builder.py::_select_rag_blocks` | **红** 4 failed, 30 passed |
| **N2** | `_trim_rag_text` 删掉闭合补齐：`len(body) <= limit and fence_closed(body)` → `len(body) <= limit` | `context_builder.py::_trim_rag_text` | **红** 2 failed, 32 passed |
| M01 | 闭合宽度写死三个反引号：`closer = "…\n" + opened` → `"…\n" + "```"` | `context_builder.py::safe_truncate` | **红** 1 failed, 33 passed |
| H02 | 首块无条件装入（恢复截断前身）：`if used + cost > room:` → `... and blocks:` | `context_builder.py::_select_rag_blocks` | **红** 6 failed, 28 passed |
| H01a | Fast RAG 绕过 Builder：`assemble_retrieval(...).text` → `snippet` | `app/agents/fast_path.py` | **红** 3 failed, 31 passed |
| H01b | Supervisor 绕过 Builder：同上 | `app/agents/supervisor.py` | **红** 2 failed, 32 passed |
| a | 去掉围栏补齐：`return head + "\n" + FENCE_CLOSE` → `return head` | `context_builder.py::_trim_rag_text` | **红** 5 failed, 29 passed |
| b | `selected` 直接返回全部命中 | `context_builder.py::build_context` | **红** 16 failed, 18 passed |
| c | 记忆裁剪改尾部硬切 | `context_builder.py::trim_memory_entries` | **红** 2 failed, 32 passed |
| d | 忽略 `RAG_CONTEXT_CHARS` | `context_builder.py::build_context` | **红** 14 failed, 20 passed |
| e | 装不下就跳过继续装后面的 | `context_builder.py::_select_rag_blocks` | **红** 6 failed, 28 passed |
| f | `_reply_sources` 忽略 `last_selected`、直接用 `last_hits` | `app/services/chat_service.py` | **红** 7 failed, 27 passed |

存活变异数：**0**。

> 过程记录（沿用首轮教训）：首轮曾因 (1) 子进程 env 只传 `PATH` 导致 6 个假红、
> (2) 测试超时被 SIGTERM 中断导致变异残留并污染他人回归数字。本轮因此改为
> 在独立 worktree 中执行、子进程继承 `os.environ`、先跑基线、
> 注入后自证生效、启动时全量备份 + `finally` 兜底还原。

## 6. 回归与静态检查

| 项 | 命令（均在独立 worktree `data/pytest-tmp/rag029-fix-wt` 中执行） | 结果 |
|---|---|---|
| 专项 + 归口 | `pytest tests/test_rag_029_context_builder.py tests/test_context_merge.py -q` | `38 passed` |
| 专项 + 审查反例探针 | 再加 `tests/test_review_probes_rag029.py` | `45 passed` |
| 关联回归（12 个文件，含 supervisor / rag） | 见 §6.1 | `1 failed, 193 passed` |
| 全量回归 | `pytest -q`，全新 DB `rag029-fix-full2.db` + 全新 basetemp | `2 failed, 1081 passed, 3 skipped` |
| ruff | `python -m ruff check app/` | `All checks passed!` |
| mypy | `python -m mypy app/rag/ app/agents/` | `Success: no issues found in 89 source files` |

### 6.1 全量回归的两条失败（均与本卡无关，逐条取证）

| 失败 | 取证 | 结论 |
|---|---|---|
| `tests/test_supervisor.py::test_supervisor_error_skips_subtask_and_hides_exception` | 首轮已在 `git worktree add --detach HEAD`（3aaa310）单独跑同一测试同样失败（`1 failed, 15 passed`） | 既有缺陷：Supervisor 失败文案断言。本轮只改 `_retrieve()` 的检索组装分支，未触碰 `b2432e4` 的 `_failure_text` 相关改动 |
| `tests/test_chunk_auto_routing_evaluation.py::test_auto_entry_quality_report_and_replay` | 在同一 worktree 里 `git checkout HEAD -- app/`（回退本轮改动）后**仍失败**：`ValueError: Frozen RAG-021 comparison dataset/parameter mismatch` | 既有缺陷，属 RAG-021 冻结数据集，本卡未触碰分块/路由代码 |

> 第一轮全量还有一条 `tests/test_rag_033_parse_quality.py::test_committed_report_matches_script_output`
> 也失败，原因是 worktree 检出时 `core.autocrlf` 把语料转成 CRLF（561 vs 537 字节）。
> 按 `git -c core.autocrlf=false checkout -- evals/` 重新检出后**恢复通过**
> （`tests/test_rag_033_parse_quality.py` 84 passed）。这是 worktree 环境假红，
> 不是代码缺陷——与 rag-033-reviewer 提醒的坑一致。

## 7. 撤销「首块截断」授权（任务派发方的决定变更）

任务卡首轮规格里有一条：

> 若**第一块本身就装不下**，则将该块按预算截断，但必须补齐围栏闭合标记与省略标记
> （`reason="budget_truncated"`）

**该授权已被任务派发方撤销**，实现改为「整块丢弃」。理由两条，均由独立审查抓到：

1. **违反 ADR-0005 §9**。该决定批准的是「**完整证据块**……不能安全装入时明确超限
   或证据不足，不截坏块/围栏」。截一半的块本身就是半成品证据，会丢掉限定条件或
   结论后半段。首轮「至少给一点证据」的妥协，代价是违背已批准决定。
2. **破坏 `selected` 与 payload 的一致性**。实测 `budget=110`（`_FENCE_OVERHEAD=109`）
   时 payload 只有前导说明 + `…` + 闭合标记，**既没有 `[资料 N]` 也没有任何正文**，
   但 `selected == ['c1']`、`_reply_sources()` 返回 `['c1']`——对外声称引用了一块
   根本没进模型的证据。这比「少给证据」严重得多。

同时把「payload 中实际出现的 `[资料 N]` 与 `selected` 完全一致（**包括都没有**）」
写成硬不变量，用 `test_markers_selected_and_sources_agree_even_when_all_empty`
和变异 H02 钉住。`REASON_BUDGET_TRUNCATED` 常量随之删除，不留死代码。

## 8. 已知边界

- `selected` **不等于**「答案引用了它」。逐答案点的引用核验属于 RAG-030，本卡不做。
- 证据块整块装入或整块丢弃，**不存在「进了半块」的中间态**。代价是极小
  `RAG_CONTEXT_CHARS`（< 前导说明 + 首块长度）时一块证据都进不去，用户会看到
  「超预算」而不是部分资料——这是 ADR-0005 §9「不能安全装入时明确超限或证据不足」
  要求的行为，不是缺陷。
- 记忆条目按 `\n{2,}` 切分并归一化为一个空行分隔。若压缩器输出不含空行，整段视为**一条**记忆，
  超预算则整段丢弃 —— 不会出现半条，但会一次丢较多。
- 降级路径（`build_from_rag_text`）拿不到结构化命中，`selected` 恒为空、
  `_reply_sources` 也声明为空来源。这意味着**只有字符串**的自定义 Retriever
  会**欠声明**来源（有证据但不列出来源），而不是夸大来源。要彻底对齐需要它
  暴露 `last_hits`；接 `HybridRetriever` 的真实链路不存在此问题。
- `AgentPipeline`（`app/agents/pipeline.py`）的 `_structured_hits()` 仍只认
  `list`，tuple 命中会走字符串降级并声明空来源（欠声明，安全）。本轮改动边界
  未包含该文件，故未统一；待边界放开后一行即可对齐 `structured_hits()`。
- `drop_reasons` 以分块 id 为键；同一批命中出现重复 id 时后者覆盖前者（实际检索不会产生）。
- 预算只约束记忆段与 RAG 段各自的长度，不含后续追加的 `state.retrieval_notice`
  与工具结果——它们由 `app/llm/budget` 的整体预算另行约束。
