# FLOW-001、MEM-001、FLOW-002：调度可启动、超窗压缩回归、工作流页失败态

> 状态：FLOW-001 已实现，见 `docs/plans/implementation_flow_001.md`。MEM-001 已实现，见 `docs/plans/implementation_mem_001.md`。FLOW-002 未实现。2026-09-30 修订：补上到点判定的根因、能力矩阵只改原因、默认 20/30/5 回归、手动运行的 201、实现说明路径、安装说明位置，以及浏览器验收步骤。
> 来源：`tasks.yaml` 的 FLOW-001、MEM-001、FLOW-002；`docs/plans/plan_remaining_delivery.md` 对应三节；交付排期第 5 节 D1、第 6 节第 17 项。
> 日期：2026-09-30
> 截止：2027-01-15
> 依赖：FLOW-001 依赖已完成的 EVD-004。MEM-001 依赖已完成的 SEC-004。FLOW-002 依赖 FLOW-001。

一次只实现一条。顺序是 FLOW-001、MEM-001、FLOW-002。本文件写全每条的目标、现状、方案、非目标和验收。实现时沿用对应小节，不必再写第二份计划。

## 目标

1. 目标环境装上调度依赖后，工作流调度能启动，也能停止。
2. 对话消息数超过压缩阈值时发生压缩，最近几条原文仍留在窗口里，并在用例里写明留下的是哪些。
3. 工作流页在没有任务时显示空状态；执行失败时能看到失败原因。空列表和失败执行在页面上可以区分。

## 已核对的现状

核对日期：2026-09-30。拆分说明里「未安装调度依赖时调度测试不能运行」「失败态和空态未补齐」需要按下面的代码事实收窄。

调度：

- `croniter` 在 `pyproject.toml` 的可选依赖 `workflow` 里，版本范围 `croniter>=2.0.0`。主依赖列表里没有它。
- `requirements.txt` 第 39–40 行把同一条依赖注释掉了。`Dockerfile` 用 `pip install -r requirements.txt` 装镜像，所以镜像里没有调度依赖。
- `app/workflow/scheduler.py` 的 `_is_scheduler_runnable()` 在 `WORKFLOW_ENABLED` 为假，或导入 `croniter` 失败时返回假，`start_scheduler()` 不创建后台任务。`app/main.py` 在 lifespan 里调用 `start_scheduler()`。
- `app/core/config.py` 里 `WORKFLOW_ENABLED` 默认 `false`。`.env.example` 同样是 `false`。
- `tests/test_workflow.py` 的 `test_scheduler_runnable_and_start_stop` 把 `settings.WORKFLOW_ENABLED` 直接改成真，断言之后才改回假。断言失败时还原不会执行。缺 `croniter` 时「可运行」断言失败。这条测试只检查调度任务是否创建、是否结束，不检查到点是否调用执行。
- `_tick()` 调用 `next_run_time` 时不传基准时间，函数内部用当前时刻。根因是 `get_next()` 严格晚于基准，而判定要求 `nxt <= now`。把 `_tick()` 里的 `now` 传进 `after` 同样不会触发。只有更早的基准（例如 `after=last`）才会让下一次落在过去，同时让长期未运行的任务按扫描周期逐档补跑。2026-09-30 用 `croniter` 复算过：`04:46` 配 `*/5` 得到 `04:50`；带时区的每分钟表达式同样是下一次晚于当前时刻。开关关闭时 `_is_scheduler_runnable()` 直接返回，没有警告日志；只有缺少 `croniter` 才打警告。工作流接口在开关关闭时返回 503，页面标题是「工作流引擎未启用」。
- 能力矩阵把工作流调度标为 `Partial`，原因写成缺依赖导致启停测试失败。启停测试变绿后，状态仍是 `Partial`，原因改为：到点触发没有执行记录。第 7 节 2026-09-13 的全量跑次保持原数字，不改写成后来的结果。
- QA-005 记录的 8 个失败里包含同一条调度启停测试。其余 7 个失败（SSE、删文档清向量、BM25 全零）不在本批。QA-005 对缺外部依赖的处理是显式跳过；本批装上依赖并禁止把启停用例改成跳过，以后做 QA-005 时不得把这条改回 skip。多实例下同一周期重复触发由 `PWFL-003` 认领，依赖 `OPS-001`。`PWFL-001` 的非目标包含不改 cron 触发机制。到点不触发目前没有任务卡。交付第 17 项里，调度部分的完成标准是进程内调度任务能创建、能取消，不表示到点会执行。

记忆：

- `MemoryManager.manage()` 分三段：条数不超过 `window_size` 时原样返回；超过窗口但未超过 `compression_threshold`（或阈值为 0）时只做滑动窗口，不写摘要；超过阈值时保留最近 `keep_recent` 条，其余交给 `MemoryCompressor`。
- 默认配置与 `app/core/config.py` 一致：窗口 20、阈值 30、策略 `summary`、保留最近 5 条。`MEMORY_ENABLED` 默认 `true`。
- 对话入口在 `ChatService._build_memory()`。它直接 `MemoryManager(self.llm)`，用的是 `MemoryConfig()` 默认值，没有走 `get_memory_manager()`，因此运行中改配置项不会作用到这条路径。默认值目前和配置项相同。
- 没有 LLM 时压缩器仍返回带 `compressed_count` 的空摘要。有 LLM 时摘要来自模型输出。
- `tests/test_memory_smoke.py` 是模块级脚本，覆盖滑动窗口和 `should_compress`，没有「超过阈值后摘要出现、最近原文仍在」的 pytest 用例。`tests/test_context_merge.py` 测的是记忆与检索的预算合并，不是超窗压缩。

工作流页：

- `frontend/src/pages/Workflows.tsx` 在列表为空时已经用 `EmptyState`，标题是「还没有定时任务」。加载失败走 `ErrorState`；引擎返回 503 时标题是「工作流引擎未启用」。
- 执行历史弹层在没有记录时是另一块空状态「还没有执行记录」。`status=failed` 且 `execution.error` 有值时，用红色段落显示原因。
- 手动运行若 HTTP 失败，toast 标题是「执行失败」，正文是接口 `detail`。`POST /api/workflows/{id}/run` 成功时状态码是 201，正文里的 `status` 仍可以是 `failed`。前端把 2xx 当成成功，于是 toast 用成功样式，文案是「执行结束」，不带 `execution.error`。
- 列表接口的 `WorkflowOut` 没有最近一次执行。卡片上看不出上次是失败还是从未跑过。FLOW-002 的允许路径只有前端，本批不改列表接口。

## 已定的取舍

- `croniter` 仍不写入 `pyproject.toml` 的主依赖。`workflow` extra 保留，版本范围仍是 `croniter>=2.0.0`，供 `pip install -e ".[workflow]"` 使用。同时取消 `requirements.txt` 里该行的注释。镜像和 `pip install -r requirements.txt` 会装上它；只执行 `pip install -e ".[dev]"` 的环境不会。extra 留下来是为了和清单使用同一版本范围。缺包时调度器不启动，手动触发路径保持现有行为。实现说明里按这两条路径写，不把 extra 说成「装不装都不影响镜像」。
- `WORKFLOW_ENABLED` 默认保持关闭。本批证明的是：开关打开且依赖已安装时，调度任务能创建、能取消。不改 cron 扫描间隔，不改 `_tick()` 的触发判定。
- 超窗回归直接调用 `MemoryManager.manage()`，用测试里的假 LLM 返回固定摘要。不断言真实模型的摘要质量。消息条数固定为大于阈值，最近几条用可识别的正文。
- 压缩算法、窗口、阈值、`keep_recent` 保持现有实现。用例若证明最近原文被丢掉或超过阈值却没有摘要，才允许改 `app/memory/`，并在实现说明里写明改了哪一条。
- `ChatService` 未读取记忆配置项，本批不改对话服务，也不加「生产构造与 settings 一致」的断言。回归锁定的是显式传入 `MemoryConfig` 的 `MemoryManager`。默认数字目前与配置项相同。以后若改 `MEMORY_WINDOW_SIZE` 等配置，对话路径仍走 `MemoryConfig()` 默认值，那是后续任务。
- 默认 20 / 30 / 5 下，消息数在 21 到 30 时保留最近 20 条，超过 30 条时保留最近 5 条。这是现有分段。MEM-001 用这组默认数字各写一条回归把它锁住，不在本批做平滑过渡。`keep_recent` 按消息条数切，不按「一轮两条」来数。
- 工作流空列表沿用现有空状态，不重做。失败原因补在两处前端已有数据上：手动运行返回失败状态时展示 `error`；历史记录里状态为失败但 `error` 为空时，显示固定短句「执行失败，未返回原因」。空列表文案保持「还没有定时任务」，失败原因用危险色段落，不放进空状态组件。
- 卡片上不显示最近一次执行结果。那需要列表接口带上执行摘要，超出 FLOW-002 允许路径。本批不引入前端测试运行器。空列表、失败原因、历史空态这三处用页面核对，类型检查和构建只证明能编译。

## 需要先改的允许路径

实现某一条之前，先把下表路径补进该条 `tasks.yaml` 的 `allowed_paths`，再改业务文件。

| 任务 | 增加的路径 | 原因 |
|---|---|---|
| FLOW-001 | `requirements.txt`、`README.md`、`docs/plans/plan_d1.md`、`docs/plans/implementation_flow_001.md`、`docs/product/as-is-capability-matrix.md` | 镜像按 `requirements.txt` 安装；安装说明要写明调度依赖。实现说明单独成文。能力矩阵只改工作流调度行的原因，状态保持 `Partial`。`pyproject.toml` 已在允许路径内 |
| MEM-001 | `docs/plans/plan_d1.md`、`docs/plans/implementation_mem_001.md` | 实现说明单独成文，并回指本计划 |
| FLOW-002 | `docs/plans/plan_d1.md`、`docs/plans/implementation_flow_002.md` | 同上 |

`Dockerfile` 已安装 `requirements.txt`，不必改镜像文件。`.env.example` 的 `WORKFLOW_ENABLED=false` 保持不动。

## FLOW-001 调度在目标环境可启动

- 目标：装上 `croniter` 的目标环境里，调度器能启动和停止。
- 风险：L1。只增加可选依赖的安装入口。回退是恢复 `requirements.txt` 的注释，并在该环境卸载 `croniter`。不改已冻结的检索基线。
- 改动：
  - `requirements.txt`：取消 `croniter>=2.0.0` 的注释，与 `pyproject.toml` 的 `workflow` extra 使用同一版本范围。上一行「可选」改成：默认安装清单包含该依赖，`WORKFLOW_ENABLED` 仍默认关闭。
  - `README.md`「快速开始」：`pip install -r requirements.txt` 会装上 `croniter`；`pip install -e ".[workflow]"` 也会。默认 `WORKFLOW_ENABLED=false`。打开后进程启动时创建调度任务。到点是否执行不写成已完成。「开发」一节写明只装 `.[dev]` 不会带上 `croniter`。
  - `docs/product/as-is-capability-matrix.md` 第 2 节工作流调度行：状态保持 `Partial`，原因改为到点触发没有执行记录。第 7 节 2026-09-13 的全量数字不改，补一句此后启停用例在装有 `croniter` 的环境通过。
  - `pyproject.toml` 的 extra 已存在，版本范围不变则不改文件。
  - 在项目声明的 conda 环境 `ai-assistant` 中执行 `python -m pip install "croniter>=2.0.0"`，再跑测试。不为此重装整份 `requirements.txt`，也不构建镜像。安装动作和未验证项记在 `docs/plans/implementation_flow_001.md`。
- 测试：不放宽 `test_scheduler_runnable_and_start_stop`，不改成缺依赖就跳过。该用例用 `monkeypatch.setattr` 设置 `WORKFLOW_ENABLED`，开关回到用例开始前的值。`finally` 里调用 `stop_scheduler()`，避免断言失败后后台循环继续扫测试库。不在断言之后才写回 `False`。其余工作流用例保持现有断言。不新增「等待一个扫描间隔后出现 cron 执行记录」的用例，那属于到点触发，不在本批。
- 验收：`pytest tests/test_workflow.py -v` 通过，其中启停用例为通过。实现说明写明解释器路径，以及该环境中 `import croniter` 成功。完成标准是调度任务能创建、能取消。能力矩阵仍是 `Partial`。镜像未重建，写入未验证项。任务标 `done` 后不把 QA-005 标成完成。
- 非目标：不做拖拽编排，不新增一套运行时，不实现重试、超时、取消或并发限制（那些在 PWFL），不把 `WORKFLOW_ENABLED` 默认改成开启，不修改 QA-005 列出的另外 7 个失败用例。不改 `_tick()` 的触发判定，不用传入当前时刻或 `after=last` 当作本批补丁。不把工作流调度改成 `Implemented`。不在本批新增到点触发的任务卡。不把启停用例改成 `importorskip` 或跳过。

## MEM-001 超窗压缩回归

- 目标：固定长度的历史在超过阈值时产生摘要，最近若干条原文仍在 `recent_messages` 里。
- 风险：L0。优先只加测试。回退是删掉该测试文件。不改已冻结的检索基线。
- 改动：
  - 新增 `tests/test_memory_overflow.py`。消息正文带序号，按条计数。`keep_recent` 是消息条数，不是对话轮数。
  - 默认配置对照，锁住 21–30 段：总条数 25，窗口 20、阈值 30、保留 5。只滑动窗口，`is_compressed` 为假，`recent_messages` 按顺序等于最后 20 条。
  - 默认配置超阈：总条数 31，同一组 20 / 30 / 5。假 LLM 的异步 `chat(messages, options)` 返回固定非空摘要，并记下用户提示。断言：`is_compressed` 为真；`snapshot.summary` 等于该摘要；`snapshot.compressed_count` 等于被压缩的 26 条；`recent_messages` 按顺序等于最后 5 条。序号 01–26 进入压缩输入，且不在 `recent_messages` 里。序号 27–31 不在压缩输入里。
  - 两条用例的文档字符串写明总条数、窗口、阈值、保留条数，以及留下的正文序号。
  - 不另写一套更小的自定义窗口来代替上述两段。未达阈值的对照就是 25 条那条，避免把滑窗误判成压缩。
- 只有上述断言失败且原因在 `app/memory/` 时，才改压缩实现。改动限于让这组固定输入满足上述断言。
- 验收：`pytest tests/ -k "memory or context" -v` 通过，且超窗用例的失败输出或文档字符串能看出保留了哪些消息。
- 非目标：不做长期记忆、跨会话记忆、夜间蒸馏。不改 `tests/test_context_merge.py` 的预算断言，不改检索实验。不改 `should_compress()` 与 `manage()` 在阈值为 0 时的差异。不把 `ChatService` 改成读取配置项。不改「超过阈值后保留条数从窗口大小降到 `keep_recent`」这段现有行为，不另做平滑过渡。

## FLOW-002 工作流页失败态与空态

- 目标：没有任务时仍是空状态；一次失败的执行能在页面上看到原因；两种情况的文案和样式不同。
- 风险：L0。只改工作流页。回退是还原 `frontend/src/pages/Workflows.tsx`。
- 改动，均在 `frontend/src/pages/Workflows.tsx`：
  - 空列表保持现有 `EmptyState`（「还没有定时任务」）。
  - `handleRun` 在 201 返回体的 `status === 'failed'` 时改用失败 toast，正文优先用 `execution.error`。没有 `error` 时正文用「执行失败，未返回原因」。成功状态仍用成功 toast。HTTP 4xx/5xx 仍走现有的失败 toast，正文用接口 `detail`。
  - `ExecutionRow` 在 `status === 'failed'` 且 `error` 为空时，仍渲染危险色段落，正文为「执行失败，未返回原因」。已有 `error` 时继续显示原文。
  - 执行历史为空时保持「还没有执行记录」，不把失败行渲染成这块空状态。
- 验收：
  - `WORKFLOW_ENABLED=true` 且当前租户没有任务时，看到「还没有定时任务」，看不到失败原因段落。
  - 开关关闭时仍是「工作流引擎未启用」，不显示成空列表。
  - 手动运行返回 201 且 `status` 为 `failed` 时，提示里有失败原因。桥接抛错可以得到这种响应。`error` 为空的短句要另有一条失败历史（库里插入或已有记录），打开执行历史能看到「执行失败，未返回原因」。
  - 历史中一条带 `error` 的失败记录能看到原文；没有记录时仍是「还没有执行记录」。
  - `cd frontend; npm run typecheck` 与 `cd frontend; npm run build` 通过。
  - 浏览器按上面三态核对。没有可用前端测试运行器，不为本批新增组件测试框架。
- 非目标：不做新的工作流类型，不做六态全集，不改列表接口，不在卡片上汇总最近一次执行，不做重试按钮。不新增 vitest 或其它前端测试运行器。`error` 为空且状态为失败时仍要渲染原因短句，不保持「没有 error 就不渲染」的现状。

## 验证命令

按实现顺序，在 conda 环境 `ai-assistant` 的解释器上执行（`D:\install\anaconda3\envs\ai-assistant\Scripts`）。

```powershell
pytest tests/test_workflow.py -v
pytest tests/ -k "memory or context" -v
cd frontend; npm run typecheck
cd frontend; npm run build
```

FLOW-001 另记：该解释器中 `python -c "import croniter"` 退出码为 0。

## 自查

对照了 `app/workflow/scheduler.py`、`pyproject.toml`、`requirements.txt`、`Dockerfile`、`tests/test_workflow.py`、`app/memory/manager.py`、`app/memory/compressor.py`、`app/services/chat_service.py`、`frontend/src/pages/Workflows.tsx`。

- 启停测试失败的直接原因是目标安装清单没有 `croniter`。本批补安装入口，并用 `monkeypatch` 或 fixture 还原开关。不重写调度循环。
- 到点不触发是另一处缺陷：循环可以活着，但 `_trigger()` 不会被调用。把当前时刻传进 `after` 也不够。本批不修。收尾时工作流调度保持 `Partial`，只改矩阵里的原因。`after=last` 会让长期未运行的任务按扫描周期逐档补跑，不能当成本批补丁。
- 拆分说明写「失败态和空态未补齐」。空列表、加载错误、历史空态、带 `error` 的失败行已经在页面上。剩下的缺口是：成功样式的 toast 吞掉了失败原因，以及失败但无 `error` 时没有原因文字。
- MEM-001 允许改 `app/memory/`，但现有三段逻辑已经保留最近消息。回归用默认 20 / 30 / 5 锁住 25 条滑窗和 31 条压缩。不改对话服务去读配置项。
- FLOW-002 若要做到卡片上显示上次失败，必须改工作流 API，当前允许路径不含后端。计划把这条留在本批之外。失败态的验收是页面核对，不新增前端测试框架。
- QA-005 与 FLOW-001 共用调度启停用例。本批让该用例通过后，不把 QA-005 标成完成，也不把用例改成跳过。

实现前若允许路径仍缺上表中的文件，先改 `tasks.yaml` 再动那些文件。
