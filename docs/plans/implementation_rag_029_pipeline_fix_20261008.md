# RAG-029 默认管线上下文组装补修 — 实施说明（2026-10-08）

本说明对应 `docs/plans/plan_rag_029_pipeline_alignment_20261008.md`，修复 2026-10-08
独立复审（`docs/reviews/2026-10-08-RAG-029按块上下文复审.md`）判定的三条阻断反例
H-01/H-02/H-03。原实现说明 `implementation_rag_029_context_builder.md` 保留历史。

## 1. 功能实际完成了什么

默认 `AgentPipeline` 此前维护第二份 list-only 组装（`_structured_hits` 只接受 list、
降级不回写 `last_selected`、超预算不披露），与唯一入口 `assemble_retrieval` 漂移。
本次把默认管线接到共享入口，使三条编排路径（自研管线 / Fast RAG / Supervisor）真正同源：

1. **H-01（tuple 整块丢弃）**：默认管线不再自建组装，改调 `assemble_retrieval`。
   `tuple` 等任意合法 `Sequence[ChunkResult]` 命中按「整块装入或整块丢弃」处理，
   不再把超预算的块截成半块塞进上下文。
2. **H-02（超预算确定性披露）**：结构化命中非空但一块都装不下时，管线把
   `CONTEXT_BUDGET_EXHAUSTED` 同时写进送模型的外部上下文（`## 外部上下文`）和
   `retrieval_disclosure`。同步 `run` 经 `_with_disclosure` 前置、流式 `run_stream`
   在正文前先吐出 token，两条入口都可见，不伪装成「检索无命中」。
3. **H-03（降级清空 selected）**：降级分支由 `assemble_retrieval` 统一写回空列表，
   不再残留上一轮 `last_selected`；`_reply_sources` 因此不会把旧轮来源当本轮来源。

同时修正 `app/rag/context_builder.py` 模块注释里「首块按预算截断」的过时描述
（实际早已改为整块丢弃）。

### 1.1 P2-01 补修（2026-10-09 第二轮复审 CE-04）

第二轮入口级复审发现：可复用自定义 Retriever 第 1 轮成功选入 `[stale]`、第 2 轮
`retrieve()` 抛异常但**不清空** `last_hits` 时，管线/Fast/Supervisor 仍无条件调
`assemble_retrieval(retriever, "")` → `structured_hits` 读到残留 `last_hits` →
陈旧证据被按预算选入、回写 `last_selected`、进模型 payload，并在 `_reply_sources`
里跨轮泄漏；与「检索不可用、不含知识库内容」的确定性披露自相矛盾。上一轮守护测试
`test_pipeline_retriever_exception_converges_unavailable` 用 `last_hits=[]`，恰好绕开
该分支，造成假覆盖。

修法（共享根因，最小改动）：`assemble_retrieval` 新增 `trust_structured: bool = True`。
当本轮检索未跑成（`outcome.status == UNAVAILABLE`）时，三条编排路径都传
`trust_structured=False`：不读 `last_hits`，直接走记忆-only 降级并把 `last_selected`
清空。unavailable 既有披露语义保持不变，不被 `CONTEXT_BUDGET_EXHAUSTED` 覆盖。

## 2. 成功与失败行为（同步 / 流式）

| 场景 | 成功行为 | 失败 / 降级行为 |
|---|---|---|
| 结构化命中（list/tuple）部分选入 | 选入块进 `state.context`，围栏闭合，`last_selected`=实际选入 | 某块起超预算：其后全部 dropped、不补位；`[资料 N]`↔selected↔来源三方一致 |
| 一块都装不下 | （无证据进模型） | `state.context` 补 `CONTEXT_BUDGET_EXHAUSTED`；`retrieval_disclosure`=该文案；同步前置 / 流式先吐；`last_selected=[]` |
| 只有字符串的自定义 Retriever | 字符串按预算裁剪且围栏闭合 | `last_selected=[]`、来源声明为空（欠声明，安全） |
| `retriever.retrieve` 抛异常 | — | 收敛 `unavailable`，不向上炸；**不读取残留 `last_hits`**，`last_selected=[]`、来源为空、陈旧证据不进模型 payload；保留 unavailable 既有披露，不被「超预算」覆盖（P2-01） |
| 无 Retriever | 保留既有 `merge_memory_and_rag` 记忆+整段合并 | 不走按块预算（行为不变） |

## 3. 代码位置（改动文件，均在 RAG-029 allowed_paths）

| 文件 | 改动 |
|---|---|
| `app/rag/retriever.py` | `assemble_retrieval(retriever, snippet, memory="", *, trust_structured=True)`：`memory` 贯通 Memory/RAG 独立预算；`trust_structured=False` 时不读 `last_hits`、走降级并清空 `last_selected`（P2-01）。 |
| `app/agents/pipeline.py` | 删除自建 `_assemble_context` list-only 组装与 `_structured_hits`；改调共享入口。新增 `_apply_budget_exhausted_notice`：结构化命中非空但 selected 为空时，把 `CONTEXT_BUDGET_EXHAUSTED` 补进 `state.context` 与 `state.retrieval_disclosure`（仅在 `retrieval_status` 为 `""`/`ok` 时）。`outcome.status==UNAVAILABLE` 时传 `trust_structured=False`。无 Retriever 时保留 `merge_memory_and_rag`。 |
| `app/agents/fast_path.py` | `assemble_retrieval(retriever, snippet, trust_structured=evidence_trusted)`；unavailable 时不信任残留 `last_hits`。 |
| `app/agents/supervisor.py` | 同上：`evidence_trusted = outcome.status != UNAVAILABLE`。 |
| `app/rag/context_builder.py` | 模块注释：把「首块按预算截断」更正为「整块丢弃、不截断，证据不足由调用方披露」。 |
| `tests/test_rag_029_pipeline_alignment.py` | 新增 16 条回归（H-01/H-02/H-03 + 五类失败边界 + P2-01 的 pipeline/fast/supervisor 三路径「raising-but-stale-hits」反例）。 |
| `docs/plans/plan_rag_029_pipeline_alignment_20261008.md` | 补充五类失败边界清单与守护测试映射。 |

未改：检索排名、模型配置、字符预算值、父块策略、数据库、`ChatService._reply_sources`。


## 4. 验证命令与结果（解释器 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`）

每轮独立 `DATABASE_URL=sqlite:///./data/pytest-tmp/<uuid>.db` + 独立 `--basetemp`；
全量跑前清空 `LLM_API_KEY/EMBEDDING_API_KEY/DASHSCOPE_API_KEY/OPENAI_API_KEY`，设 `PYTHONUTF8=1`。

| 项 | 命令 | 结果 |
|---|---|---|
| 复现失败（改前） | `pytest tests/test_rag_029_pipeline_alignment.py` | `5 failed`（H-01/H-02×3/H-03 全红） |
| 修复后专项 | 同上 | `13 passed in 4.17s`，退出 0 |
| 关联回归 | `pytest tests/test_rag_029_context_builder.py tests/test_context_merge.py tests/test_fast_route.py tests/test_supervisor.py` | `64 passed in 4.88s`，退出 0 |
| 全量 | `pytest -q` | `2 failed, 1183 passed, 3 skipped in 398.58s`，退出 1（两条失败见 §6，与本卡无关） |
| **P2-01 复现（改前）** | `pytest tests/test_rag_029_pipeline_alignment.py -k unavailable_ignores_residual` | `3 failed`（pipeline/fast/supervisor 三路径 CE-04 全红），退出 1 |
| **P2-01 修复后** | 同上 | `3 passed in 2.62s`，退出 0 |
| **P2-01 后专项+关联回归** | `pytest tests/test_rag_029_pipeline_alignment.py tests/test_rag_029_context_builder.py tests/test_context_merge.py tests/test_fast_route.py tests/test_supervisor.py` | `80 passed in 3.17s`，退出 0 |
| ruff | `python -m ruff check --no-cache app/` | `All checks passed!`，退出 0 |
| mypy | `python -m mypy app/` | `Success: no issues found in 185 source files`，退出 0 |
| 前端 typecheck | `cd frontend; npm run typecheck` | 退出 0（`tsc --noEmit` 无输出错误） |
| 前端 build | `cd frontend; npm run build` | `✓ built in 6.93s`，退出 0 |
| **P2-01 后全量** | `pytest -q`（隔离配方） | `2 failed, 1186 passed, 3 skipped in 270.77s`，退出 1（同 §6 两条既有失败，通过数 +3 = 新增 CE-04） |

原 34 条专项 + 首轮 7 条反例对应的 Fast RAG / Supervisor 预算、围栏闭合、整块丢弃、
来源对齐、记忆裁剪行为均在上述「关联回归 64 passed」中保持通过。

## 5. 明确没做的事（non-goals / 边界）

- 不把 `selected` 宣称成逐答案点 cited（逐答案点引用核验属 RAG-035/030）。
- 不改 Milvus、不改 RAG-033 报告命名、不改检索阈值与排名。
- 未做真实 LLM/Embedding 调用；所有模型侧用 `_EchoLLM`/`_CaptureLLM` 假双，
  仅证明链路与确定性披露，不证明检索质量。
- 未触碰 RAG-015 / RAG-032 / RAG-036 的代码与状态。
- 未修改 `tasks.yaml`（关闭登记由主 Agent 负责）。
- 未做任何 git 提交。

## 6. 全量两条失败的取证（与本卡改动无关）

本卡业务 diff 仅 `app/agents/pipeline.py`、`app/rag/context_builder.py`、
`app/rag/retriever.py`（另加测试与 docs）。两条失败的 SUT 均不在 diff 内：

| 失败测试 | 根因取证 | 与本卡关系 |
|---|---|---|
| `tests/test_rag_033_parse_quality.py::test_report_file_name_carries_gate_version` | 断言落盘提交报告名 `parse-quality-20261009-gate-v3.json` 等于 `default_report_name()`（运行日 2026-10-08 → `...-20261008-...`）。日期漂移：提交态报告文件名带 20261009，测试按当日日期比对。 | RAG-033 报告命名，本卡未触碰；纯日期敏感的既有 artifact 漂移。 |
| `tests/test_rag_log_minimization.py::test_milvus_delete_failure_retains_diagnostics_without_exception_body` | `app/rag/vectorstore/milvus.py:264` 调 `self._connect(identity, index=target_index)`，而测试 monkeypatch 的 `_connect` lambda 不接受 `index=` 形参 → `TypeError`。 | Milvus 删除路径的 SUT/测试 mock 契约不匹配，本卡未触碰 milvus.py。 |

任务派发方点名的两条既有失败（`test_supervisor.py::test_supervisor_error_skips_subtask_and_hides_exception`、
`test_chunk_auto_routing_evaluation.py::test_auto_entry_quality_report_and_replay`）本轮
**未出现在失败清单**（已通过或由其他卡修复），无需本卡处理。

## 7. 关闭对账记录

证据类别：**E1**=外部运行证据；**E2**=隔离集成证据（真实代码 + 隔离后端）；
E3=自检/单元；E4=推导/Mock/历史。必交付项只认 E1/E2；E3/E4 一律记「未验证」。

| 原始契约（RAG-029 / 复审阻断项） | 结果 | 证据类别 | 证据位置与文件指纹（SHA256） |
|---|---|---|---|
| 纯 Context Builder 按块预算，无 DB 直连（默认管线接入共享入口） | 通过 | E2 | `assemble_retrieval` 纯函数组装；`app/rag/retriever.py` `E492DFBD…25988A`；`app/agents/pipeline.py` `A39060C2…9B97E9` |
| H-01：tuple 命中整块丢弃，无半块、`last_selected=[]` | 通过 | E2 | `test_pipeline_tuple_hits_dropped_whole_not_truncated`；`tests/test_rag_029_pipeline_alignment.py` `9E1C70FB…BE07B7` |
| H-02：超预算时模型指令 + 同步/流式最终回复都确定性披露 | 通过 | E2 | `test_pipeline_budget_exhausted_in_final_sync_reply` / `_in_final_stream_reply`；`pipeline.py` `A39060C2…9B97E9` |
| H-03：降级路径清空上一轮 `last_selected` | 通过 | E2 | `test_pipeline_degraded_resets_stale_selected`；`retriever.py` `E492DFBD…25988A` |
| Memory/RAG 独立预算、记忆按条裁剪保留最新 | 通过（既有） | E2 | `test_memory_*`、`test_pipeline_last_selected_matches_final_context` 在 80 passed 回归内 |
| selected 与 `[资料 N]` 与 `_reply_sources` 三方一致（含全空） | 通过 | E2 | `test_pipeline_marker_selected_sources_three_way_consistent`、`test_pipeline_empty_hits_is_not_budget_exhausted` |
| 围栏闭合（含字符串降级） | 通过（既有） | E2 | `test_degraded_*`、`test_pipeline_string_only_retriever_fence_closed_empty_sources` 在 80 passed 内 |
| 未选块不出现在 selected 来源（不回落 last_hits） | 通过（既有） | E2 | `test_reply_sources_*`、`test_pipeline_sources_only_selected_no_other_tenant_leak` |
| **P2-01：Retriever 抛异常收敛 unavailable 时，残留 `last_hits` 不被当本轮证据、来源为空、陈旧内容不进模型 payload（pipeline/fast/supervisor 三路径）** | 通过 | E2 | 原证据 `test_pipeline_retriever_exception_converges_unavailable` 构造过窄（`last_hits=[]`）；本轮改为真实两轮链路反例 `test_pipeline_unavailable_ignores_residual_hits`、`test_fast_path_unavailable_ignores_residual_hits`、`test_supervisor_unavailable_ignores_residual_hits`（改前 3 failed→改后 3 passed）；`retriever.py` `E492DFBD…25988A`、`pipeline.py` `A39060C2…9B97E9`、`fast_path.py` `5C89CA5B…80C2CF`、`supervisor.py` `3BC00A1F…29530` |
| 五类失败边界各至少一条回归 | 通过 | E2 | 见计划文件边界清单；`plan_rag_029_pipeline_alignment_20261008.md` `12759AB4…D260` |
| ruff / mypy / 前端 typecheck / build | 通过 | E1 | 见 §4 命令与退出码 |
| 全量 pytest 全绿 | **未验证** | — | 全量 `2 failed, 1186 passed`；两条失败 SUT 不在本卡 diff（§6 取证）。按门禁如实记「未验证/有既有失败」，不写全绿。 |
| 真实检索质量 / 真实 LLM 生成评测 | 未验证 | E4 | 本卡 non-goal，未做真实调用；Mock 只证明链路。 |
| 独立子 Agent 复核最终代码指纹 | 未验证 | — | 由主 Agent / 独立复核方在关闭时执行；本次仅本方自测。 |

**未验证项清单**：① 全量 pytest 非全绿（两条无关既有失败：RAG-033 报告日期漂移、Milvus 删除 mock 契约）；
② 真实 LLM/Embedding 生成与检索质量评测（non-goal）；③ 独立第三方复核代码指纹（关闭门禁由主 Agent 触发）。
