# 基线 83 失败修复（2026-10-23）

在 `feat/EVAL-002` 上于全量 pytest 观察到 83 failed；去掉 EVAL-002 业务改动后同簇仍失败，判定为基线问题后修复。

## 根因

1. **Windows subprocess GBK 解码**：`text=True` 默认用系统代码页读取子进程 UTF-8 中文输出，导致 `stdout`/`stderr` 为 `None`（`test_rag_gate`、`test_agent_gate*`、`test_validation_isolation` 等）。
2. **迁移 stamp 越过 timeout 修订**：`_stamp_if_schema_already_at_head` 在仅有基础配额列时直接标到 `d7e0f3a21b44`，跳过 `timeout_seconds` 所在迁移，启动迁移后工作流无超时列。
3. **日志最小化测试 Mock**：`chat_stream` 调用 `consume_tokens` 时 `Mock.rowcount` 不能转 `int`。

## 改动

- `app/core/migration.py`：缺 `timeout_seconds` 时先标到 `a4b7c2d9e160`，再由 upgrade 补列。
- `tests/_utf8_subprocess.py`：统一 `encoding=utf-8` + `PYTHONUTF8`。
- 相关 gate/validation CLI 测试改用该助手。
- `tests/test_rag_log_minimization.py`：mock `consume_tokens`。

## 验证

```text
pytest tests/eval/test_rag_gate.py tests/eval/test_agent_gate.py tests/eval/test_agent_gate_entry.py \
  tests/eval/test_agent_ci_endpoint_capture.py tests/test_workflow_timeout_migration.py \
  tests/test_validation_isolation.py tests/test_validation_runner.py tests/test_rag_log_minimization.py -q
# 217 passed

python -m pytest
# 3425 passed, 3 skipped, 0 failed（827.59s）

ruff check . → 0
mypy app/ → 0
```

解释器：`D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`。未推送 CI。
