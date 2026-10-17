# PAGT-002 实施计划独立审查结论

- 审查对象：`docs/plans/plan_pagt_002_20261011.md`（工具十要素契约：权限、风险分级、超时、幂等、审计）
- 审查基准：`tasks.yaml` PAGT-002 卡原文（4616–4670 行）、AGENTS.md §7/§5/§9/§10/§4、用户 2026-10-11 拍板决策
- 审查 Agent：独立计划审查 Agent（未参与计划编写与实现）
- 审查日期：2026-10-12；分支 `codex/pagt-tool-polish`；HEAD `e2a3b2f`（与计划声称基点一致）
- 审查方式：只读抽查代码事实 + 复跑基线测试；未修改任何计划文件、业务代码与 tasks.yaml

## 1. 审查结论速览

**总体结论：需修订。** 计划方向正确、原始契约对账完整、声称的现状事实经独立抽查基本属实、基线测试复跑绿态。但存在 2 个 P2 规格缺口（contract=None 工具的门控语义未定义；agents 层直查 User 表的分层偏差未登记），P2 未补清前不得进入实现。另有 4 个 P3 登记项。

## 2. 代码事实抽查记录（实际执行）

| 抽查项 | 命令/方式 | 结果 |
|---|---|---|
| Git 分支与基点 | `git branch --show-current; git log --oneline -3; git status --short` | `codex/pagt-tool-polish`，HEAD `e2a3b2f`，工作区仅计划文件未跟踪，与计划声称一致 |
| 基线测试绿态 | `pytest tests/test_tools.py tests/test_audit_smoke.py tests/test_pagt001_tools.py -p no:cacheprovider --basetemp=data/pytest-tmp/pagt002-review-baseline -q` | **70 passed in 4.63s，exit 0**（未触碰生产库，basetemp 隔离） |
| Tool 模型与执行咽喉 | 通读 `app/agents/tools/base.py` | `Tool` 仅 name/description/parameters/func 四字段；`run()`（96–121 行）为唯一执行咽喉，当前无权限/风险/审计；`describe()` 仅输出三要素——计划 §2.1 属实 |
| 身份通道真实性 | 通读 `app/core/log_context.py`、`app/api/deps.py`；grep `set_user` | ContextVar user_id/tenant_id 默认空串；`deps.py:90` 认证成功后 `set_user(user.id, user.tenant_id)`；`deps.py:47-49` AUTH_ENABLED=False 时 `set_user(None, None)`；全仓 set_user 仅 deps.py 两处——计划 §2.4 声称的通道真实存在 |
| 聊天路由是否经过身份依赖 | grep `get_current_user|Depends` `app/api/routes/chat.py` | 对话路由（223/272/341/…）全部 `Depends(require_permission(...))` → `get_current_user` → 写入 ContextVar；HTTP 对话主链的身份通道成立 |
| RBAC 矩阵 | 通读 `app/core/security.py:33-237` | Role 五级枚举与计划引用一致；`check_permission` 纯函数；ROLE_PERMISSIONS 无 tools 资源行，计划「不新增 resource 行」属实 |
| 审计现状 | 通读 `app/audit/models.py`、`app/audit/logger.py` | AuditAction 枚举无任何 tool 值（计划 §2.6 属实）；AuditLog 含 user_id/tenant_id/action/resource_type/resource_id/details(JSON)；`log()` 异步写 DB、失败降级 structlog 不抛异常；`app/api/routes/audit.py:88,110` 支持按 action 过滤——acceptance②「可查询」路径现成 |
| 工具构造点 | 通读 `builtin.py:135-212`、`web_search.py:236-252`、`file_ops.py:219-241` | 4 内置工具 + 2 工厂（available 才注册）；web_search 已有内部 `asyncio.timeout`（193 行）；file_ops 现纯只读、additionalProperties=False——计划 §2.2 属实 |
| 管线咽喉 | `app/agents/pipeline.py:430-436` | `_execute_tool()` = 围栏后 `self.tools.run(call)`；所有管线工具调用汇聚 run()——Brownfield 单点改造策略成立 |
| 配置默认值 | `app/core/config.py:94,326,383-386` | AUTH_ENABLED=True、AUDIT_ENABLED=True、FILE_OPS_ENABLED=False、FILE_OPS_ALLOWED_ROOTS="{}"、FILE_OPS_MAX_BYTES=65536——计划 §2.5 全部属实；`get_settings()` lru_cache 单例（478-479 行） |
| 测试隔离既有模式 | 通读 `tests/test_pagt001_tools.py:18-48` | 既有模式 = monkeypatch.setattr(settings, …) + tmp_path 根别名；计划 §5 的写测试隔离方案可复用该模式，可行 |
| MCP 注入路径 | `app/services/chat_service.py:192-210` | `_build_tools()` = `default_tools()` + MCP_ENABLED 时 `tools.extend(mcp_tools)` 进入**同一 ToolRegistry**——发现计划未覆盖的 contract=None 场景（见 P2-1） |
| file_ops 现状 | 通读 `file_ops.py` 全文 | 读路径防护齐全（别名/扩展白名单/私有组件/符号链接/句柄绑定/秘密内容）；当前无写动作；写路径需新建 |

## 3. 七维结论

### 3.1 范围漂移：基本无漂移，两处决策点已自陈且方向保守

- 三项 deliverable（十要素声明、审计事件、file_ops 写路径 L2 默认关闭）全部有对应实现章节，无删减、无降级为 non-goal。
- allowed_paths 核对：新增 `contract.py` 在 `app/agents/tools/` 内；改造集中在 base.py/builtin.py/file_ops.py/web_search.py/models.py(审计)/tests/，均在 allowed_paths；计划**主动排除** config.py、deps.py、security.py、chat.py——纪律正确。
- 越界决策点（计划 §3.5）：①写开关走 `os.environ` 而非 Settings（因 config.py 越界）——方向保守（更难开启），计划已标注偏差；②`/api/chat/tools` 不扩展十要素输出——acceptance① 原文为「工具注册表可输出」，进程内 `describe_contracts()` 满足字面要求，可接受，P3 登记。
- 未引入 PAGT-003/004 范围（调用预算、Plan 结构化均明确排除）。

### 3.2 架构与兼容：符合 Brownfield 渐进约束

- 单点改造 run() 咽喉，不重写调用链；契约为声明层，成功路径观测文本格式 `[name] 参数=shown 结果=observation` 承诺不变；既有 70 项测试作为行为回归证据——non_goal「不改既有工具业务行为」有可执行的守门。
- 与 PAGT-001 已交付 web_search/file_ops 工厂结构兼容（工厂返回 None 不注册的既有语义保留）。
- **偏差（P2-2）**：计划 §3.2 让 run() 内 `Session(engine)` 直查 `User.role`。AGENTS.md §5 要求 agents 层「不得直接操作数据库」；虽为只读主键查询且 AuditLogger 有同类先例（logger.py:186），但属 Brownfield 边界偏差，计划 §3.5 三个决策点中**未登记此项**，实现说明必须补登并注明替代方向（后续由身份依赖注入 role，本卡不做）。

### 3.3 权限与租户：身份通道经独立验证真实，L2 保守语义明确

- 运行时身份 = log_context（deps.py 认证链路写入），非工厂默认值——通道真实存在，「调用前拦截」在 run() 入口成立。
- 保守语义明确：匿名（ContextVar 空，含开发无认证模式）→ authenticated=True 的 L2 写一律拒；User 不存在/DB 查询失败 → role=None 按未授权处理（fail-closed）；参数夹带 tenant_id/user_id → 拒绝（防伪造）。
- file_ops 写默认关闭 + 显式开启（环境变量 + settings.FILE_OPS_ENABLED + 有效根三闸）两态可测；L3 在 `register()` 期抛错拦截——注册表层面保证，优于纯约定。
- **缺口（P2-1）**：run() 对 `contract is None` 工具（生产启用 MCP 时由 chat_service.py:206 进入同一注册表）的权限门/风险门/审计行为未定义；`describe_contracts()` 完整性断言（§4 test_every_registered_tool_has_complete_contract）只遍历 `ToolRegistry(default_tools())`。MCP 默认关，不破坏本卡验收，但 acceptance① 的字面「工具注册表」在 MCP 启用时将出现十要素缺失。

### 3.4 失败路径与测试：五类齐全，断言对准缺失行为

- 每类至少一条具名反例，覆盖：契约构造期校验（timeout 零/负/NaN）、写字节边界、注入异常仍审计、超时包裹、审计 DB 降级不阻断业务、写冲突幂等、匿名/member/admin 三态对照、伪造身份参数、跨租户审计绑定、L3 注册拒绝、开关两态、L0 行为回归。用例名可被 `pytest -k "tool or audit"` 选中，测试文件命名与命令自洽。
- 隔离方案可行：复用 PAGT-001 已验证的 monkeypatch settings + tmp_path 模式；审计行用后即删对齐 test_audit_smoke；basetemp 隔离；不碰生产库。
- 缺一条对应 P2-1 的 contract=None 门控反例（见修订建议）。

### 3.5 身份与绑定：审计绑定来源正确

- 审计 user_id/tenant_id 取自 ContextVar 实时值，计划专设 `test_audit_binds_calling_identity_not_factory_default`；跨租户两行绑定互不串的用例存在。
- 版本字段统一 1.0.0；十要素字段（含 max_retries/conflict_policy/error_model/audit）覆盖 AGENTS.md §7 全项。
- 实现说明需注明：开发无认证模式下 `_DISABLED_AUTH_PRINCIPAL` 合成 SYSTEM_ADMIN 与 ContextVar 空并存（deps.py:23-31 vs :48），本卡按 ContextVar 判定，故 dev 模式「合成 admin 也不能用 L2 写」是刻意保守行为而非 bug（P3-4）。

### 3.6 性能与副作用：无热点/死锁风险，降级路径安全

- 每次工具调用新增：已认证身份一次 User 主键读 + 一条审计插入（独立 Session(engine)，与请求 session 不嵌套，无死锁面）。SQLite 本地写，成本可接受；真实并发性能计划已诚实列入未验证项。
- AuditLogger.log() 内部 try/except 降级 structlog、永不抛异常 → 审计故障不会阻断业务，契约不破坏。
- 注意：`asyncio.timeout` 包裹对同步阻塞工具（code_sandbox 同步沙箱）只能取消 await 点、不能中断线程内执行；合同级 timeout 对 sync 工具为声明性，依赖既有内部超时兜底（P3-3，实现说明注明即可）。

### 3.7 计划可执行性：顺序、命令、验收映射、回滚均明确

- Red→Green→回归顺序明确；验证命令使用项目声明的 conda 解释器 + 独立 basetemp；四项 acceptance 全部映射 E2（真实 registry.run + 测试库 + 临时文件），E3 不冒充质量结论——证据分级纪律正确。
- 回滚：回退契约声明提交；AuditAction 为 str 枚举、action 列为自由字符串（models.py:105），新增 `tool_call` 值**无需 DB 迁移**；写开关默认关、无数据迁移——回滚路径成立。

## 4. 缺陷清单

| 级别 | 编号 | 缺陷 | 证据位置 | 修订建议 |
|---|---|---|---|---|
| P2 | P2-1 | contract=None 工具（MCP 注入）的门控/审计/describe 语义未定义 | 计划 §3.2/§3.3；`chat_service.py:198-206` 证实 MCP 工具进入同一注册表 | 实现前补一节：contract=None 时 fail-closed 默认（建议按未认证 + 必审计 + 不授予任何 risk>=L1 执行的保守语义）；describe_contracts 对缺失字段显式标注 missing 而非静默跳过；补一条 contract=None 反例测试 |
| P2 | P2-2 | agents 层 run() 内 `Session(engine).get(User)` 直查数据库，属 §5 分层偏差且未登记 | 计划 §3.2 第 1 步；AGENTS.md §5 | 计划/实现说明显式登记该偏差（只读主键、deps.py 越界不可改的 Brownfield 妥协），注明后续由身份上下文注入 role 的演进方向；不要求本卡改架构 |
| P3 | P3-1 | 写开关 `os.environ` 直读与 Settings 惯例不一致，.env 文件无法配置写开关 | 计划 §3.4/§3.5-1 | 已自陈偏差，实现说明登记即可；方向保守（更难开启）符合「未单独批准不开放」 |
| P3 | P3-2 | contract.allowed_roles 硬编码与 ROLE_PERMISSIONS 矩阵双轨 | 计划 §3.1；security.py:150-230 | 因 security.py 越界可接受；resource/action 标签字段预留对齐，不新增矩阵行 |
| P3 | P3-3 | 合同级 asyncio.timeout 对同步阻塞工具不可强制中断 | 计划 §3.2 第 5 步 | 注明依赖 code_sandbox 既有内部超时；describe 输出 timeout 语义为「墙钟目标」 |
| P3 | P3-4 | dev 模式合成 SYSTEM_ADMIN 主体与 ContextVar 空并存，L2 写在 dev 一律拒 | deps.py:23-31 vs :48 | 实现说明注明该可观察行为为刻意保守，避免后续误判为 bug |

无 P0/P1 缺陷。

## 5. 对实现阶段的附加约束建议

1. 先补 P2-1、P2-2 到计划/实现说明，再动业务代码；两 P2 均不改变计划主方案，只补规格文字与测试一条。
2. 实现顺序严格 Red→Green；基线绿态已由本审查复跑确认（70 passed），实现方应以同一命令重跑基线作为起点证据。
3. 审计 details 禁止落 arguments/密钥（计划已要求），代码评审时逐条核对。
4. `describe_contracts()` 的十要素断言测试遍历的注册表必须与生产装配路径（_build_tools）对齐口径，或在实现说明写清「仅 default_tools 注册表」的覆盖边界。

## 6. 已独立验证的关键代码事实清单（供实现方复核）

- 身份通道：`log_context.set_user` 仅由 `deps.py:48,90` 写入；聊天路由全部经 `require_permission` → `get_current_user`。
- 审计可查询：`AuditAction` 现无 tool 值；`audit.py` 路由支持 action 过滤；AuditLog.action 为自由字符串列，新增枚举值无需迁移。
- 执行咽喉：`pipeline.py:436` 唯一汇聚 `tools.run()`；MCP 工具经 `chat_service.py:206` 同注册表。
- 配置事实：FILE_OPS_ENABLED=False（config.py:383）；get_settings 为 lru_cache 单例，monkeypatch 模式可用。
- 基线：`pytest tests/test_tools.py tests/test_audit_smoke.py tests/test_pagt001_tools.py` = 70 passed / exit 0（basetemp=data/pytest-tmp/pagt002-review-baseline）。
