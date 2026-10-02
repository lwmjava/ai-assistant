# QUOTA-001、LIMIT-001、CLI-001、EXPORT-001：配额、限流提示、迁移与管理员命令、导出租户对话

> 状态：CLI-001、QUOTA-001、EXPORT-001、LIMIT-001 已实现，说明见 `docs/plans/implementation_cli_001.md`、`docs/plans/implementation_quota_001.md`、`docs/plans/implementation_export_001.md` 与 `docs/plans/implementation_limit_001.md`。2026-10-01 两轮评审的处置已写入本文，见 `docs/reviews/2026-10-01-plan_d3评审.md` 与 `docs/reviews/2026-10-01-plan_d3-codex评审处置.md`。做完这四条只完成交付包第 19 项，不是企业上线门禁通过。实现说明在各自完成后另写，不把未做项写成已完成。
> 来源：`tasks.yaml` 的 QUOTA-001、LIMIT-001、CLI-001、EXPORT-001；`docs/plans/plan_remaining_delivery.md` 对应四节；交付排期第 5 节 D3、第 6 节第 19 项。
> 日期：2026-10-01
> 截止：2027-03-05
> 依赖：四条都只依赖已完成的 SEC-004。彼此不阻塞。`CLI-001` 在关键路径上，完成后才解开 `REL-001`、`REL-002` 和 `PMCP-001`。

一次只实现一条。顺序是 CLI-001、QUOTA-001、LIMIT-001、EXPORT-001。

## 目标

1. 空库执行迁移命令后，应用能按现有启动路径起来。命令能创建系统管理员；该账号可以登录。同一用户名或已有系统管理员时再次创建被拒绝。密码不出现在日志里。
2. 系统管理员可为租户设置消息条数上限和源文件字节上限。未超限时对话和会写源文件的上传仍成功。超限时对话不新增会话或消息，新的源文件不落盘、不新增文档。未超限但摄取失败时，也不留下这一次新写的源文件。软删除不恢复源文件额度。同一数据库上的并发写入不能把用量写成超过上限。
3. 速率限制被触发且补充速率大于 0 时，对话页显示剩余等待秒数。倒计时结束前不能发送。未超限时不出现倒计时。补充速率小于或等于 0 时拒绝请求，且不计算等待秒数。默认安装仍是单进程内存限流。
4. 当前租户成员关系上的租户管理员能下载本租户对话的一份固定 JSON。这是取回本租户原文，不是训练数据放行。其他角色，以及切到另一租户后不再是该租户管理员的人，被拒绝。文件里没有其他租户的会话。`export_requested` 审计成功写入之后才开始返回文件。该审计不表示客户端已经收完。

## 已核对的现状

核对日期：2026-10-01。

配额：

- `tenants` 只有 `id`、`name`、`is_active`。没有消息或存储上限。
- `rag_documents` 有 `storage_path`，没有字节数。软删除只写 `deleted_at`，源文件仍留在磁盘（`RAGService.delete_document`）。
- 上传在 `app/api/routes/rag.py` 的 `upload_document`：先 `file.read()`，再 `save_source_file`，再解析和摄取。拒绝若发生在保存之后，磁盘上会留下文件。
- 对话在 `ChatService.chat` 与 `chat_stream`：限流在 `_apply_input_security`，通过后 `_resolve_conversation` 会在没有 `conversation_id` 时立刻提交新会话，然后 `_persist_user` 提交用户消息。配额若放在这两步之后，超限仍会留下空会话或用户消息。
- 文本粘贴摄取不调用 `save_source_file`。批量上传 `POST /import-jobs/upload` 和 URL 导入在 `app/rag/import_jobs.py` 里会写源文件。源文件配额必须盖住每一个 `save_source_file` 调用。纯文本摄取和向量索引体积不在本任务设闸。

限流：

- `RateLimiter.allow` 使用 Token Bucket，拒绝时只返回 `(False, 剩余 token 数)`，没有等待秒数。
- 默认 `SECURITY_RATE_LIMIT=false`，`SECURITY_RATE_LIMIT_RATE=60`（每秒补充），`SECURITY_RATE_LIMIT_CAPACITY=60`。按这个默认速率，超限后的等待通常不到 1 秒。算法本身可以算出等待时间：缺口除以补充速率。
- 非流式对话把 `SecurityRejectedError` 映射成 HTTP 429，正文是纯字符串，没有 `Retry-After`。
- 流式对话在限流时发出一条 `error` 事件后返回，此时还没有写会话。前端 `useChatStream` 把该事件当普通错误文案，`Composer` 没有倒计时。

导出：

- 没有导出路由。`app/api/router.py` 未挂导出。
- 审计枚举里没有导出动作。现有 `audit_event` 写入失败时只记日志，导出不能复用这条旁路。

命令：

- 迁移骨架是 `python -m app.core.migration migrate`，会交互确认，`--yes` 可跳过。它调用已有的 Alembic `upgrade("head")`。
- 没有 `ai-assistant` 控制台入口。`pyproject.toml` 没有 `[project.scripts]`。
- 首个管理员靠 `INITIAL_ADMIN_USERNAME` / `INITIAL_ADMIN_PASSWORD` 在启动时创建，或走 `/setup`。库里已有任意用户时，环境变量路径直接返回，不报「重复」。
- 当前 Alembic head 是 `d7c2a91e4b18`。

## 与需求文档的取舍

需求文档比任务卡宽。本批以 `tasks.yaml` 的交付、非目标和验收为准。下面这些不在本批做。

| 需求文档 | 本批 |
|---|---|
| 配额含 Token、自然月重置、手动重置、定时任务触发前检查 | 只做消息条数和源文件字节。不重置。不计量 Token。不把索引体积算进配额 |
| 配额页、租户设置里的配额查看 | 只做系统管理员 API。不改租户页 |
| 限流按用户、租户、IP 三套，并换成共享存储 | 沿用现有「用户 + 租户」键和进程内 Token Bucket。多实例留给 `OPS-001` |
| 导出 Alpaca 与 ShareGPT、时间范围、会话筛选、导出前脱敏 | 一种固定 JSON，内容是原文。不筛选。不改写消息正文。这是安全例外，不是训练数据出口 |
| `init` / `start` / `stop` / `status` / `logs`、交互式改密 | 只做 `migrate` 和创建系统管理员 |

若要改回需求文档的宽口径，先改任务卡再实现。

## 已定的取舍

- 未设置的上限是空值，表示不限制。已有租户升级后行为不变。`0` 表示该维度不允许新增。负数返回 422。
- 消息计量是本租户 `messages.role = "user"` 的条数，跨全部会话累加，不按自然月清零。助手回复不计数。检查与用户消息插入在同一事务里：先更新该租户行以取得写锁，再数已有用户消息，超限则回滚，不创建会话、不写消息。非流式和流式都走这处。工作流桥调用 `chat` 时同样经过这里。同一数据库上的并发不能把用户消息数写成超过上限。多副本各写各的库不在本批，留给 `OPS-002`。
- 这是源文件字节配额，不是租户全部存储。计入每一个还在磁盘上、由 `save_source_file` 写下的文件：单文件上传、批量上传、URL 或带远程地址的重解析。纯文本摄取不写源文件，不设闸。向量和分块体积不计入。`source_bytes` 有值且文件还在，或历史行没有该列但 `storage_path` 上的文件还在，都计入。`FileNotFoundError` 表示文件已不在，计 0。其他读取失败拒绝本次会增加占用的操作，返回 503，不按 0 放行。软删除不删源文件，额度不恢复。物理删除源文件之后不再计入。保存源文件之后若解析或摄取失败，删除这一次新写的文件；删除失败则本次请求失败，不把上传报成成功。不在本批做后台清扫。`source_bytes` 只在文档行提交成功时写入。批量上传在写下任何新文件之前，用同一把租户行锁判断「已用 + 本批全部字节」。
- 配额与限流分开。配额用尽是 429，正文为 `{"code":"quota_exceeded","limit_type":"messages"|"source_bytes","used":整数,"limit":整数}`，不带其他租户的标识。限流仍是 429，正文为 `{"code":"rate_limited","retry_after_seconds":整数或 null}`。两种失败都发生在写入之前。统一九种错误码信封仍由 `ERR-001` 收口。
- 设置配额只允许 `system_admin`。接口是 `GET/PATCH /api/admin/tenants/{tenant_id}/quota`。`PATCH` 必须带不超过 200 字的 `reason`。审计动作 `quota_update` 与配额变更同一事务提交，详情含操作者、目标租户、消息上限旧值与新值、源文件上限旧值与新值、reason。不含对话内容。审计关闭或写入失败时回滚变更并返回 503。不沿用会吞掉异常的 `audit_event`。
- 等待秒数只在补充速率大于 0 时计算：拒绝时 `ceil((本次消耗 - 当前 token) / 补充速率)`，至少 1 秒。速率小于或等于 0 且限流开启时，直接拒绝，不除法，`retry_after_seconds` 为 null，响应不带 `Retry-After`。不改默认速率和容量。默认 `SECURITY_RATE_LIMIT=false`，默认安装不出现倒计时。验收测试自行打开限流并把速率调小。
- 流式限流增加一条 `rate_limit` 事件，数据为 `{"retry_after_seconds": N 或 null}`，然后再发原有的错误文案，文案不带桶内 token 数。非流式 429 在秒数不为 null 时增加响应头 `Retry-After`。对话页只在收到大于 0 的秒数时显示倒计时并禁用发送；到 0 后恢复。秒数为 null 时显示拒绝说明，不显示会自己走完的倒计时。其他错误不显示倒计时。README 写明：限流状态只在本进程有效，重启即清空；在 `OPS-001` 完成前不得水平扩展 API 进程。
- 导出只允许当前租户成员关系上的角色为 `tenant_admin`。不看 `users.role`。`member`、`viewer`、`system_admin`、`system_viewer`，以及在当前租户里只是 member 的人，一律 403。请求体里若带有与令牌不一致的租户号，返回 403，不导出。只查询令牌上的租户。安全例外：文件内容是消息原文，供该租户管理员取回，不作为可直接用于训练的数据出口，不做字段脱敏，不做审批流。第 20 项完成后再清点是否补脱敏。
- 导出 JSON 固定为：`tenant_id`、`exported_at`、`conversations[]`。每条会话含 `id`、`title`、`messages[]`（`role`、`content`、`created_at`）。会话和消息都按创建时间、主键稳定排序。先按页累计条数和 UTF-8 字节，超过 5000 条或 32 MiB 立即停止，不把已读正文写入日志。单条消息本身超过 32 MiB 同样返回 413，错误码 `export_too_large`，不返回半份 JSON，不写成功审计。未超限时先提交审计 `export_requested`，详情是导出人、租户、会话数和消息数，不写正文。该记录表示服务端开始返回，不表示客户端已经收完。不另记下载完成。审计成功后才把同一份 JSON 作为 `POST /api/export/conversations` 的附件返回。`AUDIT_ENABLED=false` 或审计写入失败时返回 503，响应体没有对话内容。
- 命令入口是安装后的 `ai-assistant`。`ai-assistant migrate` 等价于现有升级到 head；无待执行迁移时成功退出并说明已是最新。`--yes` 跳过确认。保留 `python -m app.core.migration`。
- `ai-assistant admin create-superuser --username <name>` 从环境变量 `INITIAL_ADMIN_PASSWORD` 读密码。密码短于 8 位时非 0 退出，不写用户，不打印密码。创建放在同一数据库事务里：写入前数已有 `system_admin`，已有则拒绝；用户名冲突按非 0 退出。不增加「全局只能有一个系统管理员」的约束。日志只写用户名。
- 环境变量启动引导和 `/setup` 保持原样。本批迁移验收只在临时 SQLite 上证明升级和降级。不把 PostgreSQL 演练算进本批，该验证仍是 `NFR-005`。

## 需要先改的允许路径

下表已写入对应任务的 `allowed_paths`。未列入的文件不改。

| 任务 | 增加的路径 | 原因 |
|---|---|---|
| CLI-001 | `docs/plans/plan_d3.md`、`docs/plans/implementation_cli_001.md`、`pyproject.toml`、`docs/product/as-is-capability-matrix.md` | 控制台脚本入口、实现说明、能力矩阵只补命令行缺口 |
| QUOTA-001 | `docs/plans/plan_d3.md`、`docs/plans/implementation_quota_001.md`、`alembic/versions/`、`app/schemas/`、`app/audit/models.py`、`app/rag/import_jobs.py`、`docs/product/as-is-capability-matrix.md` | 租户上限和文档字节数要迁移；批量上传和 URL 导入也写源文件 |
| LIMIT-001 | `docs/plans/plan_d3.md`、`docs/plans/implementation_limit_001.md`、`app/services/chat_service.py`、`app/api/routes/chat.py`、`README.md`、`docs/product/as-is-capability-matrix.md` | 等待秒数要进流式事件；单进程限制要写进 README |
| EXPORT-001 | `docs/plans/plan_d3.md`、`docs/plans/implementation_export_001.md`、`app/api/router.py`、`app/schemas/`、`app/audit/models.py`、`docs/product/as-is-capability-matrix.md` | 新路由要挂上；审计要有导出动作 |

## CLI-001 migrate 与管理员命令

实现说明：`docs/plans/implementation_cli_001.md`。

- 目标：空库执行 `ai-assistant migrate --yes` 后，现有启动路径可以打开数据库。`ai-assistant admin create-superuser` 创建的系统管理员可以登录。第二次创建被拒绝。日志和标准输出都不含密码。
- 现状：见上文「命令」。
- 方案：新增 `app/cli.py`，用标准库 `argparse` 解析两个子命令。`migrate` 调用 `app.core.migration` 里已有的升级函数，不重写 Alembic 配置。`create-superuser` 在一个数据库事务里先数 `system_admin`，人数已大于 0 则拒绝；密码短于 8 位则拒绝。用户名唯一约束冲突时非 0 退出。不打印密码。`pyproject.toml` 增加 `[project.scripts] ai-assistant = "app.cli:main"`。README 在现有环境变量说明旁边加上这两条命令，不删掉环境变量和 `/setup`。
- 交付：迁移命令、创建管理员命令、README 用法、测试。
- 非目标：不实现 `init`、`start`、`stop`、`status`、`logs`。不强制首次登录改密。不从命令参数读取密码。不限制管理接口继续创建更多系统管理员。不在本批做 PostgreSQL 演练。
- 验收：临时空 SQLite 上 `migrate --yes` 退出码 0，随后能读到 `alembic_version`。用该库创建的管理员可经现有登录接口取得令牌。再执行一次创建，退出码非 0，用户数不增加。短密码不写用户。同一库上两次重叠的创建最多成功一次。测试捕获的日志和命令输出不含该密码。
- 验证：`pytest tests/test_cli.py -v`；`ruff check app/cli.py`。命令测试用子进程，工作目录和 `DATABASE_URL` 指向临时文件。

## QUOTA-001 租户配额与超限拒绝

实现说明：`docs/plans/implementation_quota_001.md`。

- 目标：系统管理员设置某租户的消息上限和源文件字节上限后，额度内的对话和源文件写入成功；超出后对话不新增会话或用户消息，新的源文件不落盘、不新增文档。同一数据库上并发请求不能把用量写成超过上限。
- 现状：见上文「配额」。
- 方案：`tenants` 增加可空整数 `message_limit`、`storage_limit_bytes`。`rag_documents` 增加可空整数 `source_bytes`，只在文档行提交成功时写入，值等于该源文件字节数。迁移修订接在 `d7c2a91e4b18` 之后。升级用添加可空列。降级用 `batch_alter_table` 删除这三列，并在临时 SQLite 上先升级再降级：降级后列消失，降级前已有的对话和文档还在。若这次降级测试出现数据丢失或删列失败，停止实现并改记「回滚只回退代码，列保留」。不在本批对 PostgreSQL 做演练。`app/services/quota.py` 在锁住该租户行的事务里读取用量并决定是否写入。对话的两处入口使用这个事务。每一个 `save_source_file` 之前做同样的判断，包括 `upload_document`、`/import-jobs/upload` 和 URL 拉取。超限返回上文约定的 429，且不调用保存。未超限时，保存之后若解析或摄取失败，删除这一次新写的源文件；删除失败则请求失败。读取源文件遇到 `FileNotFoundError` 计 0，遇到其他 IO 错误则 503，不继续写入。`PATCH` 与 `quota_update` 审计同一事务提交，审计失败则配额不变。
- 交付：设置接口、对话前检查、全部源文件入口的检查、可降级的迁移、并发测试。
- 非目标：不计费。不做用户级配额。不计量 Token。不按月重置。不改租户页。不给纯文本摄取设闸。不计量向量索引。不在本批做物理删除、后台清扫或多副本锁。
- 验收：上限为空时，现有对话和上传测试行为不变。消息数已等于上限时，再发一条后会话数和用户消息数不变。本次上传或本批上传会使源文件超过上限时，文档数不变，且该次相对路径下没有新文件。URL 导入越过上限时不写新文件。未超限但摄取失败时，该次新文件不在。软删除后源文件用量不下降。源文件被物理删除后不再计入。源文件路径存在但无法读取时，上传得到 503，不新增文件。未超限的对照请求成功。非系统管理员调用设置接口得到 403。缺少 reason 或审计写入失败时，上限不变。临时库上升级后再降级，三列消失，已有对话和文档仍可读取。同一库上并发越过上限的请求里，最终用户消息数和源文件字节不超过上限。
- 验证：`pytest tests/test_quota.py -v`；`ruff check app/`。测试使用临时库，不碰 `data/ai_assistant.db`。降级用例单独用一块临时 SQLite。

## LIMIT-001 限流提示与倒计时

实现说明：`docs/plans/implementation_limit_001.md`。

- 目标：连续请求打满当前桶之后，对话页出现倒计时，倒计时结束前发送按钮不可用。未超限时页面上没有倒计时。
- 现状：见上文「限流」。
- 方案：`allow` 在补充速率大于 0 且拒绝时算出等待秒数，写入 `SecurityContext.retry_after_seconds`。速率小于或等于 0 时拒绝并把该字段留空，不做除法。桶的补充公式在速率大于 0 时不变。`chat_stream` 在限流分支先产出 `rate_limit` 事件，再产出现有错误文案，然后返回。非流式 429 仅在秒数不为空时带 `Retry-After`。前端在秒数大于 0 时显示倒计时并禁用发送。README 写明单进程限制和重启清空。默认速率配置不改，默认开关仍关闭。
- 交付：响应中的剩余秒数、对话页倒计时、README 中的单进程限制。
- 非目标：不换成滑动窗口或 Redis。不新增按 IP 的另一套限流。不在知识库页显示倒计时。不把本批写成多实例限流已经生效。
- 验收：测试里把容量设为 1、补充速率设为每秒 1 个，第二次对话得到等待秒数且没有新的用户消息。速率为 0 时第二次请求被拒绝，响应没有 `Retry-After`，测试进程不因除零退出。前端在收到大于 0 的 `retry_after_seconds` 时禁用发送并显示该数字；未收到或为 null 时不显示会走完的倒计时。未超限请求不产生 `rate_limit` 事件。README 能找到「完成 `OPS-001` 之前不要水平扩展」这句话。
- 验证：`pytest tests/test_rate_limit_retry.py -v`；`cd frontend; npm run typecheck`。页面行为在实现时用浏览器走一次超限和一次未超限；若当时没有浏览器，用组件测试或渲染脚本代替，并在实现说明里写明未用浏览器的部分。

## EXPORT-001 导出租户对话

实现说明：`docs/plans/implementation_export_001.md`。

- 目标：当前租户成员关系上的租户管理员下载的 JSON 只含自己令牌所在租户的会话。其他角色得到 403。`export_requested` 成功写入之后才开始返回文件。审计不含消息正文，也不表示客户端已经收完。
- 现状：见上文「导出」。`switch_tenant` 不修改 `users.role`。`audit_event` 写入失败时只记日志，不向调用方抛错。
- 方案：`app/services/export_service.py` 先取当前用户在令牌租户上的成员关系，角色不是 `tenant_admin` 则 403。按创建时间和主键分批读取，累计条数和 UTF-8 字节，达到 5000 条或 32 MiB 就返回 413，不把正文写入日志，不写成功审计。未超限时先提交 `export_requested`，成功后再把同一份 JSON 作为附件返回。审计关闭或写入失败返回 503，响应体没有对话。路由是 `POST /api/export/conversations`。请求体不接收租户号；若仍传入且与令牌不一致，403。`app/api/router.py` 挂上该路由。
- 交付：导出接口、先审计后下载、越权与超限测试。
- 非目标：不训练模型。不提供 Alpaca 或 ShareGPT。不按时间或会话筛选。不脱敏改写正文。不做导出页面。不把导出做成 GET。不记录客户端下载完成。
- 验收：两个租户各有会话时，甲租户的租户管理员下载结果的 `tenant_id` 和全部会话都属于甲。乙租户的会话不在文件中。成员、viewer、系统管理员得到 403，且没有 `export_requested`。在甲租户是租户管理员、受邀以 member 加入乙并切换到乙之后，导出得到 403，响应和审计详情里都没有乙的对话。审计写入失败或关闭审计时得到 503，响应不含对话正文。超过 5000 条、累计超过 32 MiB，或单条消息本身超过 32 MiB 时得到 413，没有成功审计，响应不是半份导出，日志不含该消息正文。成功时审计动作是 `export_requested`，详情能看到会话数，看不到消息正文，且该审计行早于响应体开始写出。
- 验证：`pytest tests/test_export_conversations.py -v`；`ruff check app/`。

## 风险与回滚

- 配额检查与写入共用锁住该租户行的事务。同一数据库上的并发不能越过上限。多副本不在本批关闭。
- 软删除不删源文件，源文件用量不恢复。管理员删了文档后额度仍占用，直到源文件被物理删除。物理清理和后台对账不在本任务。
- 限流状态仍在进程内存里。重启后倒计时失效。多实例不共享。README 禁止在 `OPS-001` 之前水平扩展。这不是多实例上线结论。
- 导出把消息原文交给当前租户的租户管理员。这是已记录的安全例外，不是训练数据放行。审计、413 和 503 的响应不带正文。单次 5000 条或 32 MiB 在累计过程中截停，不是先拼完再判断。
- 创建管理员失败路径若把异常参数打进日志，可能带上密码。命令只把用户名传给日志；测试用哨兵密码断言日志和输出。
- 回滚：各自回退该任务的提交。配额迁移在临时 SQLite 上证明降级能删掉三列且旧数据仍在之后，才把 downgrade 当作回滚步骤。证明不了时，回滚只回退代码，列留在库里。PostgreSQL 不在本批演练。不改已冻结的检索基线。

## 未纳入本批

- D4 的 Swagger、备份记录、测试门禁和检索实验。
- 打磨族 `PAGT`、`PWFL`、`PRAG`、`PMCP`，以及 `NFR`、`OPS`。其中 `OPS-001` 才关闭多实例限流，`OPS-002` 才关闭多副本配额，`NFR-005` 才验证 PostgreSQL 迁移。
- 需求文档里的 Token 配额、月重置、双格式 SFT、导出筛选、脱敏改写、CLI 全集和配额页面。原文导出的安全例外保持到这次缺口清点，不在本批改成训练出口。
