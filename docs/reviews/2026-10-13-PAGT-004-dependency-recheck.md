# PAGT-004 前置卡当前工作区证据复核

- 日期：2026-10-13。
- 复核 Agent：`/root/pagt004_dependency_check`，独立于 PAGT-004 实现。
- 前置卡：PAGT-002「工具十要素契约：权限、风险分级、超时、幂等、审计」。
- 目标：遵 task-card-delivery v1.3.0 的依赖链准入要求，在当前工作区核对证据，不凭 `status=done` 推断可用。
- 仓库：`E:\culture\SmartCustomerServiceSystem\ai-assistant`。
- HEAD：`a0d02eb411a53c3191e81f0fc99528ea60d0d3c4`。
- 工作区已有 PAGT-006 未提交修复；本复核不把这些改动计为 PAGT-004 交付。本 Agent 未改源码、测试或任务卡，仅在用户批准文档范围后写本报告，未提交、推送、合并、部署。

## 依据与复核范围

已读取 AGENTS.md、AI 辅助开发迭代指导、Harness 治理规范、tasks.yaml 中 PAGT-002/PAGT-004 原始契约、适用 Cursor rules（00、01、02、03、07、08、09、11）、用户指定的 task-card-delivery 技能，以及以下证据：

- `docs/plans/implementation_pagt_002.md`。
- `docs/plans/closure_pagt_002_20261011.md`。
- `docs/reviews/2026-10-11-PAGT-002-plan-review.md`。
- `docs/reviews/2026-10-11-PAGT-002-counterexample-review.md`，包含第 8 节修复复核。
- `docs/reviews/2026-10-11-PAGT-002-manual-review-1.md`。
- `docs/reviews/2026-10-12-PAGT-002-head-recheck.md`（历史参考，不能替代本次运行）。

技能 SHA-256：`97248DEC90E7B927DA655E6C336DCCD12AE4C634D3CC4FF9E8CC4E9DD806C2DD`。

## 当前代码事实与验收映射

| 原始前置要求 | 当前操作链与可观察行为 | 当前复核结果 |
|---|---|---|
| 每个默认工具声明十要素，注册表可输出契约 | ToolContract / ToolPermission 声明权限、风险、副作用、超时、重试、幂等、冲突、错误、审计、版本；default_tools 挂契约，describe_contracts 输出 | 声明在位，完整性测试通过；只覆盖默认工具，不扩张为 MCP 契约已完整 |
| 调用产生一条可查审计 | Registry.run 的 legacy 路径，及 PAGT-006 bound Executor 路径，共用 _record_audit；真实 SQLite 审计行绑定 user/tenant；details 四键不含 arguments | 工具/审计专项通过，包含一次调用、身份绑定、伪造参数拒绝、Executor 聚合审计回归 |
| L2 及以上默认保守执行 | _denied_reason 检查认证、租户上下文与角色；L3 注册期拒绝 | 拒绝和零文件副作用测试通过；不代表所有资源级权限已实现 |
| file_ops 写路径默认关闭，需显式开启 | FILE_OPS_ENABLED 默认 False；FILE_OPS_WRITE_ENABLED 和有效根共同控制注册；写契约 L2，匿名/成员拒绝；create-only，不覆盖 | 默认关闭、角色拒绝、越界拒绝、重复写不覆盖、超限无空壳及随后合法写均通过 |
| PAGT-004 复用受控工具执行 | 当前 Pipeline 使用既有 Registry/Executor；Executor 组合委托同一 RBAC 与审计 | 前置可供复用；后续结构化 Plan 不得绕过此入口 |

源码阅读为当前事实核对，必交付项的运行证据为下述 **E2 隔离集成**（真实工具/注册表/审计代码、测试 SQLite 库及临时文件）。历史审查为 E4 历史证据，仅用于追踪缺陷处置，不替代本次验证。

## 实际验证

运行前已通知主 Agent 暂停其他 pytest，运行结束后通知释放占用。全程独占串行，未与其他 Agent 并发访问共享测试库。明确指定项目解释器、native 后端及测试数据库，不使用 PATH 上的 base 环境。

```powershell
$env:RAG_BACKEND='native'
$env:DATABASE_URL='sqlite:///./data/test_ai_assistant.db'
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' -m pytest -k 'tool or audit' -p no:cacheprovider --basetemp=data/pytest-tmp/pagt004-dependency-check-20261013 -q
```

退出码 **0**；**171 passed, 1653 deselected, 1 warning in 15.88s**。无 failed、error 或 skipped。选择集包含 PAGT-002 原始专项 15 条及当前工具、审计、Executor 相关回归。

warning 为 `app/agents/tools/sandbox/sandbox.py:93` 使用 `ast.NameConstant` 的既有弃用提示，不构成业务断言失败。此次未运行全量 pytest、ruff、mypy、前端、真实模型或生产数据库；本报告只证明前置准入相关路径，不冒充 PAGT-004 最终验收。

## 当前工作区 SHA-256

旧 HEAD 报告中 contract/base 的指纹已因 PAGT-006 增加 strict_validation 与 Executor 委托发生变化。本表为本次实际读取工作区文件的 SHA-256；Executor 工作区修改是参数类型校验修复，不能只用 HEAD hash 代替。

| 文件 | SHA-256 |
|---|---|
| app/agents/tools/contract.py | D9CFB7A737B0F5C93760531E68D72D004A83815D71A3ED06858DC9C96D8D63F4 |
| app/agents/tools/base.py | 01A246EEA574FB5A560A75DD787A3208F796A04DE331B683E3DD14491D2E8F27 |
| app/agents/tools/executor.py | FE5390359CFE9731E4D593929CC14139121FDFACED9771000EB90CA65C48BF36 |
| app/agents/tools/builtin.py | 01CD92982763CBBA45EC94CE79D2CACEB2CC82BB77F9B4013ED4691E224D39B4 |
| app/agents/tools/file_ops.py | 9ECF5AAF34DAD6A9FE7196F02CD218688BB40CD6B0559BDF7F89B39C69DEEE3F |
| app/agents/tools/web_search.py | DDC434E86B2C945C8C151EB2977CA70A6759030AF3E2020F7027CED1B0083113 |
| app/audit/models.py | A941D550FFB092C05D7F2A2DBF07E16AD1B2C6CF270AFA4485E6577FD77A6388 |
| app/audit/logger.py | E3BCC267CAA3888DB010723A30991DA7626F7514F33384FE192C3A908FF73565 |
| app/core/security.py | BD7DAF3C479124A38F9F96927BCFE87A7101F81D3905E5E4AA8E4BEA9B1BD7F0 |
| app/core/log_context.py | ADA6E8CA154D4A6137F79DCB52E7A6F84F1DD29E4B669141358B91B1427F0E1E |
| tests/test_tool_contract.py | 047FD291FA53B5CC0F8456CFC68B87358CEB3F5AEA2E958267AA55F704F070BE |
| tests/test_tool_audit.py | 5A167879942C55720FCE7722998E0417C0ED19837934CD558310408050AB3CB9 |

## 未决审查核对

计划审查的 P2（contract=None 语义、agents 层只读查角色分层偏差）已有规格/偏差登记。入口反例审查发现的超限空壳 P2 已先校验长度再 O_EXCL；contract=None 注释 P2 按接受的方案 B 如实修正文案并登记 MCP 启用前补契约前置。该审查第 8 节明确修复复核通过，随后人工审核通过。当前源码与绿态回归符合上述处置。

未发现 PAGT-002 未决 P1/P2。历史关闭记录末尾保留等待审查措辞，是当时记录；随后独立复核及人工审核已经给出通过结论。本报告不把早期结论孤立解读为仍有开放 P2。

## 限制与准入结论

**PAGT-002 当前工作区证据复核通过，PAGT-004 可据此开工。** 该结论只限此次指纹和验收路径；若后续修改相关代码，关闭前需更新受影响证据。

以下仍是已登记边界，不得在 PAGT-004 实现说明中写成已闭合：

- contract=None 未治理工具仍放行并审计；MCP 默认关闭，启用前必须先补契约。此次通过不授权启用 MCP 或注册未治理写工具。
- agents 层按主键只读查角色仍属 Brownfield 分层偏差，角色集合与权限矩阵仍双轨，未证明矩阵化一致性。
- asyncio.timeout 对同步阻塞沙箱不能强制中断，不能把 async 超时专项推导为所有同步副作用均可取消。
- PostgreSQL 审计 E1、真实模型行为与多实例运行未验证；SQLite E2 不证明生产就绪或模型质量。
- 本次不实施 PAGT-004、不修改原任务边界、不清除其他卡工作区改动、不执行 Git 发布。
