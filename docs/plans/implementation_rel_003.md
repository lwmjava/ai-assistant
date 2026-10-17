# 实现说明：一次测试门禁记录

> 日期：2026-10-02
> 计划：`docs/plans/plan_d4.md` 的 REL-003
> 结果：五条命令都实际跑过。ruff 与 mypy 退出码为 1，前端类型检查和构建退出码为 0。pytest 在打印汇总之前卡住，进程被停止。进程退出码是 4294967295，这是停止进程的结果，不是 pytest 打印汇总后的退出码。发布结论是「未达到发布合格」。没有改测试，没有改业务代码，没有安装 mypy，没有执行 `ruff check --fix`。

## 环境

- 仓库提交：`8f2c453`
- 日期：2026-10-02
- Python：3.12.0，解释器 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`（conda 环境名 `ai-assistant`）
- Node：v22.20.0
- `frontend/node_modules` 已存在，没有执行 `npm ci`
- 计划正文里的解释器目录写成了 `D:\install\anaconda3\envs\ai-assistant\Scripts`。本机该环境不在这个路径。本次没有改用 base 解释器 `D:\DepTooL\anaconda3\python.exe`，也没有为了换一个通过结果再换解释器。

## 命令与退出码

| 命令 | 工作目录 | 退出码 |
|---|---|---|
| `python -m pytest -q --tb=no` | 仓库根目录 | 4294967295。进程在汇总前被停止，不是 pytest 正常结束的退出码 |
| `ruff check .` | 仓库根目录 | 1 |
| `python -m mypy app/` | 仓库根目录 | 1 |
| `npm run typecheck` | `frontend/` | 0 |
| `npm run build` | `frontend/` | 0 |

pytest 使用了 `-q --tb=no`，断言与跳过项没有改。这是为了让进度字符能对上收集顺序。计划列出的命令是不带这两个参数的 `pytest`。输出格式不同，通过与否的判定没有另做一轮。

## 发布结论

未达到发布合格。

ruff 和 mypy 已经是非 0。pytest 没有跑完。前端两条通过不能把整次记录写成合格。没有覆盖率数字，也不把覆盖率写成达标。

## pytest

收集阶段另一次 `--collect-only` 报告收集到 521 条，用时 2.42 秒。正式这次用 `-q` 只打出 164 个结果标记：17 个 `E`、10 个 `F`，其余为通过标记。之后进度不再增加。该进程 CPU 在连续观察中增量为 0，对外只有连回本进程的 `127.0.0.1` 连接。进程随后被停止，退出码 4294967295。用户正在使用的开发服务没有被停掉。

下面的节点名是把这 164 个标记按收集顺序对齐得到的，不是 pytest 失败汇总里的名字。对齐若有一位偏差，卡住的节点就是进度停住后的下一条。

错误（`E`）：

- `tests/eval/test_effective_date.py::test_parse_query_schedule_at_requires_future_date`
- `tests/eval/test_effective_date.py::test_ed02_future_dated_query_opens_scheduled`
- `tests/eval/test_effective_date.py::test_ed03_undated_249_question_does_not_open_preview`
- `tests/eval/test_effective_date.py::test_ed04_cross_tenant_scheduled_still_hidden`
- `tests/eval/test_effective_date.py::test_ed05_flag_off_matches_pre_adr_behavior`
- `tests/eval/test_effective_date.py::test_eval_index_writes_manifest_dates`
- `tests/eval/test_rag006_pipeline.py::test_pipeline_merges_memory_with_rag`
- `tests/eval/test_rag006_pipeline.py::test_pipeline_rejects_tool_from_retrieved_injection`
- `tests/eval/test_rag006_pipeline.py::test_chat_service_rag_injects_facts_without_executing_dropped_injection`
- `tests/eval/test_rag006_pipeline.py::test_chat_service_rejects_tool_when_injection_chunk_not_dropped`
- `tests/eval/test_rag_baseline_harness.py::test_index_covers_every_manifest_document`
- `tests/eval/test_rag_baseline_harness.py::test_non_current_versions_are_not_retrievable`
- `tests/eval/test_rag_baseline_harness.py::test_tenant_isolation_holds_in_eval_index`
- `tests/eval/test_rag_baseline_harness.py::test_case_outcome_is_scorable_and_serializable`
- `tests/eval/test_rag_baseline_harness.py::test_cases_without_expected_docs_are_excluded_from_ranking`
- `tests/eval/test_rag_baseline_metrics.py::test_dedupe_keeps_first_occurrence_order`
- `tests/eval/test_rrf_k_experiment.py::test_apply_rrf_k_can_be_restored`

失败（`F`）：

- `tests/test_api.py::TestRootEndpoint::test_root_returns_app_info`
- `tests/test_chat.py::test_chat_with_rag_enabled_completes`
- `tests/test_chat.py::test_chat_reply_includes_source_filename`
- `tests/test_chat.py::test_chat_sources_empty_when_retrieval_disabled`
- `tests/test_chat.py::test_chat_stream_includes_sources_event`
- `tests/test_chat.py::test_chat_stream_reports_unexpected_failure`
- `tests/test_chat.py::test_chat_stream_returns_sse`
- `tests/test_chat.py::test_code_result_persists_on_complete`
- `tests/test_chat.py::test_code_result_persists_on_stream`
- `tests/test_chat.py::test_code_result_persists_when_stopped`

卡住、没有结果标记的下一条：

- `tests/test_chat_controls.py::test_viewer_cannot_send_and_member_can`

这次没有跑到的测试没有失败名单。`--tb=no` 没有留下异常文本，所以上面的 `E` 只有节点名，没有异常类型。

负责人：个人开发者。原因：当次进程停在上述节点，只保留指向自己的本地连接，没有汇总行。处置：这些节点不是 2026-09-28 记在 QA-005 上的那 8 项；先按这次名单更新那张卡的事实，再修。截止日期不晚于把本系统当作企业上线之前。本条不改测试。

## ruff

`ruff check .` 输出 `Found 149 errors`，其中 94 条可用 `--fix` 自动改。按规则计数：

| 规则 | 条数 |
|---|---|
| E501 | 29 |
| W292 | 25 |
| F401 | 22 |
| I001 | 21 |
| UP007 | 11 |
| E702 | 9 |
| F821 | 7 |
| UP017 | 6 |
| E402 | 4 |
| UP035 | 4 |
| UP037 | 3 |
| N806 | 2 |
| N818 | 2 |
| W291 | 1 |
| F541 | 1 |
| F841 | 1 |
| UP038 | 1 |

负责人：个人开发者。原因：当次 `ruff check .` 退出码 1，共 149 条。处置：企业上线前单独处理这些规则，不在本条执行 `--fix`。截止日期不晚于把本系统当作企业上线之前。

## mypy

`python -m mypy app/` 退出码 1，输出 `No module named mypy`。该环境的 `Scripts` 下没有 `mypy.exe`。

负责人：个人开发者。原因：当前 conda 环境未安装 mypy。处置：企业上线前在这个环境安装并重跑 `mypy app/`。未安装不能写成类型检查通过。截止日期不晚于把本系统当作企业上线之前。本次没有为了退出码 0 去安装。

## 前端

`npm run typecheck`（`tsc --noEmit`）退出码 0。`npm run build`（`tsc -b && vite build`）退出码 0，构建完成约 6.96 秒。这两条没有失败项。

## 没做的事

没有修测试，没有改业务代码，没有建 GitHub Actions，没有跑覆盖率，没有把这次写成发布合格。QA-005 的卡片事实还没有按这次名单改写。计划正文和 `AGENTS.md` 第 10 节里的旧解释器目录本次没有改成上面的实际路径。
