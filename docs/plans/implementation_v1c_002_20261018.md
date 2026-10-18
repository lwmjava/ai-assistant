# V1C-002 统一工具执行入口实现说明

日期：2026-10-18。状态：实现与自动验收已完成，任务保持 `in_review`。用户授权按任务交付技能开发本卡，没有把人工业务验收交给 Agent 代为关闭，也没有授权提交或推送。

## 实际行为

默认自研管线和工具短路径执行工具时都进入已有 `ToolExecutor`。短路径在创建管线后用同一注册表注入执行器。管线若还没注入，行动循环也会构造这个执行器，不再调用不含有限重试和严格参数校验的 `ToolRegistry.run`。

同一探测工具在短路径、显式注入管线和未注入回退上：

- 未认证、缺少租户、伪造身份参数、角色已撤下时不执行，审计为拒绝。
- 严格校验失败不调用函数。
- 超时和连接失败按契约做有限重试；非幂等连接失败不重试；一次逻辑调用一条审计。
- 非有限计算器结果保持错误终态。
- 相同参数不重复执行。两条已支持路径都在 2 轮后停止。短路径的轮次上限仍是 2，这是原有有界策略，不是另一套执行器。
- 取消会向上传播，不留下成功审计。

允许调用的工具名单和短路径轮次上限仍由原路径决定。Supervisor 不注入。未治理 MCP 工具的契约缺口没有补上。内置工具默认不重试、不开启严格校验，成功观测格式不变。

## 代码位置

- `app/agents/fast_path.py`：工具短路径注入 `ToolExecutor`。
- `app/agents/pipeline.py`：`_execute_tool` 在缺少执行器时构造它；构造函数注释改为与该回退一致。
- `app/services/chat_service.py`：更正运行时注入说明。
- `tests/test_tool_path_contract.py`：两条入口和未注入回退的契约与失败边界。
- `docs/ai-prompts/evals/tool-path-contract-v1.yaml`：Smoke/Adversarial，不是 Gold。
- `docs/adr/0010-agent-runtime-state-boundary.md`：2026-10-18 补记。
- `docs/plans/plan_v1c_002_20261018.md`：实施计划。
- `docs/reviews/2026-10-18-v1c-002-entry-review.md`：独立入口反例。
- `docs/reviews/2026-10-18-v1c-002-manual-review-1.md`：未参与实现和入口审查的人工审核。结论不把任务标成完成。

## 验证

基线 HEAD `d881ca32a4da10440138b61e0c828ab5b68e9d2f`，分支 `codex/v1c-001-lazy-retrieval`。解释器 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`，Python 3.12.0。测试库 `data/test_ai_assistant.db`。第一次使用默认临时目录时，`data/pytest-tmp/run` 被拒绝访问，774 项在 setup 报错；该结果不作为业务失败。随后两次使用独立临时目录。

最终候选指纹：

| 文件 | SHA256 |
|---|---|
| app/agents/fast_path.py | 64e1730f3b9ca973b47a5a7fc775b9b95a31d0be9a1529db4a702a1282d7cc83 |
| app/agents/pipeline.py | 3c8d4ab2d571dc2ba82e6ba06cbfd2e17204e2162340acc56e9d2feb3ad049f8 |
| app/services/chat_service.py | 964c27fad78663efeb6965b272ad11e6c3d1c1c0c501fa36d3e29c7fdd3e096e |
| tests/test_tool_path_contract.py | b5f850484efadb3dbda732c9b803303aec69b43f6d41d12ecf0109c8458cd8a1 |
| docs/ai-prompts/evals/tool-path-contract-v1.yaml | 48abff43477a3f9f73df730c57c48fe68f5d08dc5786596089ad98dd516004f2 |
| docs/adr/0010-agent-runtime-state-boundary.md | ebde606654aa36832c2b05b1f7fb7e2f814c0913c73e28d8a7b18eaa75573541 |

命令与结果：

- `python -m ruff check .`：退出码 0。
- `python -m mypy app/`：退出码 0，213 个源文件。
- `python -m pytest tests/test_tool_path_contract.py -q`：退出码 0，40 passed。
- `python -m pytest -q --tb=line --basetemp data/pytest-tmp/v1c002-full2`：退出码 0，2704 passed，3 skipped。这次在构造函数注释改正之后。
- 独立入口审查脚本退出码 0，无未修复 P1/P2。见入口审查文件。审查发生在注释改正之前；改正只改注释，不改变已测执行次数、状态和审计。

前端源码、接口契约和构建没有改，前端四门不适用。没有新增进程或平台专用分支。Ubuntu CI 未对这个工作区运行。

## 关闭对账

| 原始要求 | 证据类别 | 结果 | 位置 |
|---|---|---|---|
| 默认管线与工具短路径复用受控执行器 | E2 | 通过 | `tests/test_tool_path_contract.py` 的 fast/wired/unwired；入口审查三条反例 |
| 身份、参数、超时、有限重试、预算、审计一致 | E2 | 通过 | 同上，40 项契约测试 |
| 撤权、取消、重复调用、非法参数、执行失败 | E2 | 通过 | 角色改写后新会话拒绝；取消无成功审计；重复指纹；严格缺参；超时与连接失败 |
| 不扩大权限 | E2 | 通过 | 拒绝路径函数零调用；diff 未改权限声明 |
| 不把接线说成 MCP 治理完成 | 不适用 | 未做 | 未改 MCP。这是非目标 |
| Supervisor 受控执行 | 不适用 | 未做 | 属于 V1C-004 |
| Ubuntu CI 与最终提交绑定 | E4 | 未验证 | 工作区未提交，无 Linux 运行 |
| 用户业务验收 | E4 | 未验证 | 用户未委托 Agent 代为关闭 |

未验证项必须保留：最终提交的 Ubuntu CI；用户对本卡行为的确认。没有真实 Embedding 或生产工具调用，本卡也不要求检索质量。

## 没做的事

未改 Supervisor、技能接线、MCP 契约、工具权限、轮次上限数值、数据库和前端。未提交、未推送。回滚方式是去掉短路径注入和 `_execute_tool` 的执行器回退，无数据要恢复。
