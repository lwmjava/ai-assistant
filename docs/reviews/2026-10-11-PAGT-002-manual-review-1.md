# PAGT-002 人工审核验收结论（第 1 轮）

- 卡编号：PAGT-002「工具十要素契约：权限、风险分级、超时、幂等、审计」
- 审核 Agent：独立人工审核 Agent（未参与本卡实现、计划审查与入口级反例审查）
- 审核日期：2026-10-12（轮次：第 1 轮人工审核）
- 分支：`codex/pagt-tool-polish`；HEAD `e2a3b2f`；工作区为本卡未提交 diff
- 审核方式：从原始契约（tasks.yaml 4616–4670 行）独立通读最终 diff、计算文件指纹、用项目解释器独立复跑关键验证、自写脚本从真实入口复验业务行为；未修改任何业务代码/测试/既有 docs/tasks.yaml，未做 git 提交

## 1. 原始契约逐项核对结果

| 原始要求 | 核对结论 | 证据 |
|---|---|---|
| deliverable① 每工具声明权限/风险/副作用/超时/幂等 | **通过** | `contract.py` ToolContract 含 version/permission/risk/side_effect/timeout_seconds/max_retries/idempotent/conflict_policy/error_model/audit，对应 AGENTS.md §7 十要素；6 个默认工具全部显式挂契约（builtin.py:138-249、web_search.py:253-261、file_ops.py:242-249/357-373） |
| deliverable② 工具调用写审计事件 | **通过** | base.py:175-197 `_record_audit()`；models.py 新增 `AuditAction.TOOL_CALL="tool_call"`（git diff -w 语义仅 +3 行） |
| deliverable③ file_ops 写路径按 L2 契约交付、默认关闭、未批准不开放 | **通过** | file_ops.py:254-266 三闸（settings.FILE_OPS_ENABLED + 环境变量 FILE_OPS_WRITE_ENABLED=="true" + 有效根）；L2 契约 authenticated=True/allowed_roles={system_admin,tenant_admin}/tenant_scoped=True；开关关时工厂返回 None 不注册 |
| acceptance① 注册表可输出每工具十要素 | **通过** | base.py:104-132 `describe_contracts()`；测试 `test_describe_contracts_covers_every_default_tool` 遍历断言无 "missing" |
| acceptance② 一次调用产生一条可查询审计记录 | **通过** | 我的增量复验：单次 registry.run(calculator) 前后审计行数差恰好 +1，绑定真实 user_id/tenant_id，resource_type="tool"，details={"risk","allowed","reason","latency_ms"} 不含 arguments/密钥 |
| acceptance③ L2 及以上风险工具默认保守执行 | **通过** | base.py:150-173 `_denied_reason`：匿名(dev ContextVar 空)/MEMBER 角色调写被拒且不落盘；L3 在 register() 期即抛 ValueError（base.py:84-85） |
| acceptance④ file_ops 写默认关闭且需显式开启 | **通过** | 我的复验：默认装配无 file_ops_write；三闸全开后写工具才注册 |
| non_goal：不实现审批流 UI | **遵守** | 无 UI 改动 |
| non_goal：不改既有工具业务行为 | **遵守** | 基线 70 passed；calculator 成功路径观测格式 `[calculator] 参数=… 结果=42` 与我复验一致 |
| non_goal：L3 能力不注册成工具 | **遵守** | 注册期强制拒绝，非约定 |
| 用户拍板：运行时 RBAC 强制校验、调用前拦截、L2+ 默认保守 | **遵守** | run() 入口先 `_denied_reason` 门控后执行；伪造身份参数（带 tenant_id/user_id）即拒 |

## 2. 关键文件指纹（git hash-object，当前工作区）

| 文件 | 当前 blob 指纹 | 反例审查 §2 修复前指纹 | 变化是否可解释 |
|---|---|---|---|
| app/agents/tools/contract.py | `e743befd2adbeb115333a0573724b2cd377d977c` | 同 | 未变（修复回路未触及契约模型）✓ |
| app/agents/tools/base.py | `054e2adbc6188606b3d2d48a079766c408673fe3` | `b4edb37c…` | 变（R2-P2-2 注释修正，审查 §8.1 已登记）✓ |
| app/agents/tools/file_ops.py | `9681adf6dab6a8a2053eb015dae429968c038b086` | `78ec0637…` | 变（P2-1 先校验后开文件修复）✓ |
| app/audit/models.py | `0826db2eaf90b9f7a0102f25ab6e06b17b13cef7` | `ef480cc7…` | 变（P3-1 行尾还原；git diff -w 实证语义仅 +3 行）✓ |
| tests/test_tool_contract.py | `39c65132d73abe8244e3e985cb4a75c4eac864c5` | `0de41177…` | 变（修复回路期间整理；当前 8 用例与实现说明一致）✓ |
| tests/test_tool_audit.py | `f03b6c320ef92f44cdfb8fcf7b9c4723af085541` | `92b1da91…` | 变（新增空壳反例 `test_write_oversize_leaves_no_empty_shell_and_later_legal_write_succeeds`）✓ |
| builtin.py / web_search.py | `ddefe55c…` / `a2b4b625…` | — | 契约挂载，diff -w 仅新增 contract 段 ✓ |

说明：关闭对账记录本身未列指纹（其证据位置以用例名引用）；指纹差异全部落在审查 §8 修复回路已登记的改动上，无无法解释的漂移。

## 3. 实际复跑命令与退出码（项目解释器，隔离 basetemp）

| 命令 | 退出码 | 结果 |
|---|---|---|
| `pytest tests/test_tool_contract.py tests/test_tool_audit.py --basetemp=data/pytest-tmp/manual-pagt002-new` | 0 | 15 passed / 3.56s |
| `pytest tests/test_tools.py tests/test_audit_smoke.py tests/test_pagt001_tools.py --basetemp=.../manual-pagt002-base` | 0 | 70 passed / 3.65s（基线无回归） |
| `pytest -k "tool or audit" --basetemp=.../manual-pagt002-k` | 0 | 152 passed, 1580 deselected / 21.69s |
| `ruff check . --no-cache` | 0 | All checks passed! |
| `mypy app/` | 0 | Success: no issues found in 195 source files |

与关闭记录 §3 完全一致。全程使用测试库 `data/test_ai_assistant.db`，未触碰生产库 `data/ai_assistant.db`。

## 4. 自选业务操作复验（自写脚本，真实入口 registry.run，已删除脚本）

1. 真实 tenant_admin 身份经 `registry.run()` 调 calculator：输出 `[calculator] 参数={'expression': '6*7'} 结果=42`；**单次调用审计增量恰好 1 行**，user_id/tenant_id 绑定本次调用身份，details 无 arguments/密钥 — PASS。
2. 默认装配下 `file_ops_write` 不在注册表 — PASS；三闸全开后注册成功 — PASS。
3. 三闸全开但 ContextVar 空（匿名/dev）调写：返回「调用未授权」，临时目录无文件 — PASS。
4. tenant_admin 写新建文件成功、内容正确；同路径二次写返回 conflict、内容不被覆盖 — PASS。
5. 超限写（500 字符/max_bytes=10）返回 `content_too_large`、磁盘无 0 字节空壳、随后同路径合法写成功 — PASS（P2-1 修复有效）。
6. 构造 L3 契约工具 register：抛 ValueError 且不入注册表 — PASS。
7. `describe_contracts()` 覆盖全部默认工具、十要素无 missing — PASS。

（首次脚本曾因未先清理既有基线测试残留的 tool_call 行而误报「3 行审计/身份为空」；改用增量差值法复验后确认为我脚本自身隔离问题，非产品缺陷——既有 test_tools.py 管线循环经 run() 会写 tool_call 审计但不清理，属既有测试卫生的 P3 级观察，不阻断本卡。复验后已清理测试库。）

## 5. 关闭对账记录核对结论

- 逐项覆盖：closure §1 把原始契约每一项映射到证据类别（全部 E2）↔ 结果（全部通过）↔ 证据位置（具体用例名）；§2 五类失败边界各有具名反例；§3 退出码汇总与我独立复跑一致。
- 用例名与实际文件核对：`test_describe_contracts_covers_every_default_tool`、`test_tool_call_writes_one_audit_record_without_arguments`、`test_unlisted_contract_none_tool_runs_and_is_audited`、`test_write_oversize_leaves_no_empty_shell_and_later_legal_write_succeeds` 均真实存在——审查 §8 残留的用例名漂移（P3-余1）已在关闭记录中订正。
- 未验证项原样列出（closure §4）：sync 沙箱合同级超时不可强制中断、allowed_roles 矩阵双轨一致性、MCP 工具契约化（链 PMCP 族）、PostgreSQL 下审计写入 E1。四项均如实标注为未验证/已知限制，未冒充通过。
- 未见「未验证也关闭」或「从 A 推导 B 可用」的伪证据；§6 结论正确地保持「等待人工审核」姿态。

## 6. DoD 核对（AGENTS.md §13）

- 验收标准有可复现证据：是（E2 测试 + 我独立复跑 + 自选入口复验）。
- diff 未超范围：git status 改动全部落在 allowed_paths；无 config.py/deps.py/security.py/chat.py；既有测试文件零修改。
- 既有工具对外行为未变：基线 70 passed，观测格式不变。
- 权限/租户/失败路径已验证：匿名/MEMBER/admin/伪造身份/跨租户隔离/超时/异常仍审计/审计降级均有测试与我的复验。
- 文档同步：计划、实现说明、关闭对账、两轮审查均落盘；tasks.yaml 状态更新按流程由主 Agent 在本验收后执行（预期行为）。
- 未验证项与残余风险已记录：closure §4 + 本结论 §5。

## 7. 残留观察（均为 P3 级，不阻断）

1. `web_fetch` 在 builtin.py 声明为 L1，计划 §3.1 对照表写 L0；本实现 L1 契约未加角色门（allowed_roles=None），无行为差异，属文档表格级漂移。
2. contract=None 工具（MCP 启用时注入）按「放行+必审计 risk=unlisted+describe 标 missing」处置，非 fail-closed；默认 MCP_ENABLED=False 无实际暴露，已登记为「启用 MCP 前必须先补契约」运维前置（链 PMCP 族卡），审查 R2-P2-2 方案 B 可接受边界。
3. run() 内 `_resolve_role()` 对 User 表只读主键直查（agents 层 Brownfield 偏差），已最小化为仅 allowed_roles 非 None 时触发、失败 fail-closed，后续由身份上下文注入 role 消除。
4. 既有 test_tools.py 管线循环经 run() 产生 tool_call 审计行但不自行清理；本卡新测试 fixture 已在 setup 删除该类行，测试结果不受影响，建议后续测试治理时顺手清理。

## 8. 人工审核验收结论

**通过。**

理由：原始契约三项 deliverable 与四项 acceptance 全部有 E2 隔离集成证据支撑并经我独立复跑与自选入口复验确认；L3 注册拒绝、L2 三闸默认关闭、create-only 幂等、审计绑定真实身份且不落 arguments/密钥均为真实代码行为而非声明；关闭对账记录逐项映射且未验证项如实列出，无伪证据；diff 范围纪律良好，既有工具行为零回归；两轮审查发现的 P2（空壳文件、contract=None 语义）均已修复并复核。无 P1/P2 阻断问题。
