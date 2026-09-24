# RAG-009：单次评测结束时恢复 RAG_HYBRID_RRF_K

> 状态：已完成（`tasks.yaml` RAG-009 `done`，2026-09-24）
> 分支：`fix/rag-005-006-review`
> 来源：评审修复方案阶段 1 / L3；Cursor 实施计划归档
> 日期：2026-09-24

单次评测在进程内改过 `RAG_HYBRID_RRF_K` 后，无论成功或中途失败，结束时都写回进入前的值。扫描路径的恢复逻辑不动，也不跑官方评测。

## 目标

[`scripts/run_rag_baseline.py`](../../scripts/run_rag_baseline.py) 的单次路径在 `apply_rrf_k` 之后，原先 `finally` 只做 `index.close()`，进程内常数留在被覆盖的值上：

```python
    apply_rrf_k(args.rrf_k)
    ...
    try:
        outcomes = await run_all_cases(...)
    finally:
        index.close()
```

`run_sweep` 已在自己的 `finally` 里写回 `previous_k`，本任务不改这段。

## 非目标

不改默认 k、不改 `app/core/config.py`、不跑官方评测、不重跑 rag-v0.1、不改冻结基线 JSON。

## 实施

在单次路径进入 `apply_rrf_k` 之前保存 `previous_k`。用包住后续步骤的 `finally` 写回 `settings.RAG_HYBRID_RRF_K`，索引关闭仍留在内层 `finally`。这样索引构建或案例运行抛错时也会恢复。报告里的 `rrf_k` 已由调用前的 `build_run_config` 记下，写回不影响当次报告。

脚本头「待完善」删掉「单次覆盖融合常数后，进程在退出前不会自动改回原值。」其余两条保留。`--rrf-k` 的帮助文字仍写「不写回配置文件」，只补充进程结束时改回进入前的值。

[`tests/eval/test_rrf_k_experiment.py`](../../tests/eval/test_rrf_k_experiment.py) 保留现有 `test_apply_rrf_k_can_be_restored`。新增用例直接调用 `main_async`，把 `build_eval_index`、`run_all_cases`、`make_report`、`write_json` 换成空操作，不建索引、不跑语料：

- 进入前记下 `RAG_HYBRID_RRF_K`，传入 `--rrf-k` 的另一个正整数，返回后常数与进入前一致。
- `run_all_cases` 抛错时，常数同样写回。
- 用同样的空操作调用 `run_sweep`（列表含 60），断言扫描结束仍等于进入函数前的常数。不改 `run_sweep` 源码。

完成后只把 [`tasks.yaml`](../../tasks.yaml) 的 RAG-009 标为 `done`，清掉 `blocked_by: RAG-008`，补事实与 `completed_at`。不开始 RAG-010。

### 实施清单

| 项 | 内容 | 状态 |
|---|---|---|
| restore-single | 单次路径保存并在 `finally` 写回 `RAG_HYBRID_RRF_K`；删脚本头待完善中对应一条 | done |
| restore-tests | 新增单次成功/失败与扫描恢复用例，不跑官方评测 | done |
| verify-close-009 | 跑 pytest 与 ruff，通过后把 `tasks.yaml` 的 RAG-009 标为 done | done |

## 验收

```powershell
pytest tests/eval/test_rrf_k_experiment.py -v
ruff check scripts/run_rag_baseline.py tests/eval/test_rrf_k_experiment.py
```

## 风险与回滚

只影响评测脚本的进程内配置。回滚是去掉单次路径的写回；进程退出后该改动本来也会消失。
