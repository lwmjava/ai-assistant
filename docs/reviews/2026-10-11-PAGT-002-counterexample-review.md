# PAGT-002 入口级反例审查结论

- 审查对象：分支 `codex/pagt-tool-polish` 当前未提交 diff（PAGT-002 实现）
- 审查 Agent：独立入口级反例审查 Agent（未参与 PAGT-002 计划编写与实现）
- 审查日期：2026-10-12
- 参照：`docs/plans/implementation_pagt_002.md`、`docs/plans/closure_pagt_002_20261011.md`、`docs/reviews/2026-10-11-PAGT-002-plan-review.md`、tasks.yaml PAGT-002 卡
- 审查方式：通读完整 diff + 独立构造 12 个入口级反例实际运行（不复用实现者测试）+ 独立复跑全量子集 + 文件指纹记录

## 1. 总体结论

**存在阻断缺陷**：发现 2 个 P2 业务缺陷（P2-1 实测复现，P2-2 为已接受设计语义的边界确认）。P1/P2 未清前不得关闭本卡。其余契约项（十要素声明、审计绑定、L2 保守门、三闸默认关闭、L3 注册拦截、超时包裹、旧行为不变、范围纪律）经真实入口反例验证全部成立。

## 2. 代码指纹（git blob hash-object，审查时工作区状态）

| 文件 | blob 指纹 |
|---|---|
| app/agents/tools/contract.py（新增） | `e743befd2adbeb115333a0573724b2cd377d977c` |
| app/agents/tools/base.py（改） | `b4edb37c821ae74db08fb8aac399499ca1a892ba` |
| app/agents/tools/file_ops.py（改） | `78ec063750cb76e54415dae429968c038b086fe5` |
| app/audit/models.py（改） | `ef480cc74a493faad6eeb04b5190bc1309cf6280` |
| tests/test_tool_contract.py（新增） | `0de411777aac87c034903677f4e305a384f98191` |
| tests/test_tool_audit.py（新增） | `92b1da91e5796611515c4fa5068bf42bf63eaaee` |

## 3. 范围与 diff 核查

- `git status` 改动清单全部落在 allowed_paths（app/agents/tools/、app/agents/（builtin/web_search 属此目录树）、app/audit/、tests/、docs/plans、docs/reviews）；无 config.py/deps.py/security.py/chat.py 改动；既有测试文件（test_tools.py、test_audit_smoke.py、test_pagt001_tools.py、test_agent_chain.py）无任何修改。
- `app/audit/models.py` 表面 diff 247 行，`git diff -w` 实证语义改动仅 +3 行（新增 `TOOL_CALL = "tool_call"`）；其余为行尾翻转噪声（P3-1，见缺陷清单）。

## 4. 反例清单与实际运行结果

独立构造脚本（审查后已删除，不复用实现者用例）：`data/pytest-tmp/review_pagt002_counterexamples.py`，运行命令：
`& "D:\DepTooL\anaconda3\envs\ai-assistant\python.exe" -m pytest data/pytest-tmp/review_pagt002_counterexamples.py -p no:cacheprovider --basetemp=data/pytest-tmp/pagt002-ce-review` → **12 passed, exit 0**。

| # | 反例（入口/操作） | 期望 | 实际 | 结果 |
|---|---|---|---|---|
| CE-1 | 真实注册表 calculator 调用，ContextVar 绑定 MEMBER 身份 | 一次调用=一条审计；user_id/tenant_id=真实身份；details 无 arguments | 完全符合；audit row resource_type="tool"，details 仅 risk/allowed/reason/latency | 通过 |
| CE-2 | 三闸全关时 default_tools() | file_ops_write 不出现 | 未注册 | 通过 |
| CE-3 | 写闸全开但 ContextVar 空（dev 无认证模式）调写 | 拒绝、不落盘、审计 allowed=false 且 user_id=None | 拒绝；tmp_path 无文件；`"allowed": false` | 通过 |
| CE-4 | 真实 DB 用户：MEMBER 写 vs TENANT_ADMIN 写 | MEMBER 拒不写文件；admin 写成功 | MEMBER 拒绝；admin 写出内容一致 | 通过（_resolve_role 直查真实） |
| CE-5 | 同路径二次写 | 第二次 conflict、内容不被覆盖 | conflict；文件仍为 first | 通过 |
| CE-6 | `../escape.txt` 越界写 | 拒绝、不逃出临时根 | error；父目录无文件 | 通过 |
| CE-7 | calculator 调用夹带 tenant_id 参数 | 拒绝 forged_identity_argument，审计绑定原因 | 拒绝；details 含 forged_identity | 通过 |
| CE-8 | 构造 L3 契约工具注册 | register 抛 ValueError、不入注册表 | 抛错、get()=None | 通过 |
| CE-9 | 模拟未治理 MCP 写能力（contract=None）在匿名 ContextVar 下调用 | 审查重点：是否裸奔 | **匿名即执行成功**；审计 risk="unlisted" allowed=true；describe 标 missing | 语义确认（见 P2-2） |
| CE-10 | admin 写内容 500 字符、max_bytes=10（超限） | 校验失败不产生任何文件 | **返回 error，但磁盘残留 0 字节空壳文件 big.txt** | **复现缺陷（P2-1）** |
| CE-11 | sleep 0.5s 工具挂 0.05s 超时契约 | "超时"观测且快速返回 | 超时观测、总耗时 <0.4s | 通过 |
| CE-12 | calculator 6*7 成功路径 | 观测格式 `[calculator] 参数=… 结果=42` 与改造前一致 | 格式不变 | 通过 |

独立复跑闭声称谓：`pytest -k "tool or audit" -p no:cacheprovider --basetemp=data/pytest-tmp/pagt002-kverify` → **151 passed, 1580 deselected, exit 0**，与关闭记录一致。

## 5. 缺陷清单

### P2-1（阻断，已复现）过大内容写请求残留 0 字节空壳文件，create-only 幂等被破坏

- 证据位置：`app/agents/tools/file_ops.py:313-320`（`_write()`：先 `os.open(target, O_WRONLY|O_CREAT|O_EXCL)`，之后才 `if len(data) > maximum: raise ValueError("content_too_large")`）。
- 复现（CE-10，真实入口）：admin 身份经 registry.run 写 `path=big.txt, content="x"*500, max_bytes=10` → 返回 `{"status":"error","code":"access_denied"}`，但 `tmp_path/big.txt` **已存在且 size=0**。
- 影响：①后续对同路径的合法 create-only 写永远得到 `file_exists` conflict（空壳占坑），直到运维手工删除；②用户看到的错误码是泛化 `access_denied`，真实原因 `content_too_large` 被吞；③与关闭记录 §2「失败不更改数据/重试不重复副作用」的对账声明直接矛盾。
- 修复建议：`_write()` 内在 `os.open` **之前**完成 `data = content.encode("utf-8")` 与长度校验（超限直接 ValueError，不落盘）；错误码保留 `content_too_large`；补一条反例测试（超限写后断言同路径合法写仍可成功）。

### P2-2（阻断级语义确认）contract=None 工具「L0 只读级放行」无只读强制，匿名可执行未治理写能力

- 证据位置：`app/agents/tools/base.py:188-189`（`contract is None → return None` 直接放行，无身份/角色门）；`chat_service.py:198-206`（MCP 启用时未治理工具进入同一注册表）。
- 复现（CE-9，真实入口）：contract=None 的「mcp_delete_dir」式工具在匿名 ContextVar 下执行成功，观测返回 `side-effect-performed`。
- 评审判断：实现者选择「放行+必审计+describe 标 missing」而非 fail-closed 拒绝。**当前默认配置下无实际暴露**（MCP_ENABLED=False、内置工具全部有契约、每次调用审计 risk="unlisted"），故不定 P1；但代码注释「按 L0 只读级保守处理」**言过其实**——没有任何只读强制，未治理写工具对匿名/任意角色裸奔。
- 处置要求（二选一，落盘后方可放行）：①把 unlisted 工具至少加 `authentication_required` 门（匿名/dev 不放行，生产任意已认证角色可运行但全程审计）；或 ②在实现说明与 PMCP 族卡中明确登记「启用 MCP 前必须先为其补契约」为运维前置条件，本卡不改代码。建议选①，改动极小且语义与注释一致。

### P3-1 行尾翻转噪声
`app/audit/models.py` 整体 CRLF/LF 翻转，语义仅 +3 行。提交时建议还原行尾或在提交说明注明，避免后续 review 被 244 行噪声淹没。

### P3-2 关闭记录用例名不实
`closure §1` 引用 `test_registry_describes_each_tool_contract`（实际为 `test_describe_contracts_covers_every_default_tool`）与 `test_tool_call_writes_audit_without_arguments`（实际为 `test_tool_call_writes_one_audit_record_without_arguments`）。证据位置真实存在，仅名称对不上，修订关闭记录即可。

### P3-3 错误原因被泛化吞掉
`content_too_large`（及其他 ValueError）在 `file_ops_write` 被统一映射为 `access_denied`，可观测性损失；随 P2-1 修复一并改正。

## 6. 通过项汇总（独立验证，非实现者自审）

- 审计绑定真实身份/租户（非工厂默认）、每次调用恰好一条、details 不落 arguments/密钥——CE-1/CE-7/CE-9 实证。
- L2 保守执行：匿名拒、MEMBER 拒不写文件、admin 放行——CE-3/CE-4 实证；审计 allowed=false 绑定。
- 写三闸（FILE_OPS_ENABLED + FILE_OPS_WRITE_ENABLED==true + 有效根）真实存在且默认关闭——CE-2/CE-3 实证。
- L3 注册期拒绝——CE-8。
- 超时包裹对 async 工具生效——CE-11。
- 旧 L0 成功路径观测文本格式不变——CE-12。
- 范围纪律：diff 全在 allowed_paths，既有测试零改动。
- 151 passed/-k 子集独立复跑一致。

## 7. 修复回路建议

1. 实现方修 P2-1（_write 先校验后开文件 + 错误码 + 新反例测试）。
2. 按 P2-2 处置要求二选一并落盘登记。
3. P3-1 行尾处理、P3-2 用例名订正、P3-3 错误码。
4. 修复后重跑：`pytest tests/test_tool_contract.py tests/test_tool_audit.py -k "tool or audit"` + 新增超限空壳反例；本审查者可复核。

---

## 8. 修复复核（2026-10-12，Review 驱动修复回路第 8 步）

实现方按缺陷清单完成修复后，本审查者独立复核（未改业务代码/测试/tasks.yaml）。

### 8.1 修复内容核对（代码级）

| 原缺陷 | 修复实现 | 核对结果 |
|---|---|---|
| P2-1 空壳文件 | `file_ops.py:289-294` `_write()` 先 `content.encode("utf-8")` + `len(data) > maximum → ValueError("content_too_large")`，**之后**才 `os.open(O_CREAT\|O_EXCL)`；`file_ops_write:328-330` 对 ValueError 保留具体错误码 | 代码位置与语义符合修复要求 |
| P2-2 contract=None 语义 | 方案 B：不改门控；`base.py:153-156` docstring 删除「只读级」字样，如实写明「无只读/写强制——未治理 MCP 写能力在匿名下也会执行；启用 MCP 前必须先为其补契约（运维前置，链 PMCP 族卡）」；`implementation_pagt_002.md §5` 同文登记 | 如实描述代码行为；满足本审查原 P2 要求中「运维前置登记」可接受边界（默认配置无暴露、每次调用审计 risk=unlisted、describe 标 missing 均保留） |
| P3-1 行尾噪声 | `git diff -w --stat` 实证 models.py 仅剩 `3 +`（TOOL_CALL 枚举）；全量语义 diff 297 insertions/8 deletions | 干净 |
| P3-3 错误码泛化 | ValueError 分支返回具体码（content_too_large/invalid_path/unsupported_file） | R1 实证输出含 `content_too_large` |

### 8.2 独立复跑反例（本次复核新构造，审查后已删除）

`data/pytest-tmp/review_pagt002_fix_ce.py` + 实现方两测试文件同跑：

| 反例 | 结果 |
|---|---|
| R1（原 CE-10 修复目标）：admin 超限写（500 字符/max_bytes=10）→ 输出含 `content_too_large`；**磁盘无 big.txt 残留**；随后同路径合法写成功且内容正确 | **PASSED — P2-1 清除** |
| R2 门控回归：匿名写拒绝不落盘、二次写 conflict 不覆盖、`../evil.txt` 越界拒绝 | PASSED |
| R3 审计回归：calculator 一次调用一条 tool_call 行，绑定真实 MEMBER 身份/租户，details 无 arguments | PASSED |
| 实现方新增 `test_write_oversize_leaves_no_empty_shell_and_later_legal_write_succeeds` | PASSED |

合计 **18 passed in 3.91s, exit 0**。

### 8.3 独立复跑全量子集与基线

| 命令（项目解释器，独立 basetemp） | 退出码 | 结果 |
|---|---|---|
| `pytest -k "tool or audit" --basetemp=.../pagt002-fix-kverify` | 0 | **152 passed, 1580 deselected**（较修复前 151 +1 空壳反例，与预期一致） |
| `pytest tests/test_tools.py tests/test_audit_smoke.py tests/test_pagt001_tools.py --basetemp=.../pagt002-fix-baseverify` | 0 | **70 passed**（起点基线无回归） |

### 8.4 复核结论

**P1/P2 全部清除，复核通过。** 残留仅 P3 级文档漂移（不阻断）：

- P3-余1：closure_pagt_002 §1 的 acceptance① 行仍引用不存在的用例名 `test_registry_describes_each_tool_contract`（实际 `test_describe_contracts_covers_every_default_tool`）、acceptance② 行仍引用 `test_tool_call_writes_audit_without_arguments`（实际 `test_tool_call_writes_one_audit_record_without_arguments`）；§2 引用 `test_unlisted_contract_none_tool_runs_as_l0_and_is_audited`（实际 `test_unlisted_contract_none_tool_runs_and_is_audited`）。证据文件真实存在、仅名称未订正。
- P3-余2：implementation §5 的 P2/P3 编号在两轮审查间复用（同号指不同缺陷），建议后续按轮次重排编号，不影响内容。
