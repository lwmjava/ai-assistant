# PAGT-002 当前 HEAD 证据复核结论

- 复核对象：前置卡 `PAGT-002`「工具十要素契约：权限、风险分级、超时、幂等、审计」
- 复核目的：作为 `PAGT-006` 开工/关闭的强制准入证据；结论独立得出，不只凭 `tasks.yaml` 的 `status=done`
- 复核日期：2026-10-12
- 复核人：独立 HEAD 证据复核 Agent（未参与 PAGT-002 计划编写、实现与此前评审）
- 仓库：`E:\culture\SmartCustomerServiceSystem\ai-assistant`（Windows / PowerShell）
- 本复核只读代码、实跑测试与读档；仅落本文件一个产物，未改任何源代码、未 git 提交/推送/合并

## 1. 复核范围与方法

逐项复核三类证据，均独立取得，不采信实现方自报：

1. **关键文件指纹**：对契约/注册表/内置工具/写路径/审计/测试文件计算 SHA-256（`Get-FileHash`），并对照 PAGT-002 声称交付（ToolContract 十要素、`ToolRegistry.run` 调用前 RBAC 拦截/超时/审计、`describe_contracts`、file_ops 三闸默认关闭）。
2. **验收路径实跑**：用项目规定 conda 解释器实跑 `pytest -k "tool or audit"` 与本卡新增测试文件；先读 `tests/conftest.py` 确认隔离测试库。
3. **未决 Review 扫描**：通读三份 PAGT-002 review 与关闭记录，核对 closure「无 P0/P1 残留、4 项 P3 观察已记录」断言是否与文档一致，排查未关闭的 P1/P2。

证据分级：代码事实为 E2（通读真实源码逐行核对）；测试为 E2（真实 conda 解释器 + 隔离测试库实跑）。

## 2. HEAD 与工作区状态

| 项 | 复核值 | 预期 | 结果 |
|---|---|---|---|
| HEAD | `0b08c2ee765d25b18e18a989fcdffb4ad3e35b30` | `0b08c2ee…` | 一致 |
| 分支 | `codex/pagt-tool-polish` | `codex/pagt-tool-polish` | 一致 |
| 工作区 | `git status --porcelain` 空（实跑测试前后各核对一次） | 干净 | 一致 |
| HEAD 提交 | `2026-10-12 22:55:15 +0800 feat(pipeline): 为工具调用增加预算上限并在耗尽后停止行动` | — | 该提交为后续卡 PAGT-003（工具调用预算），位于 PAGT-002 关闭之后 |

说明：PAGT-002 于 2026-10-11 关闭，其后 PAGT-003 已并入本分支 HEAD；因此当前测试计数较 PAGT-002 关闭记录略有增长（见 §4），属后续卡追加测试的预期漂移，非 PAGT-002 回归。

## 3. 关键文件指纹表（SHA-256，Get-FileHash）

| 文件 | SHA-256（当前 HEAD 工作区） | 与 PAGT-002 声称交付的代码事实对照 |
|---|---|---|
| `app/agents/tools/contract.py` | `46E46E0352BB092F397A8F45DE4F1EB914609E9B1B16ABD2F471D2F931DD5526` | 新增。`RiskLevel`=L0/L1/L2/L3；`ToolPermission`(authenticated/allowed_roles/tenant_scoped/resource/action)；`ToolContract` 十要素 version/permission/risk/side_effect/timeout_seconds/max_retries/idempotent/conflict_policy/error_model/audit —— 与声称一致 |
| `app/agents/tools/base.py` | `D207F1B99EC5E4FE31C9860B6B469CA476EF62815EA11A2DC5E58AE05D75ACCE` | 改。`Tool.contract` 可选字段；`register()` 拒绝 L3（:84-85 抛 ValueError）；`describe_contracts()` 逐工具输出十要素、contract=None 标 `"missing"`（:104-132）；`run()` 重写：log_context 取身份、伪造身份参数拦截、`_denied_reason` RBAC 门、`asyncio.timeout` 包裹、`_record_audit` 每次调用（含拒绝）写一条 TOOL_CALL、审计 details 仅 risk/allowed/reason/latency_ms 不落 arguments/密钥、审计失败降级不阻断业务 —— 与声称一致 |
| `app/agents/tools/builtin.py` | `01CD92982763CBBA45EC94CE79D2CACEB2CC82BB77F9B4013ED4691E224D39B4` | 改。`default_tools()`：calculator→L0、code_sandbox→L1、get_current_datetime→L0、web_fetch→L1；工厂 search/file_ops_read/file_ops_write 可用性非 None 才追加 —— 四个内置工具全部显式挂契约，与声称一致 |
| `app/agents/tools/file_ops.py` | `9ECF5AAF34DAD6A9FE7196F02CD218688BB40CD6B0559BDF7F89B39C69DEEE3F` | 改。读工具挂 L0；三闸 `file_ops_write_availability()`（settings.FILE_OPS_ENABLED + 环境变量 FILE_OPS_WRITE_ENABLED=="true" + 有效根，:254-266）；`_write()` 先 `encode("utf-8")`+长度校验（:290-292）**后** `os.open(O_CREAT|O_EXCL)`（:294）——P2-1 空壳修复在位，超限不落盘；create-only、FileExistsError→conflict 不覆盖；ValueError 保留具体错误码；写工具 L2 契约 authenticated=True/allowed_roles={system_admin,tenant_admin}/tenant_scoped=True（:357-373）；开关关时工厂返回 None 不注册 —— 与声称一致 |
| `app/agents/tools/web_search.py` | `DDC434E86B2C945C8C151EB2977CA70A6759030AF3E2020F7027CED1B0083113` | 改。挂 L0 契约，`timeout_seconds=float(SEARCH_TIMEOUT_SECONDS)`，authenticated=False —— 与声称一致 |
| `app/audit/models.py` | `A941D550FFB092C05D7F2A2DBF07E16AD1B2C6CF270AFA4485E6577FD77A6388` | 改。`AuditAction.TOOL_CALL = "tool_call"`（:84）—— 与声称一致 |
| `app/audit/logger.py` | `E3BCC267CAA3888DB010723A30991DA7626F7514F33384FE192C3A908FF73565` | 既有。`log()` 异步写 DB、失败降级 structlog 不抛异常 —— run() 审计降级语义所依赖的既有机制真实存在 |
| `tests/test_tool_contract.py` | `047FD291FA53B5CC0F8456CFC68B87358CEB3F5AEA2E958267AA55F704F070BE` | 新增。含 `test_describe_contracts_covers_every_default_tool`、`test_unlisted_contract_none_tool_runs_and_is_audited` 等 |
| `tests/test_tool_audit.py` | `5A167879942C55720FCE7722998E0417C0ED19837934CD558310408050AB3CB9` | 新增。含 `test_write_tool_not_registered_when_switch_off`、`test_write_oversize_leaves_no_empty_shell_and_later_legal_write_succeeds` 等 |

补充核对：`config.py:385 FILE_OPS_ENABLED: bool = False`、`:388 FILE_OPS_MAX_BYTES=65536` —— 三闸首闸默认关，与「file_ops 写路径默认关闭、未单独批准不开放」一致。`tests/conftest.py` 将 `DATABASE_URL` 钉到 `sqlite:///./data/test_ai_assistant.db`、`AUTH_ENABLED=true`、`AGENT_ORCHESTRATION=self`、`SECURITY_RATE_LIMIT=false`，basetemp 固定到 `data/pytest-tmp/`，未触碰 `data/ai_assistant.db` 开发/生产库。

> 注：此前评审记录的是 git blob hash-object 指纹（不同算法），与本表 SHA-256 不可直接比对；本表以当前 HEAD 工作区文件 SHA-256 为准，并经逐行源码阅读确认内容与声称一致。

## 4. 验收路径实跑结果（项目 conda 解释器）

解释器：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`（经 `python -m pytest`，非 base 解释器）。

| 命令 | 退出码 | 结果 |
|---|---|---|
| `python -m pytest tests/test_tool_contract.py tests/test_tool_audit.py -p no:cacheprovider --basetemp=data/pytest-tmp/pagt002-recheck-new -q` | 0 | **15 passed in 3.95s**（无失败、无跳过） |
| `python -m pytest -k "tool or audit" -p no:cacheprovider --basetemp=data/pytest-tmp/pagt002-recheck-k -q` | 0 | **153 passed, 1596 deselected, 1 warning in 24.05s**（无失败、无跳过） |

对照：PAGT-002 关闭记录声称 `pytest -k "tool or audit"` = 152 passed / 1580 deselected。本次复核为 153 / 1596，差 +1 passed、+16 deselected。该差异来自 PAGT-003（工具调用预算，即当前 HEAD 提交）在 PAGT-002 关闭后追加的 tool/audit 相关测试，非 PAGT-002 自身用例变化；两项命令退出码均为 0、无 failed/error/skipped，验收路径当前为绿态。

四条 acceptance 的代码级证据均在位（§3 对照表），且由绿态测试承载：①注册表输出十要素；②一次调用一条可查审计；③L2+ 默认保守执行；④file_ops 写默认关闭需显式开启。

## 5. 未决 Review 扫描结论

通读三份评审与关闭记录：

| 文档 | 结论 | 发现的 P1/P2 | 后续处置 |
|---|---|---|---|
| `2026-10-11-PAGT-002-plan-review.md` | 需修订 | 2 个 P2 系**计划规格缺口**（contract=None 语义未定义；agents 层直查 User 表分层偏差未登记），明示「无 P0/P1」 | 两 P2 在实现阶段补规格（implementation §5 R1-P2-1/R1-P2-2），非代码缺陷遗留 |
| `2026-10-11-PAGT-002-counterexample-review.md` | 初判存在阻断 → §8 修复复核后「P1/P2 全部清除，复核通过」 | 2 个 P2 实现缺陷（P2-1 超限写残留 0 字节空壳+错误码被吞；P2-2 contract=None 注释言过其实） | P2-1 已改为先编码校验后 `O_EXCL`（§3 已实证在位）；P2-2 按方案 B：修正注释 + 登记「启用 MCP 前必须先为其补契约」运维前置（链 PMCP 族卡） |
| `2026-10-11-PAGT-002-manual-review-1.md` | **通过** | 无 P1/P2；§7 列 4 项 P3 观察 | 全部为接受的残余/已知限制 |
| `closure_pagt_002_20261011.md` §6 | 「无 P0/P1 业务缺陷残留」 | — | 与上述三份文档一致 |

核对 closure「无 P0/P1 残留、4 项 P3 观察已记录」断言：**与文档一致**。全流程无任何 P1；唯一两个 P2 均在修复回路中清除并经独立复核（§8）。残留项全部为 P3 级或已登记的运维前置/已知限制，逐项如下，均**不构成未决 P1/P2**：

1. contract=None 工具（MCP 启用时注入）按「放行+每次必审计(risk=unlisted)+describe 标 missing」处置，**未加 authentication_required 门**。因默认 `MCP_ENABLED=False`、内置工具全部有契约，当前无实际暴露；已登记「启用 MCP 前必须先补契约」为运维前置并链 PMCP 族卡——属**经评审接受的设计决策**，非开放缺陷。
2. `run()` 内 `_resolve_role()` 对 User 表只读主键直查，属 AGENTS.md §5 Brownfield 分层偏差；已最小化为仅 allowed_roles 非 None（即 L2 写）时触发、失败 fail-closed；后续由身份上下文注入 role 消除。
3. web_fetch 在 builtin 声明 L1 而计划对照表写 L0（allowed_roles=None，无行为差异），文档表格级漂移。
4. 既有 `test_tools.py` 管线循环经 run() 产生 tool_call 审计行但不自行清理（测试卫生 P3，不影响结果）。
5. 合同级 `asyncio.timeout` 对同步阻塞沙箱无法中断线程内执行（声明性墙钟目标，依赖既有内部超时兜底）。
6. `allowed_roles` 硬编码与 `ROLE_PERMISSIONS` 矩阵双轨（security.py 越界，未矩阵化）。
7. 未做 PostgreSQL 下审计写入 E1 验证（仅 SQLite 隔离测试库）。
8. 写开关走 `os.environ` 直读而非 Settings（.env 不可配置，方向更保守）。

此外，反例审查 §8.4 残留的 P3-余1（closure 引用用例名不实）经本次核对：当前 closure §1/§2 引用的 `test_describe_contracts_covers_every_default_tool`、`test_tool_call_writes_one_audit_record_without_arguments`、`test_write_oversize_leaves_no_empty_shell_and_later_legal_write_succeeds` 等用例名均真实存在于测试文件且随绿态运行——**该漂移已订正**。

## 6. 总体结论

**通过。**

PAGT-002 在当前 HEAD（`0b08c2ee`，分支 `codex/pagt-tool-polish`，工作区干净）上的交付证据成立：

- 十要素契约模型、`ToolRegistry.run` 调用前 RBAC 拦截/超时包裹/每次调用一条 TOOL_CALL 审计、`describe_contracts`、file_ops 三闸默认关闭与 L2 写路径——代码事实与声称逐项一致（§3）。
- 验收命令 `pytest -k "tool or audit"` 与本卡两个新增测试文件经项目 conda 解释器实跑均退出码 0，153/15 全绿、无失败无跳过（§4）。
- 三份评审的全部 P2 已在修复回路清除并复核；无任何未决 P1/P2；closure「无 P0/P1、4 项 P3」断言与文档一致（§5）。

**对 PAGT-006 的准入判定**：PAGT-002 准入证据充分，**PAGT-006 可据此开工**。

**缺口清单（均为接受的残余/已知限制，不阻断 PAGT-006，仅作记录）**：

- 无阻断性缺口。
- 需持续留意的承接项：contract=None 的 MCP 工具未做 fail-closed 门，已链 PMCP 族卡（启用 MCP 前必须先补契约）；`_resolve_role` agents 层直查 DB 的分层偏差待身份上下文注入 role 消除；PostgreSQL 审计 E1、`allowed_roles` 矩阵化一致性仍为未验证项。这些均已在 PAGT-002 实现说明与关闭记录中如实登记，不属于本卡未决缺陷。
