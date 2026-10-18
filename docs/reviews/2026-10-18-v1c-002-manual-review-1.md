# V1C-002 最终人工审核 第 1 轮

日期：2026-10-18。身份：未参与本卡实现，也未参与已经落盘的入口反例审查（`docs/reviews/2026-10-18-v1c-002-entry-review.md`）。本轮只读任务原文、实现说明和指定调用点，并实跑指定契约测试。未改业务代码，未改 `tasks.yaml`，未提交。任务保持 `in_review`，不标 `done`。

**结论：抽查未发现未修复的 P1/P2。关闭对账没有把未验证项写成通过。Ubuntu CI 与用户业务确认仍须交给用户决定。**

## 原始契约

`tasks.yaml` 中 V1C-002 当前状态为 `in_review`。原始交付是：默认管线与工具短路径复用受控执行器；跨路径工具契约一致性测试。验收是：同一工具在已支持路径中的身份、参数、超时、有限重试、预算与审计规则一致；撤权、取消、重复调用、非法参数与执行失败有拒绝或终态证据。非目标是：不扩大工具权限；不将接线统一等同于 MCP 治理完成。

## 关闭对账

`docs/plans/implementation_v1c_002_20261018.md` 的关闭对账把交付、契约一致、失败边界和不扩大权限标为 E2 通过，并指向契约测试与入口审查。MCP 治理和 Supervisor 受控执行标为未做。最终提交的 Ubuntu CI、用户业务验收标为 E4 未验证。文首状态写明实现与自动验收已完成，任务保持 `in_review`，用户没有委托代为关闭，也没有授权提交或推送。

未验证项没有被写成通过。本审核没有重跑全量 pytest、Ruff、mypy，也没有重跑入口反例脚本，因此不把那些记录改成本轮亲自通过的证据。

## 抽查

HEAD `d881ca32a4da10440138b61e0c828ab5b68e9d2f`。实现说明所记六份文件的当前 SHA256 与该说明一致。`app/agents/tools/executor.py` 为 `dfd8203bf477ad0709a0fcbdd081c75a2313eb8a3756aa3135334424c3f233e6`，工作区未改这个文件，也未改 `app/mcp`。

- `app/agents/fast_path.py` 工具分支在创建管线后执行 `pipeline.executor = ToolExecutor(tools)`。允许名单来自 `route.tool_names`，轮次上限仍是 `_TOOL_ROUND_CAP = 2`。没有调用 `ToolRegistry.run`，没有改 MCP，没有放宽权限声明。
- `app/agents/pipeline.py` 的 `_execute_tool` 在已有执行器时直接使用；没有执行器时用同一注册表构造 `ToolExecutor`，然后调用 `executor.execute(call)`。允许名单、重复指纹和非法调用在 `_reject_tool_call` 中于执行前结束。身份拒绝仍在执行器内走注册表既有拦截。没有扩大权限，没有改 MCP。
- `ChatService._wire_runtime` 只在默认管线有工具时注入同一个 `ToolExecutor`；说明写明 Supervisor 仍不注入。本轮未把它当成新的权限或 MCP 变更。

## 本轮实跑

解释器：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`。工作目录为仓库根。未使用 `data/pytest-tmp/run`。

```text
python -m pytest tests/test_tool_path_contract.py -q --tb=line --basetemp data/pytest-tmp/v1c002-manual
```

退出码 0。统计：40 passed，3.31 秒。输出中没有 failed、error 或 skipped。

## 仍须用户决定

- 最终提交的 Ubuntu CI 未在本工作区运行。
- 用户尚未确认本卡业务行为，本审核不代为关闭。

没有未修复的 P1/P2 要退回实现。V1C-002 继续保持 `in_review`。
