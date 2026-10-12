# PAGT-002 实现说明：工具十要素契约（权限/风险分级/超时/幂等/审计）

- 卡编号：PAGT-002
- 分支：codex/pagt-tool-polish
- 实现日期：2026-10-11
- 前置：PAGT-001（done）
- 计划：`docs/plans/plan_pagt_002_20261011.md`（已按审查 P2-1/P2-2 修订）
- 审查：`docs/reviews/2026-10-11-PAGT-002-plan-review.md`（结论「需修订」，无 P0/P1）

## 1. 实际完成了什么

本卡为工具调用建立声明层十要素契约，并在执行咽喉（`ToolRegistry.run`）做运行时拦截、超时包裹与审计写入。不改既有工具的成功路径观测文本。

### 1.1 契约模型（新增 `app/agents/tools/contract.py`）

- `RiskLevel`：`L0/L1/L2/L3` 枚举（str Enum）。
- `ToolPermission`（frozen dataclass）：`authenticated`、`allowed_roles: frozenset[str] | None`、`tenant_scoped`、`resource`、`action`。
- `ToolContract`（frozen dataclass）：`version / permission / risk / side_effect / timeout_seconds / max_retries / idempotent / conflict_policy / error_model / audit`。对应 AGENTS.md §7 的十要素（权限、风险、副作用、超时、重试、幂等、冲突、错误模型、审计、版本）。

### 1.2 注册表执行咽喉（改 `app/agents/tools/base.py`）

- `Tool` 新增字段 `contract: ToolContract | None = None`。
- `ToolRegistry.register()`：拒绝 `RiskLevel.L3` 契约（抛 `ValueError`）——L3 能力在审批闭环存在之前不注册成工具。
- `describe_contracts()`：输出每个工具的十要素；`contract is None` 的工具字段显式标 `"missing"`，不静默跳过。
- 重写 `ToolRegistry.run()`：
  1. 从 `app/core/log_context.py` ContextVar 读取 `user_id`/`tenant_id`（dev 无认证模式为空）。
  2. 伪造身份拦截：`arguments` 含 `tenant_id`/`user_id` 键即拒绝（`forged_identity_argument`）。
  3. `_denied_reason()` 权限门：`contract=None` 按 L0 只读级保守放行（必审计、describe 标 missing）；`contract` 存在时按 `authenticated`/`tenant_scoped`/`allowed_roles` 校验，角色经 `_resolve_role()` 查库，失败按未授权（fail-closed）。
  4. `asyncio.timeout(timeout_seconds)` 包裹执行。
  5. `_record_audit()` 写一条 `TOOL_CALL` 审计；`details` 只含 `risk/allowed/reason/latency_ms`，**不落 arguments/密钥**；审计失败降级 structlog，不阻断业务。

### 1.3 审计动作（改 `app/audit/models.py`）

- `AuditAction` 新增 `TOOL_CALL = "tool_call"`。

### 1.4 内置工具挂契约（改 builtin.py / web_search.py / file_ops.py）

- 四个内置工具（calculator、code_sandbox、get_current_datetime、web_fetch）挂 L0/L1 契约；`default_tools()` 工厂按可用性加入 `file_ops_write_tool`。
- `web_search` 挂 L0 契约（timeout 取 `get_settings().SEARCH_TIMEOUT_SECONDS`）。
- `file_ops` 读工具挂 L0 契约；新增 L2 写路径：
  - `file_ops_write_availability()` 三闸：`settings.FILE_OPS_ENABLED` + 环境变量 `FILE_OPS_WRITE_ENABLED=="true"` + 有效根。
  - `_write()`：create-only，已存在即 conflict，不覆盖。
  - `file_ops_write_tool()`：L2 契约，`allowed_roles={system_admin, tenant_admin}`，`tenant_scoped=True`，`authenticated=True`。

## 2. 谁可以做什么 / 成功与失败行为

| 行为 | 成功 | 失败 |
|---|---|---|
| L0 只读工具（calculator 等） | 任意身份可执行，写一条审计 | 工具异常 → 返回观测错误文本，审计记 allowed=true |
| 伪造身份参数（带 tenant_id/user_id） | — | 拒绝 `forged_identity_argument`，写审计 allowed=false |
| 未认证调用要求 authenticated 的工具 | — | 拒绝 `authentication_required` |
| MEMBER 调 file_ops_write | — | 拒绝 `role_not_allowed`，不写文件 |
| 匿名/dev 调 file_ops_write | — | 拒绝（ContextVar 空），不写文件 |
| tenant_admin 调 file_ops_write | create-only 写入成功，返回 `"status":"ok"` | 同路径再写 → conflict，不覆盖；越界路径 → error，不写文件 |
| L3 契约工具注册 | — | 注册期抛 ValueError，不进注册表 |

## 3. 代码位置

- `app/agents/tools/contract.py`（新增）
- `app/agents/tools/base.py`（改：contract 字段、register 拒 L3、describe_contracts、run() 门控+超时+审计）
- `app/audit/models.py`（改：AuditAction.TOOL_CALL）
- `app/agents/tools/builtin.py`、`web_search.py`、`file_ops.py`（改：挂契约 + L2 写路径）
- `tests/test_tool_contract.py`（新增 8 用例）、`tests/test_tool_audit.py`（新增 6 用例）

## 4. 验证命令与结果（项目解释器）

解释器：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`

| 命令 | 退出码 | 结果 |
|---|---|---|
| `pytest tests/test_tools.py tests/test_audit_smoke.py tests/test_pagt001_tools.py --basetemp=.../pagt002-fix-base2` | 0 | 70 passed（起点基线） |
| `pytest tests/test_tool_contract.py tests/test_tool_audit.py --basetemp=.../pagt002-fix2` | 0 | 15 passed（本卡新增，含空壳反例） |
| `pytest -k "tool or audit" --basetemp=.../pagt002-fix-k2` | 0 | 152 passed, 1580 deselected |
| `ruff check . --no-cache` | 0 | All checks passed |
| `mypy app/` | 0 | Success: no issues found in 195 source files |

所有 pytest 使用隔离 `--basetemp=data/pytest-tmp/<唯一目录>`，未触碰生产库 `data/ai_assistant.db`；测试库 `data/test_ai_assistant.db`。

## 5. 审查缺陷登记（按轮次编号，避免混淆）

### 第一轮：计划审查（2026-10-11，结论「需修订」无 P0/P1）
- **R1-P2-1（contract=None 语义）**：未列举工具（MCP 启用时经 chat_service 进入注册表）的门控/审计/describe 语义。处置：放行、每次必审计（risk="unlisted"）、`describe_contracts` 显式标 `"missing"`。注意：无只读/写强制——未治理 MCP 写能力在匿名下也会执行。反例测试 `test_unlisted_contract_none_tool_runs_and_is_audited`。
- **R1-P2-2（agents 层直查 DB）**：`ToolRegistry._resolve_role()` 在 agents 层内 `Session(engine).get(User)` 直查数据库——属 AGENTS.md §5「agents 不得直接操作数据库」的分层偏差。Brownfield 妥协（身份上下文只带 user_id 不带 role；security.py 不在 allowed_paths）。最小化：主键只读、失败 fail-closed。后续由身份上下文注入 role 消除。

### 第二轮：入口反例审查（2026-10-11，2 个 P2 已修复）
- **R2-P2-1（空壳文件）**：`_write()` 原先 `O_CREAT|O_EXCL` 开文件、后校验长度，超限写残留 0 字节空壳并吞掉 `content_too_large`。已改为先 `encode()` + 长度校验、后开文件；错误码保留 `content_too_large`。新增反例 `test_write_oversize_leaves_no_empty_shell_and_later_legal_write_succeeds`。
- **R2-P2-2（contract=None 无只读强制）**：按方案 B 处置——不改门控（方案 A 会破坏既有管线/MCP 测试），修正 base.py 注释（不写「只读级」），登记「启用 MCP 前必须先为其补契约」为运维前置（链 PMCP 族卡）。
- **R2-P3-1（行尾噪声）**：还原 models.py 等文件 CRLF，`git diff -w` 仅剩真实变更。
- **R2-P3-2（用例名订正）**：见关闭记录 §1。
- **R2-P3-3（错误码泛化）**：`ValueError` 不再吞成 `access_denied`，保留具体码。

### P3（接受为约束，不改正方案）
- 写开关 `os.environ.get("FILE_OPS_WRITE_ENABLED")` 直读：`config.py` 不在 allowed_paths，未把开关加进 Settings。
- `allowed_roles` 硬编码与 `ROLE_PERMISSIONS` 矩阵双轨：security.py 不在 allowed_paths，未做矩阵化。
- 合同级 `timeout_seconds` 对 sync 沙箱不可强制中断：仅 `asyncio.timeout` 包裹 async 工具；同步阻塞调用无法被取消，记为已知限制。
- dev 无认证模式 ContextVar 空与合成 admin 并存：L2 写在 dev 一律拒（刻意保守，防误判）。

## 6. 明确没做的事（Non-goals）

- 不实现审批流 UI。
- 不改既有工具的业务成功路径观测文本。
- L3 能力未注册成工具（注册期即拒）。
- 不改 `config.py`/`deps.py`/`security.py`/`chat.py`/`app/mcp/`（不在 allowed_paths）。
- 未做 MCP 工具的契约化（属 PMCP 族后续卡）。
- 未把 allowed_roles 矩阵化到 ROLE_PERMISSIONS（后续治理）。

## 7. 回滚方式

回退本卡契约声明与审计写入的提交即可（git revert）；无数据库迁移、无配置新增、无生产副作用。`file_ops_write` 默认关闭，回滚后不影响既有读路径。
