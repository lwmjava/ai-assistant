# V1C-002 独立入口反例审查

日期：2026-10-18。身份：独立入口审查，未参与本卡实现，不采信实现者摘要。审查范围只判断有没有未修复的业务缺陷：P1 指契约不成立，或出现越权、错误终态；P2 指已支持路径行为分岔，但还不到整卡失败。本报告不关闭任务，不改业务代码，不改 `tasks.yaml`，不提交。

## 阅读范围

对照原始 `tasks.yaml` V1C-002、`docs/plans/plan_v1c_002_20261018.md`、ADR-0010 的 2026-10-18 补记，以及下列实现：

- `iter_fast_path` 的工具分支：创建管线后用同一注册表构造 `ToolExecutor`，轮次上限仍是 2，允许名单来自 `route.tool_names`。
- `AgentPipeline._execute_tool`：注册表存在但未注入时，回退构造同一个 `ToolExecutor`，再把 `ExecutorResult.status` 写回 `ToolOutput`。
- `AgentPipeline._iter_action_loop` 与 `_reject_tool_call`：允许名单、重复指纹和非法调用在进入执行器之前结束。
- `ToolExecutor.execute`：身份与权限走注册表，超时和有限重试在执行器内，一次逻辑调用写一条审计。本卡未改这个文件。
- `ChatService._wire_runtime`：默认管线在 `tools` 存在时注入 `ToolExecutor`；短路径在自己的管线上注入；Supervisor 仍不注入。

实现者测试 `tests/test_tool_path_contract.py` 已覆盖抛异常重试、超时、严格参数、撤权、重复指纹，以及取消落在工具函数内部。下列三例不在该文件中。

基线 HEAD `d881ca32a4da10440138b61e0c828ab5b68e9d2f`。候选是该 HEAD 加工作区，不是新提交。解释器 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`，Python 3.12.0。数据库钉在 `sqlite:///./data/test_ai_assistant.db`，脚本在导入前隔离仓库 `.env`；若 URL 不是测试库则拒绝运行。

当时工作区与本卡相关的文件：`app/agents/fast_path.py`、`app/agents/pipeline.py`、`app/services/chat_service.py`、`docs/adr/0010-agent-runtime-state-boundary.md`、`tasks.yaml` 已修改；`tests/test_tool_path_contract.py`、`docs/plans/plan_v1c_002_20261018.md`、`docs/ai-prompts/evals/tool-path-contract-v1.yaml` 未跟踪。`app/agents/tools/executor.py` 的 SHA256 为 `dfd8203bf477ad0709a0fcbdd081c75a2313eb8a3756aa3135334424c3f233e6`，与计划所记执行器指纹一致。

## 反例与结果

脚本：`data/pytest-tmp/v1c002-review/run_counterexamples.py`。入口是 `iter_fast_path`，以及显式注入（wired）和未注入回退（unwired）的 `AgentPipeline._iter_action_loop`。每条路径单独跑。审计资源名使用 `v1c002rev_soft`、`v1c002rev_cancel`、`v1c002rev_outside`，运行结束已删除这些行。

命令：

```text
D:\DepTooL\anaconda3\envs\ai-assistant\python.exe data\pytest-tmp\v1c002-review\run_counterexamples.py
```

工作目录为仓库根。退出码 0。结果文件 `data/pytest-tmp/v1c002-review/counterexamples.json`。未使用被锁的 `data/pytest-tmp/run`。

### 1. 返回 ToolOutput(status=error)，不抛异常

工具幂等且 `max_retries=2`，直接返回 `ToolOutput("下游业务拒绝", status="error", error_code="business_reject")`。

三条路径相同：函数只调用 1 次，不因返回的错误状态再重试；`tool_results` 最后一项 `status=error`，正文含「下游业务拒绝」；流式 `tool.result` 的 `ok=false`、摘要为「执行失败」；只用 1 轮。审计各 1 条，`allowed=true`、`reason=null`，键只有 `risk/allowed/reason/latency_ms`。

这不是成功终态。执行器把 `allowed=true` 且 `reason=null` 记为「已放行并实际执行」，抛出的瞬时错误也是这一形状；失败终态在观测状态和 `tool.result`。`error_code` 没有传到管线里的 `ToolOutput`，三条路径都是 `null`，与绑定执行器后的 `Registry.run` 一样只保留 `status`。路径之间没有分岔。

### 2. 取消发生在重试间隔

第一次调用抛出 `ConnectionError`，随后把执行器模块里的 `asyncio.sleep` 换成一直等待的事件，确认已经进入重试间隔后再取消任务。工具函数本身不再阻塞。

三条路径相同：异常类型为 `CancelledError`，任务没有正常结束；函数只调用 1 次，没有开始第二次尝试；审计 0 条，没有成功审计，也没有 `tool.result`。短路径在传播取消前多发出 `node.finished`，节点 `action`、状态 `cancelled`。这是 `iter_fast_path` 的流式包装，直接调用行动循环时不经过这层包装；工具的尝试次数、取消传播和审计规则仍然一致。

### 3. 允许名单之外的已注册工具

注册表里有探测工具，允许名单只有 `calculator`。模型仍点名探测工具。若函数被调用会留下计数。

三条路径相同：函数调用 0 次；没有 `tool.call` / `tool.result`；审计 0 条；观测文本为「[工具调用失败] 当前步骤不允许调用该工具。」。该文本是普通字符串，没有 `status` 属性，因为名单拒绝发生在执行器之前。拒绝证据是这段文本加上未执行、未写审计。三条路径都消耗 1 轮，这是行动循环的既有轮次计数，不是执行器分岔。

## 结论

未发现未修复的 P1 或 P2。

三条已核对入口上，同一探测工具的执行次数、错误状态、取消传播、名单拒绝和审计形状一致。名单外调用没有执行，也没有 `allowed=true` 审计。重试间隔中的取消向上传播，且不留下审计。返回 `ToolOutput(status=error)` 时，终态是 `error` / `tool.result ok=false`，不会被记成执行成功。

`AgentPipeline.__init__` 仍写着未注入时走 `Registry.run`。`_execute_tool` 的实际回退已经改为构造 `ToolExecutor`。这是注释滞后，三条入口的运行结果并不因此分岔，不升为 P1/P2。

本审查没有重跑实现者的契约测试，也没有跑全量 pytest、Ruff、mypy 或 Linux CI。那些门禁仍由主线负责。业务或测试输入再变，本次结果就失效。
