# SKILL-001、SKILL-002、SUP-001、SUP-002：创建技能、技能管理页、Supervisor 分派、任务过程展示

> 状态：SKILL-001、SKILL-002、SUP-001、SUP-002 已实现，说明见 `docs/plans/implementation_skill_001.md`、`docs/plans/implementation_skill_002.md`、`docs/plans/implementation_sup_001.md` 与 `docs/plans/implementation_sup_002.md`。2026-10-01 经两轮评审修订，同日按要求补上系统管理员跨租户管理和系统全局技能。前文若与计划正文冲突，以正文为准。
> 来源：`tasks.yaml` 的 SKILL-001、SKILL-002、SUP-001、SUP-002；`docs/plans/plan_remaining_delivery.md` 对应四节；交付排期第 5 节 D2、第 6 节第 18 项。评审处置见 `docs/reviews/2026-10-01-plan_d2评审.md`。
> 日期：2026-10-01
> 截止：2027-02-05
> 依赖：SKILL-001 依赖已完成的 FLOW-001。SKILL-002 依赖 SKILL-001。SUP-001 依赖 SKILL-001。SUP-002 依赖 SUP-001。

一次只实现一条。顺序是 SKILL-001、SKILL-002、SUP-001、SUP-002。

## 目标

1. 成员能创建只对自己对话生效的私有技能。系统管理员能创建对所有人生效的系统全局技能。对话在关键词命中时最多选用一条，并在非流式响应、正常流式落库和取消流式落库里留下技能名。其他成员的私有技能不会被选中。已启用的系统全局技能可以被选中。
2. 控制台用表格列出技能，列表上方可以筛选和新增。每行最后一列放详情、修改、启用、停用和删除。列表不返回技能正文。创建失败能看到接口错误。viewer 不能提交。系统管理员可以跨租户管理全部用户技能。
3. `AGENT_ORCHESTRATION=langgraph` 且已安装规定版本的 `langgraph` 时，Supervisor 完成一次分派并带回子代理结果。未安装时对话仍回退五阶段管线。默认配置仍是五阶段管线。
4. 对话页在图执行完成后展示子任务摘要。这不是节点进行中的实时进度。默认管线不出现该区块。

## 已核对的现状

核对日期：2026-10-01。拆分说明里的「可以从 YAML 注册」「没有技能页」「Supervisor 是可选路径」需要按下面收窄。

技能：

- `app/agents/skills/manager.py` 是进程内单例。`load()` 只扫内置目录，现有 `translator`、`code_review` 两份 YAML。`register()` 写进同一份字典，没有 `tenant_id`。
- 匹配已有关键词、正则、始终激活。关键词命中 1 个时置信度 0.5。`SkillManager.match()` 默认门槛 0.3，最多返回 3 条；`SkillMatch.is_match` 再按触发器自己的 `min_confidence` 过滤。对话只把通过这两关的结果注入提示词。
- `app/services/chat_service.py` 的 `_match_skills()` 用全局单例，不看租户。技能名只留在 `SkillContext` 和日志里。`ChatResponse` 和 `Message` 都没有技能字段。
- 助手消息有两条写入路径。正常结束走 `_persist_assistant`。流式被取消时走 `_persist_assistant_standalone`，见 `chat_stream` 的 `finally`。只改前一条，已停止的回复不会留下技能名。
- 工具模式的 `collect_tools()` 没有接到对话路径。两个内置技能都是 `prompt_injection`。用户技能正文若直接拼进系统提示，模型仍可能照着做；工具名单本身由代码决定。
- `PromptInjectionDetector` 用已知模式打分，默认阈值 0.5。对话是否阻断由 `SECURITY_BLOCK_ON_INJECTION` 决定，默认不阻断。创建技能若沿用这个开关，注入句仍会存进库。
- 没有技能 HTTP 接口，`app/api/router.py` 未挂技能路由。`frontend/src/App.tsx` 没有技能页。
- `agents.write` 只有系统管理员和租户管理员。任务要求成员可创建，不能复用这个资源。
- 技能不得直接访问数据库。持久化放在 Service，匹配仍留在 `SkillManager`。
- 当前 Alembic head 是 `c4a8e1b27d90`（`messages.status`）。`messages` 没有 `skill_names`。

Supervisor：

- 默认 `AGENT_ORCHESTRATION=self`。`app/agents/supervisor.py` 在 `langgraph` 时才构建图：`supervisor` 在 `research` 与 `draft` 之间路由，调研轮数上限后强制撰写。`run()` 只把最终 `draft` 写进 `answer`。
- `run_stream()` 先把图跑完，再发一条阶段「Supervisor 协作」、若干 token 和 `done`。没有逐个子任务的名称和结果。在这之后补发的事件是执行完成后的回放，不是节点开始时的实时进度。
- 成功走 Supervisor 时，`_build_pipeline()` 不接收技能上下文。缺少 `langgraph` 时捕获 `ImportError` 并回退自研管线。这是现有行为。技能上下文在本批仍不传给 Supervisor。
- `tests/test_supervisor.py` 只断言模块可导入，以及未安装 `langgraph` 时构造失败。没有「分派并汇总」用例。
- 报错文案写 `pip install "ai-assistant[langgraph]"`，`pyproject.toml` 里没有这个 extra。`requirements.txt` 里 Milvus、MCP 用注释表示可选；`croniter` 则在默认清单里。本批不把 `langgraph` 做成第二种。
- 现有图使用 `StateGraph`、`END`、`set_entry_point`、`compile`、`ainvoke`。LangGraph 1.0 文档仍保留 `set_entry_point`，并与 `add_edge(START, ...)` 等价。
- 前端 `frontend/src/hooks/useChatStream.ts` 不认识子任务事件。未知类型被忽略。`frontend/src/components/chat/StageTracker.tsx` 只画管线阶段。
- 仓库没有 CI 工作流。两组依赖验证用本地命令，不在本批新建 CI。

能力矩阵里 YAML Skill 与 LangGraph Supervisor 都保持 `Partial`。本批不把它们改成 `Implemented`。

## 已定的取舍

- 用户技能进数据库，不进进程单例。表为 `skills`。`scope` 为 `private` 或 `global`。私有技能只对创建者本人的对话生效，同一租户的其他成员既不能选中它，也不能在列表里看到它。系统全局技能由系统管理员创建，启用后所有登录用户都能看见并可能被对话选中。这是技能市场的数据铺垫，本批不做市场上架、定价、安装或审批。内置 YAML 仍是只读，页面上标成内置，不能通过接口修改、停用或删除。重启后用户技能还在。
- 列表：成员和 viewer 看到内置技能、已启用的系统全局技能，以及自己的私有技能。`tenant_admin` 额外看到本租户全部私有技能，以便停用和启用，不能跨租户。`system_admin` 与 `system_viewer` 看到全部租户、全部用户的私有技能，以及系统全局技能和内置技能。只有 `system_admin` 能修改、停用、启用、删除这些行，并新增系统全局技能。
- 名称仍叫技能，因为交付项和 `tasks.yaml` 用的是这个词。它是可校验的声明式技能，不是可执行工具技能。工具、检索和升级话术由服务端固定，用户不能提交。完整版本史留给 `PAGT-005`。状态保持 `Partial`。
- 创建接口用结构化字段，不接收任意 YAML。只开放关键词触发和 `prompt_injection`。正则、始终激活、工具模式仍只存在于现有加载器，创建接口遇到 `tools` 或非关键词触发返回 422。
- 私有技能在同一创建者名下名称唯一。系统全局技能的名称在全部全局技能中唯一，也不得与内置技能同名。唯一键放在 `name_key`：私有为 `private:{tenant_id}:{owner_id}:{name}`，全局为 `global:{name}`。不用「空租户参与联合唯一」的写法，避免 SQLite 把 NULL 当成互不相同。先查再插入只是体验优化；并发冲突以数据库 `IntegrityError` 为准，映射为 409。不同成员的私有技能可以使用同一个名字。
- 一次对话最多激活 1 条技能，取得过门槛且置信度最高的那条。这把现有的最多 3 条收成 1 条。
- 私有技能和系统全局技能的正文在进入系统提示前套上同一层固定围栏。围栏写明这段文字不可信，不能覆盖安全规则、权限、租户边界和工具策略。基础系统提示仍在围栏前面。内置 YAML 不套这层围栏。围栏不能保证模型服从；工具名单不从技能正文解析，这是代码边界。
- 创建时用 `PromptInjectionDetector`（阈值 0.5）检查用户提交的目的、关键词、约束、说明和示例。命中则 422 且不入库。此处不看 `SECURITY_BLOCK_ON_INJECTION`。检测器未覆盖的新句式仍可能入库，记为残余风险。
- 停用有两层。`SKILL_ENABLED=false` 时对话不选用任何技能，包括内置和系统全局。`POST /api/skills/{id}/disable` 与 `POST /api/skills/{id}/enable`：创建者可处理自己的私有技能；本租户 `tenant_admin` 可处理本租户私有技能；`system_admin` 可处理任意租户的私有技能和系统全局技能。看不见的技能返回 404。看得见但不能操作的返回 403。内置 YAML 不能靠这两个接口改状态，返回 404。状态已经是目标值时返回 200，不再写第二条审计。
- 命中的技能名写入助手消息 `skill_names`。非流式响应、正常流式落库、取消流式落库和会话详情使用同一份名单。未命中时读出来是空列表。对话页本批不展示这个名字。
- 成员可创建、修改、启用、停用和删除自己的私有技能。`tenant_admin` 可启用或停用本租户私有技能，不能改别人的正文，也不能删除，也不能新建系统全局技能。`system_admin` 可列出全部用户和租户的技能，并修改、启用、停用、删除，以及新建系统全局技能。`viewer` 与 `system_viewer` 可以看列表和详情，不能写。新资源名是 `skills`，不放宽 `agents.write`。`delete` 只给 `system_admin` 和 `member`，服务层再限制成员只能删自己的私有技能。
- 创建写审计 `skill_create`，修改写 `skill_update`，停用写 `skill_disable`，启用写 `skill_enable`，删除写 `skill_delete`。详情只记名称和关键词，不记整段提示词。
- `langgraph` 只做 optional extra，版本 `langgraph>=1.0.0,<1.1`。`requirements.txt` 只加注释行，不加入默认安装。默认编排保持 `self`。分派测试在未安装时失败，不跳过。回退测试用打桩的 `ImportError`，已安装和未安装都能跑。
- `delegations` 不用 LangGraph reducer。节点每次读取已有列表，复制后追加当前这一条，再把完整列表写回。只返回新的一条会覆盖前面的记录。图是串行的，`research` 在轮数上限内可以执行两次，两次都要留下。
- 子任务展示是图跑完后的摘要，事件版本为 1。不是节点开始时的实时流。实时流会改变超时和断线语义，本批不做。若以后改成实时流或把 `delegations` 入库，再写 ADR。
- 系统管理员跨租户读写技能改变租户隔离，按治理文档第 17 节单独立 `docs/adr/0004-skill-scope-and-admin.md`。Supervisor 的 `delegations` 仍只活在一次图执行的内存里，不另写 ADR。`subtask` 是观测投影。技能市场页面、定价和安装不在本批。

## 需要先改的允许路径

下表路径已经写入对应任务卡的 `allowed_paths`。实现时按任务卡执行，不要再缩小。

| 任务 | 增加的路径 | 原因 |
|---|---|---|
| SKILL-001 | `app/models/`、`app/models/__init__.py`、`app/core/database.py`、`app/core/security.py`、`app/audit/models.py`、`alembic/env.py`、`alembic/versions/`、`docs/adr/`、`docs/plans/plan_d2.md`、`docs/plans/implementation_skill_001.md`、`docs/reviews/2026-10-01-plan_d2评审.md`、`docs/product/as-is-capability-matrix.md` | 技能要有表、启动注册、权限、审计动作和迁移。跨租户管理改变租户隔离，单独立 ADR。矩阵只改 YAML Skill 行的原因，状态保持 `Partial` |
| SKILL-002 | `docs/plans/plan_d2.md`、`docs/plans/implementation_skill_002.md` | 实现说明单独成文。`frontend/src/` 已在允许路径内 |
| SUP-001 | `pyproject.toml`、`requirements.txt`、`README.md`、`docs/plans/plan_d2.md`、`docs/plans/implementation_sup_001.md`、`docs/product/as-is-capability-matrix.md` | 可选 extra 与注释行。矩阵只改 Supervisor 行的原因，状态保持 `Partial` |
| SUP-002 | `docs/plans/plan_d2.md`、`docs/plans/implementation_sup_002.md` | 实现说明单独成文。`frontend/src/` 已在允许路径内 |

## SKILL-001 创建技能并在对话中选用

- 目标：成员创建的私有技能，在关键词命中且为当次最高置信度时被选用；响应、完成的助手消息和已停止的助手消息都留下技能名。其他成员看不到这条私有技能。系统管理员可以列出全部租户和用户的技能，修改、启用、停用、删除，并新增系统全局技能。已启用的系统全局技能进入每个人的匹配集合。不含关键词、已停用或被注入检测拒绝时不选用、不入库。
- 风险：L1，高于普通文本写入。成员编写的说明会进入本租户对话的系统提示，可能影响回答。租户边界、工具名单和停用由代码执行，不靠模型。残余风险是检测器没认出的提示词仍会进入围栏。回退是回退本任务提交并降级本节的 Alembic 修订。不改已冻结的检索基线。
- 技能字段与契约：

| 契约 | 谁填写 | 约束 |
|---|---|---|
| Purpose | 用户，字段 `description` | 1–200 字 |
| Preconditions | 用户，字段 `keywords` | 1–8 个，每个 1–32 字，去空白后非空 |
| Workflow | 用户，字段 `system_prompt` | 1–2000 字 |
| Constraints | 用户，字段 `constraints` | 1–500 字。服务端再加固定围栏，见下 |
| Examples | 用户，字段 `example` | 1 条，1–200 字 |
| Tools | 服务端 | 固定空列表。请求里出现 `tools` 则 422 |
| RAG | 服务端 | 技能不选择检索范围 |
| Escalation | 服务端 | 固定短句：超出该技能范围时按普通对话回答，不声称已调用工具 |
| Tests | 本任务用例 | 选用、未选用、私有技能跨人不可见、系统全局技能可被他人选用、注入拒绝、停用与启用、删除、围栏、并发 409、列表不含正文、详情含正文、审计 |

  `description`、`constraints`、`system_prompt`、`example` 四段合计不超过 3000 字。名称 1–64 字，沿用加载器规则（小写字母、数字、下划线、连字符）。`version` 默认 `1.0`，只存储，不留变更史。

- 数据：新表 `skills`。字段：`id`、`tenant_id`、`owner_id`、`name`、`scope`（`private` 或 `global`）、`name_key`、`description`、`constraints`、`system_prompt`、`example`、`keywords`（JSON 数组文本）、`enabled`、`version`、时间戳。`name_key` 唯一。系统全局技能的 `tenant_id` 存空串。模型在 `app/models/skill.py`，并在 `app/models/__init__.py`、`app/core/database.py`、`alembic/env.py` 注册。内置清单用现有 `discover_skills()`，不写入该表。
- 迁移：一条 Alembic 修订，`down_revision` 为当前 head `c4a8e1b27d90`。`upgrade` 同时做两件事：创建 `skills`；给 `messages` 增加可空列 `skill_names`（文本，无 server default）。已有消息保持 `NULL`，不回填。SQLite 与 PostgreSQL 都只用 `create_table`、`add_column` 和唯一约束，不用只在一种数据库上成立的写法。`downgrade` 先删 `messages.skill_names`，再删 `skills`。读路径把 `NULL`、空串和 `[]` 都变成空列表。实施时用测试库跑一次升级和降级；PostgreSQL 若本环境没有实例，写入未验证项，不把 SQLite 结果写成两种库都已通过。
- 回滚：代码回退之后执行这一修订的 `downgrade`。会话和消息正文保留。技能行和技能名列被去掉。
- 权限：`app/core/security.py` 增加资源 `skills`。`read`：`system_admin`、`system_viewer`、`tenant_admin`、`member`、`viewer`。`write`：`system_admin`、`tenant_admin`、`member`。`delete`：`system_admin`、`member`。服务层再限制谁能改哪一行。
- 服务：`app/services/skill_service.py` 负责创建、列表、详情、修改、启用、停用、删除，以及把「内置清单 + 已启用的系统全局技能 + 当前用户自己的未停用私有技能」转成 `SkillManifest`。同名时私有技能覆盖系统全局技能，系统全局技能覆盖内置技能，避免匹配字典只留一条时丢了用户自己的技能。`SkillManager` 继续不持有 `Session`。创建时先做校验，再插入。捕获 `IntegrityError` 后回滚会话并返回 409。应用层预先查重不能代替这次捕获。
- 接口：`app/api/routes/skills.py`，挂到 `app/api/router.py`。列表和 201 用 `SkillListOut`：`id`、`tenant_id`、`owner_id`、`owner_username`、`tenant_name`、`name`、`description`、`keywords`、`source`、`scope`、`enabled`。不含 `system_prompt`、`constraints`、`example`。
  - `POST /api/skills`，要 `skills:write`，201。字段为 `name`、`description`、`keywords`、`constraints`、`system_prompt`、`example`，以及可选 `scope`（`private` 或 `global`，默认 `private`）。只有 `system_admin` 可以传 `global`，其他人传 `global` 返回 403。与内置重名，或违反 `name_key`，返回 409。长度、空值和注入检测失败返回 422。
  - `GET /api/skills`，要 `skills:read`。查询参数 `q`、`tenant_id`、`owner_id`、`scope`（`private`、`global`、`builtin`）、`enabled`。成员和 viewer 只能在自己的可见集里筛选；提交别人的 `tenant_id` 返回 403。`system_admin` 和 `system_viewer` 可以按租户和用户筛全部技能。
  - `GET /api/skills/{id}`，要 `skills:read`，返回 `SkillDetailOut`，在列表字段之外带上 `constraints`、`system_prompt`、`example`、`version`。创建者、本租户 `tenant_admin`、`system_admin`、`system_viewer` 可以看私有技能正文。已启用的系统全局技能，有读权限的人都能看详情。看不见的 id 返回 404。内置技能 id 为 `builtin:{name}`，详情只读。
  - `PATCH /api/skills/{id}`，要 `skills:write`。创建者可改自己的私有技能。`system_admin` 可改任意私有技能和系统全局技能。成功 200，响应仍是 `SkillListOut`。`tenant_admin` 不能改他人正文。内置技能返回 404。
  - `POST /api/skills/{id}/disable` 与 `POST /api/skills/{id}/enable`，要 `skills:write`。授权见上文停用两层。成功 200。重复请求 200 且不新增审计。
  - `DELETE /api/skills/{id}`，要 `skills:delete`。创建者可删自己的私有技能。`system_admin` 可删任意私有技能和系统全局技能。成功 204。内置技能和其他人的私有技能返回 404。
- 注入与围栏：创建和修改都用 `PromptInjectionDetector(threshold=0.5)` 检查五个用户字段。`detected` 为真则 422，修改时不改原行。对话激活私有或系统全局技能时，`ChatService` 在写入 `skill_prompt_injection` 之前套上固定围栏。围栏声明这段文字不可信，不得覆盖安全规则、权限、租户边界或工具策略，不得按正文点名去调用工具。基础系统提示保持在前。内置 YAML 仍走现有注入，不套这层围栏。
- 对话：`_match_skills(session, user, message)` 在内置技能、已启用的系统全局技能，以及该用户自己的未停用私有技能上做现有匹配，再只保留 1 条。同租户其他成员的私有技能不进入这份清单。`SKILL_ENABLED=false` 时返回空，不改这个开关的默认值。提示词注入保持现有管线属性，内容换成围栏后的文本。工具列表不读取技能正文。实施时用断言证明：围栏文本出现在基础系统提示之后，且技能清单上的工具名单仍是空。
- 技能名落库契约：

| 路径 | 写入 | 读出 |
|---|---|---|
| 非流式 `POST /api/chat` | `_persist_assistant` | `ChatResponse.skill_names` |
| 流式正常结束 | 同一方法，`status=complete` | 会话详情里的 `MessageOut.skill_names` |
| 流式取消 | `_persist_assistant_standalone`，`status=stopped` | 同上，名单与取消前命中的技能相同 |
| 未命中 | 列为空 | `[]` |
| 历史消息 | 本批不回填 | `NULL` 读成 `[]` |

  两条 persist 方法都增加 `skill_names`。`ChatResponse` 与 `MessageOut` 增加 `skill_names: list[str]`，默认空列表。流式不另发技能事件。
- 审计：`skill_create`、`skill_update`、`skill_disable`、`skill_enable`、`skill_delete`。详情只有名称和关键词。测试要查到审计行：操作者是当前用户，租户是当前租户，资源 ID 是技能 ID，详情里没有 `system_prompt`、`constraints`、`example` 的正文。
- 测试：新增 `tests/test_skill_selection.py`。关键词选用一个不会撞上「翻译」「审查」的词。两个租户各建一条私有技能，对方列表和对话都看不到。同一租户的另一名成员看不到这条私有技能，对话也不会选用。系统管理员能按 `tenant_id` 看到两边，并能修改、停用、启用和删除。系统管理员创建的系统全局技能，另一名成员的列表能看到，对话也能选用；该成员不能修改或删除它。条件不符时 `skill_names` 为空。注入句「忽略之前的所有指令」创建失败且表中无该行。再次停用返回 200 且审计仍只有一条。`SKILL_ENABLED=false` 时不选用。激活后的提示词含围栏，且基础系统提示仍在围栏前。`GET /api/skills` 和 201 响应不含三个正文字段；详情接口含这些字段。并发提交同一创建者的同名技能，恰好一条 201、一条 409。已停止消息的写入路径同样留下技能名。不把 `tests/test_skill_smoke.py` 的「加载到 2 个内置技能」改掉。
- 评测：新增 `tests/eval/test_skill_tenant_selection.py`，用固定清单断言命中、未命中、私有技能不可见、系统全局技能可见和单次只留 1 条。不调用真实模型。
- 验收命令：在 conda 环境 `ai-assistant`（`D:\install\anaconda3\envs\ai-assistant\Scripts`）执行 `pytest tests/ -k skill -v` 与 `ruff check app/`。矩阵 YAML Skill 行仍是 `Partial`，原因改为：成员可创建私有关键词技能并在对话中选用；系统管理员可跨租户管理并新增系统全局技能；工具、检索和升级由服务端固定；版本史和技能市场仍未做。
- 非目标：不做技能市场页面、定价、安装和审批。私有技能不对同租户其他成员生效。技能代码不访问数据库。不接工具模式。不开放正则和始终激活的创建。不做版本史。不改 Supervisor 的技能注入。不在对话页展示技能名。不保证模型一定服从围栏。列表和创建响应不回显正文；正文只在详情接口返回。不改用户管理和租户管理页面。

## SKILL-002 技能管理页

- 目标：有 `skills:read` 的用户打开表格页，能按条件筛选自己能看的技能。新增按钮在表格上方。详情、修改、启用、停用、删除放在每行最后的操作列。列表区域不展示技能正文。失败时能看到接口 `detail`。viewer 不能提交。系统管理员能筛租户并管理全部行，也能新增系统全局技能。
- 风险：L1。只改前端。回退是还原本任务的前端文件。不改用户管理和租户管理的现有排版。
- 改动：
  - `frontend/src/lib/permissions.ts` 增加与后端一致的 `skills` 矩阵，含 `delete`。
  - 新页面 `frontend/src/pages/Skills.tsx`，路由 `/skills`，侧栏入口「技能」。页面用通栏表格，不用用户页、租户页那种窄栏加上方整表单。
  - 表格上方是筛选条：关键词、范围、启用状态。`system_admin` 和 `system_viewer` 额外有租户筛选。同一行右侧是「新增技能」，只在 `can(role, 'skills', 'write')` 时出现。创建和修改走弹窗，不把表单堆在列表上面。
  - 列：名称、说明、范围、关键词、状态。系统管理员和系统只读角色再显示租户和创建者。最后一列是操作。
  - 操作列：每行都有「详情」。内置行只有详情。私有技能的创建者有修改、启用或停用、删除。`tenant_admin` 对本租户私有技能有启用或停用，没有修改和删除。`system_admin` 对私有技能和系统全局技能有修改、启用或停用、删除。`system_viewer` 和 viewer 只有详情。
  - 详情弹窗才显示约束、技能说明和示例。列表和创建成功后的表格不渲染这三段。
  - 删除先确认。409、422、403 的正文显示接口 `detail`。加载失败用现有错误态。筛选后没有行时用空状态。
- 验收：新增按钮在表格上方；操作在每行最后一列；成员创建后列表有该名称，表格上看不到技能说明正文，详情弹窗里看得到；停用后该行显示已停用；系统管理员能按租户筛选并看到新增系统全局技能的选项；viewer 看不到新增和写操作。`cd frontend; npm run typecheck` 与 `cd frontend; npm run build` 通过。浏览器核对上述布局。本批不新增前端测试运行器。用户页和租户页本批不改。
- 非目标：不做技能市场和版本对比。不在对话页展示选用结果。不重排用户管理和租户管理。

## SUP-001 Supervisor 分派并返回结果

- 目标：显式打开 LangGraph 编排且已安装规定版本时，一次请求里能看到任务分给 `research` 与 `draft`，并把撰写结果当作最终回答。未安装时构造失败，对话回退五阶段管线。默认配置仍返回五阶段管线。
- 风险：L1。多一个可选依赖，默认安装和默认编排都不变。回退是去掉 extra 与注释行，并卸掉该环境中的 `langgraph`。不改已冻结的检索基线。
- 依赖：`pyproject.toml` 的 extra 为 `langgraph>=1.0.0,<1.1`。`requirements.txt` 增加注释行，写明同一版本范围，以及验收安装命令是 `pip install -e ".[langgraph]"`。不把该行取消注释，也不把直接 `pip install langgraph` 写成验收。README 写明：装上后只有 `AGENT_ORCHESTRATION=langgraph` 才走 Supervisor；没装时对话回退五阶段。`.env.example` 保持 `self`。
- 安装核对：在声明的 conda 环境执行 `python -m pip install -e ".[langgraph]"`，再执行 `python -c "from langgraph.graph import END, StateGraph"`。验收不使用只执行 `pip install langgraph` 的方式，避免装到 extra 声明之外的版本。不重装整份 `requirements.txt`，也不构建镜像。镜像未装该包，写入未验证项。现有 `set_entry_point` 在 1.0 文档中仍然有效。若该环境里构造图失败，只允许把入口改成等价的 `add_edge(START, "supervisor")`，不改节点和路由。
- 分派记录：`SupervisorState` 增加内存字段 `delegations`，默认空列表。不注册 reducer。`research` 和 `draft` 都先复制 `state` 里已有的列表，追加一条 `{name, result}`，再返回这份完整列表。只返回新的一条会把前一轮盖掉。`run()` 把最终列表放到 `AgentState` 的新字段上，`answer` 仍是最终 draft。`delegations` 不入库。
- 摘要事件：`run_stream()` 仍先执行完图，再在 token 之前按顺序发出事件。这是完成后的摘要。事件类型 `subtask`。数据是 JSON 对象的字符串，版本字段 `v` 固定为 `1`。

| 字段 | 值 |
|---|---|
| `v` | `1` |
| `name` | `research` 或 `draft` |
| `status` | 有文本时 `done`；该节点没有文本时仍是 `done` |
| `summary` | 用户可见摘要。先经 `LogSanitizer` 脱敏，再截到 500 个字符。没有文本时用「没有文本结果」 |

  节点的完整 `result` 留在 `AgentState.delegations`，供测试断言固定句。SSE 只带 `summary`。图执行抛错时不发 `subtask`，沿用现有道歉句，异常文本不进入事件。聊天路由继续按事件类型原样转发，本批不改 `app/api/routes/chat.py` 的转发循环。
- 断线：摘要在 token 之前发出。客户端若在 token 阶段断开，已经发出的摘要不撤回，也不写入消息表。
- 不把 `skill_ctx` 传进 `SupervisorGraph`。
- 测试：
  - 已安装 extra 时，脚本化 LLM 让调度器依次给出 `research`、`draft`、`FINISH`，调研返回「调研记录」，撰写返回「汇总结果」。断言内存分派列表含这两个名字和完整结果，`answer` 等于「汇总结果」，流式 `subtask` 的 `summary` 等于这两句，`v` 为 1。另有一条：调度器在 `max_revisions` 允许的范围内连续两次返回 `research`，然后进入撰写。分派列表按顺序保留两条调研记录和一条撰写记录，对应的 `subtask` 也是三条，第一条调研摘要仍在。
  - 调研结果里放入一条会被 `LogSanitizer` 挡住的密钥样式，以及一段超过 500 字的文本。事件 `summary` 里看不到密钥原文，长度不超过 500。内存里的 `result` 仍保留完整文本，供测试核对「对外摘要」和「内部结果」不是同一份。摘要事件的正文里不出现工具原始返回或检索片段超出这 500 字的部分。
  - `AGENT_ORCHESTRATION=self` 时 `_build_pipeline()` 的类型是 `AgentPipeline`。
  - 把 `langgraph` 导入打成 `ImportError` 且配置为 `langgraph` 时，`_build_pipeline()` 仍返回 `AgentPipeline`。这条不依赖机器上是否安装了包。
  - 分派用例禁止 `importorskip` 或跳过。验收前必须先装上 extra。
- 验收：先执行 `python -m pip install -e ".[langgraph]"`，再执行 `pytest tests/ -k supervisor -v`，分派用例通过。回退用例在同一命令里通过。实现说明写明解释器路径、`import langgraph` 成功，以及未装包时的打桩回退通过。矩阵 Supervisor 行仍是 `Partial`，原因改为：显式打开且已用 extra 安装 `langgraph>=1.0.0,<1.1` 时能分派并在完成后给出摘要；默认编排仍是五阶段管线。
- 非目标：不把默认编排改成 Supervisor。不把 `langgraph` 放进默认安装清单。不重写五阶段管线。不把技能注入补进 Supervisor。不做 `research` / `draft` 之外的并行子代理。不改调研轮数上限的含义。不改成节点开始时的实时事件流。不新建 CI。

## SUP-002 任务过程展示

- 目标：Supervisor 路径在图执行完成后，能看到子任务名称、状态和摘要。默认五阶段对话不出现该区块。页面文案用「子任务摘要」，不用「正在执行」。
- 风险：L0。只改前端。回退是还原本任务的前端文件。
- 改动，均在 `frontend/src/`：
  - `useChatStream` 识别 `subtask`。`data` 解析为 JSON 后，只接受 `v === 1` 且 `name` 为 `research` 或 `draft`、`status` 为 `done` 的对象。缺字段或版本不是 1 则忽略。快照增加 `subtasks`，每项为 `name`、`status`、`summary`。
  - 在阶段条下方增加一块，标题为「子任务摘要」，仅当 `subtasks.length > 0` 时渲染。每行显示名称、状态和摘要。摘要在服务端已截断；块内仍允许滚动。
  - 没有合格事件时，DOM 里不存在这块，包括不渲染空标题。`StageTracker` 的五阶段占位保持原样。
- 验收：
  - 配置为 `langgraph` 且已安装 extra 时，发一条消息，在回复正文出现的同时或之前能看到 `research` 与 `draft` 的摘要。看不到「正在执行」之类的进行中文案。
  - 配置为 `self` 时，同一页面没有这块。
  - `cd frontend; npm run typecheck` 与 `cd frontend; npm run build` 通过。
  - 浏览器核对上述两态。没有前端测试运行器，不为本批新增。
- 非目标：不做图形化编排。不改阶段条的标准顺序。不把完成后的摘要显示成实时进度。不在默认对话里显示「Supervisor 协作」占位。

## 验证命令

按实现顺序，使用 conda 环境 `ai-assistant` 的解释器（`D:\install\anaconda3\envs\ai-assistant\Scripts`）。

```powershell
pytest tests/ -k skill -v
ruff check app/
cd frontend; npm run typecheck
cd frontend; npm run build
python -m pip install -e ".[langgraph]"
python -c "from langgraph.graph import END, StateGraph"
pytest tests/ -k supervisor -v
```

回退用例含在 `pytest tests/ -k supervisor -v` 里，通过打桩的 `ImportError` 覆盖未安装 extra 的对话路径。本批不新增 CI。SKILL-002 与 SUP-002 另有浏览器核对。实施时还要核对：围栏没有改写工具名单；SSE 摘要不是完整的工具或检索结果；SQLite 上这条迁移可以升级再降级。没有 PostgreSQL 实例时，把该项写入未验证项。

## 不纳入本批验收

下面四项不阻塞按本文实施。做到了可以写进实现说明，没做不算未完成。

- 迁移遇到「表或列已存在」时，先核对列类型和唯一约束与本文一致，再决定跳过。不一致就失败停下，不要静默跳过。
- 每个租户的技能数量上限，以及 `(tenant_id, enabled)` 索引。
- 租户 `is_active` 为假时，拒绝创建和停用技能。
- Supervisor 图失败时，除现有道歉句外，再写一条不含异常原文的 Trace 或原因码。

## 自查

对照了 `app/agents/skills/manager.py`、`app/agents/skills/base.py`、`app/services/chat_service.py`、`app/api/routes/chat.py`、`app/security/prompt_injection.py`、`app/security/log_sanitizer.py`、`app/models/conversation.py`、`alembic/versions/c4a8e1b27d90_add_message_status.py`、`app/agents/supervisor.py`、`tests/test_supervisor.py`、`requirements.txt`、`docs/governance/agent-harness-engineering.md` 第 17 节，以及 LangGraph 1.0 对 `StateGraph`、`END`、`set_entry_point` 的说明。

- 进程内 `register()` 不能实现租户隔离，重启也会丢掉用户技能。所以 SKILL-001 用表。
- 用户技能会进入系统提示。本批用长度、注入检测、固定围栏、单次一条、代码侧工具名单和两层停用来限制，并写明围栏不是模型服从性的证明。
- 技能名要在重新打开会话后仍在，所以两条助手消息写入路径都写 `skill_names`。
- `run_stream()` 先跑完再发事件。SUP-002 因此叫摘要，不叫实时过程。
- SSE 只带脱敏后的短摘要。完整结果留在内存分派列表。
- `langgraph` 与 `croniter` 分开：前者保持可选，后者已经在默认清单里。版本范围写死为 `>=1.0.0,<1.1`。
- 成功的 Supervisor 路径目前丢掉技能上下文。本批不修。
- 系统管理员跨租户管理技能已写入 ADR-0004。Supervisor 改成实时流或持久化 `delegations` 时再另写 ADR。
- `PAGT-005` 才做版本留痕。本批的 `version` 只是创建时的默认字符串。
- 私有技能只对创建者生效。系统全局技能启用后对所有人生效，作为技能市场的数据铺垫，本批没有市场页面。
- 同名并发以 `name_key` 唯一约束为准，`IntegrityError` 变成 409。
- `SkillListOut` 不带技能正文。详情接口才返回正文。停用或启用的重复请求返回 200，不重复记审计。
- `delegations` 每次写回完整列表。两次调研都留在列表和摘要事件里。
- SSE 摘要经过脱敏和 500 字截断。内部 `result` 不原样发给浏览器。
- `langgraph` 用 `pip install -e ".[langgraph]"` 安装。不把单独的 `pip install langgraph` 当作验收。
- 四张任务卡的允许路径、交付、验收、用例和回滚已与本文对齐。

允许路径已写入 `tasks.yaml`。实现时按该清单执行，不要再缩小。
