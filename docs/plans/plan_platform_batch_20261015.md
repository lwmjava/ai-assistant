# 安装、接口与对话批次实施计划

日期：2026-10-15。执行者：Codex 主 Agent；用户最初授权新建分支并使用 module-batch-delivery，依次实施本页八卡，后明确追加 NFR-003 前置。此授权不包含其他集合外开发、推送、合并、部署、生产数据变更或真实索引重建。

## 基线、契约与终点

- 基线：`873db9c0fb243b0847b4be57242c8f333f4f0b1e`，原分支 `codex/cov-001`，开工时工作区干净。
- 工作分支：`codex/platform-batch-20261015`，已创建；未提交、推送。
- 契约：当前 `tasks.yaml` 八张原始卡；`AGENTS.md`；技能 `docs/ai-prompts/skills/module-batch-delivery/SKILL.md` v1.0.0 与 `references/acceptance-gates.md`；产品需求相应章节；已批准 1.0 锁定/拆分计划及测试用户裁决。
- 串行推进一张卡的业务改动。同卡由独立上下文进行工程审查、验证和代码审查，主 Agent 独占状态更新。用户未明确以自动验收替代不可自动证明的人工业务判断，保留该门禁。
- 终点：八张卡全部满足原始交付及最终集成回归才可称批次完成；否则逐项记录阻塞。未通过必要门禁的卡不得写 done 或供下游作为已完成前置。

## 顺序、依赖与台账

用户顺序：INST-004 → CLI-002 → NFR-003（2026-10-15 追加）→ NFR-012 → API-001 → ERR-001 → SSE-001 → LLM-001 → CHAT-007。

依赖：CLI-001(done) → CLI-002；NFR-003(backlog) → NFR-012；SEC-002(done) → ERR-001。其余卡无显式 depends_on；done 前置还须核对当前证据。NFR-003 原在集合外，用户已明确追加，不能删除其验收或伪造完成来解锁 NFR-012。

| 卡 | 现状/问题 | 交付与候选方案 | 非目标 | 原始验收与验证 | 流程状态 |
|---|---|---|---|---|---|
| INST-004 | User 无首次改密、测试用户标记；环境变量仅创建单管理员；无改密接口和页面 | 增量迁移两标记；环境变量引导 admin/system/culture，角色分别 system_admin/tenant_admin/member；culture 测试标记为真；后端阻止未改密主体访问业务，前端引导改密；改密审计及令牌失效按现有机制实现 | 无测试专用角色；不做完整导出排除；不重置已有用户密码；不操作真实库 | 三账号可空邮箱、bcrypt 落库、新 Session 读取、首次改密前所有业务入口拒绝、修改后放行、失败无副作用、旧身份 fixture 回归；完整后端和前端门禁、实际页面路径 | pending，等待允许路径核对 |
| CLI-002 | CLI 仅 migrate/admin，无 init；Settings JWT 默认值存在 | 新增显式 init，安全生成 JWT 并写入指定初始化配置，非 development 拒绝默认密钥；幂等与失败不泄漏密钥 | 不改会话业务语义；不执行真实初始化、不覆盖已有配置 | 临时目录实际 CLI 生命周期、重复 init、已有配置保护、非开发默认值拒绝；全量 pytest/Ruff/mypy、Linux 与最终 Ubuntu CI | pending，配置输出与禁改 .env 冲突待明确 |
| NFR-003 | 只有开发编排，无生产编排文件 | 单应用实例、数据库及按配置需要的向量服务；健康检查、重启和资源限制；启用 Milvus 时明确 2.4+，local 默认不变 | 不做 Kubernetes、自动扩缩、多实例共享存储；不借此做 PostgreSQL 生产验证或切默认向量库 | 隔离环境实际一键启动且声明需要的探针通过、依赖未就绪不就绪、local 路径不计用不到的 Milvus；Compose config 不是启动验收的替代 | pending，Docker 服务当前不可用，启动验收待具备环境 |
| NFR-012 | Compose 数据库口令回退为可预测值，CORS 回退 *；NFR-003 未完成 | 前置通过后，将口令改成必填，跨域显式来源且保留 JWT 必填；检查实际 Compose 展开 | 不放宽 JWT；不自动实现集合外 NFR-003 | 缺口令/跨域拒绝或保守默认、实际展开结果不含不安全默认值；无生产部署 | blocked，集合外前置 |
| API-001 | API 挂载 /api，现有客户端、文档、代理等引用旧路径 | 一次迁移当前调用方至 /api/v1，更新 OpenAPI 与 README；审查脚本/代理调用方 | 不保留第二套旧路径；不改业务规则 | 全部注册业务 API 前缀一致、旧路径拒绝、README 路径存在、前端实际请求；完整前后端门禁 | pending，调用方范围待核对 |
| ERR-001 | 卡要求九种错误码，PRD §6.6 只列八个命名码 | 确认权威清单后对齐触发、统一响应及前端交互和 OpenAPI，逐项清单 | 不新增未经批准错误码、不做管理界面 | 每个命名码真实触发、状态/脱敏/持久化副作用、前端可见行为及 Swagger 覆盖；完整前后端门禁 | pending，数量契约冲突 |
| SSE-001 | 当前流缺 run_id、心跳与 Last-Event-ID 回放 | 保留受控执行入口，定义运行身份/有界缓冲/主体权限/断线生命周期；补 run、node、intent、plan、tool.call/result、citations、heartbeat；前端同步 | 不以此实现引用侧栏；不另造 Runtime | 全事件与 ID、按 Last-Event-ID 回放、权限/跨租户拒绝、过期缓冲和断线/取消行为；Evaluation、完整前后端回归 | pending，先核对现有 ADR 的覆盖，边界变化先落 ADR |
| LLM-001 | 只有 chat/intent/fallback，embedding 独立配置，无 rerank 档 | 明确四任务类型和兜底链；未知任务拒绝；规划/执行/质量门复用 chat；两个真实提供商切换证据 | 不为五阶段分别配模型；不自动启用未采纳重排、不变更索引身份 | 类型、熔断兜底、缓存/预算/调用次数/取消/重试、真实两家对话路由；Evaluation 与全量回归；真实外发/费用另核授权 | pending |
| CHAT-007 | 输入仅文本；ChatMessage.content 目前 str，图片需模型接口配合 | 严格按 §5.1.7 扩展名白名单，每文件 ≤1048576 字节；粘贴/选择图片及文档源码仅附当前消息，不摄取知识库；模型载荷、历史读取和权限一致 | 无 .tsx/.jsx 或其他新类型；不入知识库、不建索引；不额外限制文件个数 | 超限/非法类型拒绝且零副作用、模型实际收到图片/正文、知识库数量不变、消息和附件关联、安全失败路径、实际页面操作；Evaluation、完整前后端门禁 | pending，模型/Schema 必需路径待补充 |

## 当前执行台账（2026-10-16 续作）

上表保留开工时的问题和候选方案，其中 pending、未批准和集合外措辞为历史记录；当前授权与状态以下表及 tasks.yaml 为准。API 路径现为 /api/v1，下文首卡早期 /api 示例不再代表当前接口。

| 卡 | 当前状态 | 实际交付与未验项 |
|---|---|---|
| INST-004 | in_review | 三账号、标记、改密门禁及页面已实现；最终整合、Linux/CI 与完整真实改密页面验收待齐 |
| CLI-002 | in_review | 安全 init、已有配置保护、非开发 JWT 校验已实现；Linux 文件权限/替换竞态与最终 CI 待验 |
| NFR-003 | in_review | 生产 Compose、可选 Milvus、探针和运行手册已落；七项隔离 config 检查通过，Docker Engine 不可用，未实际启动 |
| NFR-012 | blocked | NFR-003 已纳入授权，但实际启动前置尚未完成；不以静态 config 解锁 |
| API-001 | in_review | 当前端到端调用方迁移 /api/v1；阶段全量 2362 passed/3 skipped，不再代替后续共享路径最终回归 |
| ERR-001 | ready | 2026-10-16 澄清计数笔误：八种错误码加静默刷新成功，共九种场景；前置 SEC-002 为 done，等待端到端实施与验收 |
| SSE-001 | in_review | ActiveRun、回放/取消/权限、规范事件和页面已实现；阶段后端 156 passed，隔离页面停止/刷新持久化实测；最终全量/Linux/CI 待验 |
| LLM-001 | in_review | 四用途与有界熔断已实现；120 passed，Ruff/mypy 与独立审查通过；千问真实文本对话通过，第二家及最终整合待验 |
| CHAT-007 | in_review | 附件校验、原子保存、图片/正文载荷、恢复/权限、前端已实现；151 联合专项及追加19独立生命周期通过，前端120项和四门通过；完整回归及 Linux/CI/真实视觉等仍待验 |

追加批准：SSE 单进程 ActiveRun 架构与 ADR0012/ADR 索引/README/Swagger；CHAT 附件增量迁移、app/core/migration.py、README/Swagger；INST Swagger；NFR Dockerfile 与 .dockerignore。各补充只用于对应卡。没有真实库迁移、外发、费用、推送或部署授权。

测试外发隔离纠正见 docs/reviews/2026-10-15-platform-test-network-isolation.md；此前全量结果不能证明完全离线，也不能作为纠正后的最终整合证据。当前测试入口阻断工作区 .env 和外部 DNS/socket，不禁用 RAG、安全、能力护栏以制造通过。CLI init 自身的隔离证明独立保留。

## 范围约束与待决事项

### 已收到的用户决定

2026-10-15 用户明确回复：

- “批准这些必要路径补充”：INST-004 增 `app/schemas/auth.py`、`README.md`；本批各卡在 `docs/plans/`、`docs/reviews/` 的计划/说明/证据文件可写。
- “保留九种错误码，稍后指定第九码”：ERR-001 数量门槛不变，第九码未指定前保持阻塞，不按八码关闭。
- “把 NFR-003 纳入本批次实施”：按原卡交付/非目标/验收追加，放在 NFR-012 前；不因此获得实际生产部署授权。

随后用户明确回复：

- “批准这些对应范围”：API-001 增 `Dockerfile` 与 `scripts/milvus_switch_check.py`；CHAT-007 增 `app/llm/` 多模态契约和必要 `app/agents/` 消息/预算适配；LLM-001 增 `README.md`。只用于对应卡，不形成全批任意修改许可。
- “按此边界执行”：CLI-002 可实现 init 生成配置，测试仅写隔离临时目录，本次不得读取/覆盖工作区 `.env`，已有配置默认不覆盖。

不可自动证明的人工业务判断与 Linux/Ubuntu CI 门禁仍保留。

每卡仍使用各自 allowed_paths，不合并成整批可任意修改范围。此计划与审查/报告属于用户明确调用技能要求的产物；部分原始卡未列这些目录，正式更新契约前先记录缺口，不擅自扩展业务范围。

已定位的首卡最小缺口：`app/schemas/auth.py` 要声明改密输入、首次改密/测试标记的稳定响应；原卡未允许 `app/schemas/`。新增配置与 API 按项目规则必须同步 `README.md`，原卡也未允许。建议仅为 INST-004 增加 `app/schemas/auth.py`、`README.md`、本卡 `docs/plans/` 计划/说明、`docs/reviews/` 证据。其余卡逐一补精确文件，未经确认不改。

### 首卡实施细化

沿用现有配置名 `INITIAL_ADMIN_USERNAME/PASSWORD/EMAIL`，新增 `INITIAL_SYSTEM_PASSWORD`、`INITIAL_CULTURE_PASSWORD`，无口令默认值。两项新口令启用三账号模式，要求一起配置且管理员用户名为 admin、管理员口令有效；先校验全部配置再在同一事务创建三账号、default 租户和 Membership，避免部分写入。仅旧管理员配置时保留单管理员兼容路径并标记首次改密；没有口令时不生成默认口令。已有库不认领同名账号、不重置密码/角色；旧用户标记默认 false。此功能不授权对已有真实库补种账号或迁移。

`POST /api/auth/change-password` 使用 `{current_password,new_password}`，成功返回现有双令牌；`GET /api/auth/me` 返回两标记。新密码至少八位且不能等于旧密码；错误口令、无效输入与提交失败不清标记、不改变版本。服务层保存 bcrypt 并递增 token_version，路由记录脱敏审计。统一认证依赖仅精确放行 me/改密（并保留匿名登录与刷新路径），其他业务拒绝待改密主体；角色和租户权限仍独立生效，不放行切租户。沿用原 access token 语义，不在本卡重做全套撤销机制。

前端 /change-password 使用现有 AuthScreen 组件；业务布局等 /me 验证完成再放行，不能凭持久化旧摘要放行。登录、刷新、深链均转改密页；成功换票重新 /me 后才能进入业务；失败保持待改密状态，退出可清会话。

增量 Alembic 迁移两布尔列，旧值安全默认 false；配合既有 create_all 路径做幂等列存在判断，修正自动 stamp 的结构核对避免跳过新增用户列。不得引入另一套长期手写补列流程或静默修复损坏的最终版本标记。

CLI-002 卡禁止改 `.env`，PRD §5.11 要求 init 写 `.env`。建议明确区分“代码支持将来写入用户指定配置”与“本次执行不得读取/覆盖工作区 .env”；测试仅在临时目录执行。若原禁止含功能本身，必须先裁决。

ERR-001 原卡两处“九种”与 PRD §6.6 的八个命名码冲突；Access Token 刷新成功行的 code 为 `-`，不是第九个错误码。不能自行发明第九码或弱化为八码完成。

独立只读审查另定位：API-001 需要同步 `Dockerfile` 健康检查和 `scripts/milvus_switch_check.py` 的现有调用方，均不在原允许路径；CHAT-007 图片协议需要 `app/llm/`，消息构造/预算可能需要 `app/agents/`，均不在原卡范围；LLM-001 配置同步需 `README.md`。这些补充待对应卡开工前确认，不自动授权整目录。

SSE-001 的断线后继续运行与回放改变执行所有权和取消生命周期；ADR-0010 保持原流式契约，不覆盖此变化。须先落 Proposed ADR 并取得必要架构决定，不能把用户授权实施既有卡解释为新架构已批准。初步工程审查见 `docs/reviews/2026-10-15-platform-batch-readiness.md`。

## 验证、证据与回滚

Windows 11；指定解释器 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`，Python 3.12.0；pytest 8.4.2、Ruff 0.8.4、mypy 1.14.1、FastAPI 0.115.6、SQLModel 0.0.22、Pydantic 2.10.4；langgraph 已安装。CI 安装 dev/langgraph extra，以 `.github/workflows/ci.yml` 为准。测试默认隔离库 `data/test_ai_assistant.db`，不使用生产库；不输出工作区 .env 内容或真实凭据。

开工基线：`python -m mypy app/` 退出 0，205 文件；`python -m ruff check .` 退出 1（缓存临时文件权限失败，非业务诊断）；重跑 `python -m ruff check . --no-cache` 退出 0。全量 `python -m pytest` 正在收集完整证据，暂不声称通过；失败须隔离后才能实施。

基线隔离结果（业务代码仍为同一 HEAD）：默认临时目录导致 1690 passed / 639 errors、退出 1，639 个 setup 错误均是旧 `data/pytest-tmp/run` 的 WinError 5（异常行去重仅此一种）。首次使用新临时目录的重跑又复现父子进程 UTF-8/GBK 解码不一致，已中止，退出 1、不作为业务失败结论。统一 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`，新目录 `data/pytest-tmp/platform-batch-utf8-10ad24c9a6414d298152b66baafa9fc6`，完整命令 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -m pytest --basetemp <上述绝对目录>`：2329 passed / 3 warnings，547.73 秒，退出 0。未禁用功能或改测试；只隔离临时目录和统一编码。

前端基线：Node v22.20.0、npm 10.9.3；现有 node_modules 下 `npm run typecheck`、`npm test`（4 文件/37 用例）、`npm run lint`、`npm run build` 均退出 0。本轮未重新 npm ci；不宣称重装依赖验证。全量日志保留宿主临时目录 `platform-batch-baseline-pytest.log`、`platform-batch-baseline-pytest-isolated.log`、`platform-batch-baseline-pytest-utf8.log`，正式交付另记候选结果和输入指纹。

每卡先有效失败案例，再最小实现，再独立验证/审查。公共认证、全局配置、LLM、SSE 等触发不带筛选的全量 pytest；所有后端变更运行 Ruff/mypy。前端/契约变化运行 typecheck、Vitest、ESLint、build；页面验收必须实际走路径。进程/文件系统/平台变化需 Linux 测试及同最终提交 Ubuntu CI；本次无推送授权，不伪造该证据。

最终证据绑定 HEAD 与源码/测试/配置/依赖/Evaluation 输入 SHA256、完整命令及退出码、失败清单与报告；后续卡改变输入必须重跑。每卡另写实际实现说明与独立审查，不将计划写成实现。未满足必要检查保持 in_review/blocked；无业务代码变更时仍保持原卡实际状态。

回滚只撤回本卡的源码/配置变化；有数据迁移需明确兼容及恢复步骤，不能直接退列或清库。不得 reset 用户或其他卡改动。自动化不能证明的业务判断继续保留，未经批准不推送、合并、部署。

## 2026-10-16 错误码计数纠正

用户在核对来源后要求重新判断任务状态。PRD §6.6 原表为八种命名错误码加静默刷新成功，无需新造第九码。此前“等待用户指定第九码”的裁决记录保留为历史，本次纠正替代其当前效力。ERR-001 任务卡事实、交付、验收和 Evaluation 统一按九种场景表述，非目标“不新增错误码”保留。SEC-002 当前 done，故移除该数量阻塞并改 ready；尚未完成本卡端到端实施/验收，不改 in_review 或 done。本次仅改任务与说明，不改业务、测试、依赖、配置、CI 或需求正文。具体检查见 implementation_err_001_contract_correction_20261016.md。
