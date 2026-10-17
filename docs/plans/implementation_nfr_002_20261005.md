# NFR-002 实现说明（结构化日志，2026-10-05）

> 任务卡：NFR-002「结构化日志：JSON 格式与 trace_id、user_id、tenant_id」
> 状态：实现完成，验收条件满足；待人工审查后关闭
> 分支：nightly/rag-20261005
> 定位：RAG 域 PRAG-004（低分阈值拒答）的前置依赖

## 1. 完成了什么

应用日志现在输出为**单行 JSON**，并在请求生命周期内贯穿 **trace_id / user_id / tenant_id** 三字段，跨模块可按请求串联、可归因。

- `JsonLogFormatter`：每条日志输出合法 JSON（ts/level/logger/message + trace_id/user_id/tenant_id）。
- `TraceIdMiddleware`：每请求生成/复用 `X-Request-ID` 作为 trace_id，写入日志上下文并回写响应头；请求结束清空。
- 认证后注入：`get_current_user` 成功时把 user.id / user.tenant_id 写入日志上下文（认证关闭时清空）。
- `setup_logging()` 幂等安装，按 `settings.LOG_LEVEL` 设级别。

## 2. 实际修改文件

| 文件 | 性质 |
|---|---|
| `app/core/log_context.py` | 新增：contextvars 承载 trace_id/user_id/tenant_id |
| `app/core/json_logging.py` | 新增：JsonLogFormatter + setup_logging（幂等） |
| `app/core/trace_middleware.py` | 新增：TraceIdMiddleware |
| `app/main.py` | 接线：setup_logging() + app.add_middleware(TraceIdMiddleware) |
| `app/api/deps.py` | 接线：get_current_user 成功后 set_user |
| `tests/test_logging.py` | 新增：6 个测试（JSON 格式、上下文贯穿、middleware、幂等、端到端） |

## 3. 验证命令与结果

| 命令 | 退出码 | 结果 |
|---|---|---|
| `pytest tests/test_logging.py -v` | 0 | **6 passed** |
| `pytest -k 'log or trace' -q` | 1 | 39 passed, 2 skipped, 485 deselected；**7 errors 均为 pytest-tmp 临时目录清理 PermissionError（Windows 占用），非本卡引入** |
| `ruff check <5 改动文件 + test>` | 0 | All checks passed |
| `mypy <5 改动文件>` | 1 | **改动文件 0 新增错误**；58 个错误全在其余 25 个存量文件（与 REL-003 记录的 mypy=1 项目存量基线一致） |

## 4. 验收对照（卡 acceptance）

- [x] 一次对话请求的所有日志可用同一 trace_id 检索（TraceIdMiddleware + contextvars + formatter 贯穿；端到端测试验证）。
- [x] 日志为合法 JSON 行（json.dumps 输出，测试 json.loads 解析通过）。
- [x] 新增用例可被 `pytest -k 'log or trace'` 选中（6 个测试全部被选中且通过）。

## 5. 明确没做的事（Non-goals 边界）

- **未引入**外部日志平台 / 无新第三方依赖（仅标准库 json/contextvars/uuid）。
- **未改**日志脱敏逻辑（SEC-002 的 RedactingLogFilter 与 JsonLogFormatter 正交共存，Filter 先改写 msg、Formatter 后输出）。
- **未做**日志检索界面。
- **未改**审计日志模块（app/audit/logger.py 独立，不受影响）。
- **未 commit、未合并、未部署、未推送**（夜间红线）。

## 6. 残余风险与说明

- **pytest-tmp 清理 7 errors**：`data/pytest-tmp/run` 目录被占用无法删除（WinError 5），发生在 test_cli.py / test_rag_import_jobs.py 的 setup/teardown，与 NFR-002 无文件或逻辑依赖。判定为环境性（残留临时目录句柄），不影响本卡验收。
- **mypy 存量 58 错误**：项目基线非 0（REL-003 已记录 mypy=1），本次改动文件未新增错误；存量治理属项目整体事项，非本卡范围。
- 认证关闭（AUTH_ENABLED=false）时三字段为空串（trace_id 仍由 middleware 提供），符合预期。

## 7. 次日人工审查清单

1. 复核 `pytest tests/test_logging.py`（6 passed）与 `pytest -k 'log or trace'` 的 39 passed。
2. 确认 7 errors 确为 pytest-tmp 清理环境问题（可删除 data/pytest-tmp 后复跑确认）。
3. 实机启动一次对话请求，检查日志为 JSON 且含 trace_id/user_id/tenant_id。
4. 决定 NFR-002 关闭；关闭后 PRAG-004 的"低分命中可复现日志记录"依赖已满足。
