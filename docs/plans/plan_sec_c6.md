# SEC-001～SEC-004：上传限制、日志与界面错误、Milvus 门槛、16/20 清点

> 状态：SEC-001、SEC-002、SEC-003、SEC-004 已实现。SEC-003 的五条门槛未通过，默认向量库仍是 local。SEC-004 清点见 `docs/plans/record_delivery_16.md`。第 1、2、3、4、13、15 项未完成，80% 未达到。
> 来源：`tasks.yaml` 的 SEC-001～SEC-004；`docs/plans/plan_remaining_delivery.md`；交付排期第 6 节第 13 项。
> 日期：2026-09-27
> 分支：`feat/sec-c6`
> 截止：2026-12-25
> 依赖：QA-004、TEN-003、INST-002、CHAT-005、ROUTE-002、SAND-002 均已完成。

一次只实现一条。顺序是 SEC-001、SEC-002、SEC-003、SEC-004。SEC-002 与 SEC-001 没有代码依赖，但第 13 项交付包同时包含上传限制和日志脱敏，所以 SEC-004 等 SEC-002 完成后再清点。本文件写全每条的目标、现状、方案、非目标和验收；实现时沿用对应小节，不必再写第二份计划。

## 已核对的现状

- 单文件上传在 `POST /api/rag/documents/upload`，批量上传在 `POST /api/rag/import-jobs/upload`。两条都先把整个文件读进内存，再 `save_source_file`。控制台只用单文件接口。批量接口在读文件之前就 `create_import_batch` 并提交；每个已成功文件的 `create_upload_import_job` 也会提交。
- 解析器注册表拒绝未知扩展名，提示支持 txt、md、json、xml、csv、doc、xls、ppt、docx、xlsx、pptx、pdf。`tests/test_rag.py` 的 `test_upload_rejects_non_text` 期望 `.bin` 返回 400。
- 源文件存储的扩展名白名单和解析器不一致：存储允许 html、htm，不允许 doc、xls、ppt；未知扩展名会改成 `.bin` 后仍写入磁盘。配置里没有上传大小上限。
- 知识库页在请求发出前只放行 `.txt` 和 `.md`，其它类型显示「后端仅支持 .txt 与 .md」。这和解析器注册表不一致，服务端的拒绝文案到不了页面。
- `LogSanitizer` 能遮蔽 password、token、Bearer JWT 和 `sk-` 一类密钥，但没有挂到日志系统上。字段正则在空格和逗号处截断，`password=correct horse` 只会遮住 `correct`。对话、登录、上传路径没有统一的脱敏过滤器。`Filter.filter()` 执行时，回溯还在 `exc_info` 里，`exc_text` 通常仍是空的。
- 登录失败返回「用户名或密码错误」，不记录密码。初始管理员日志只写用户名。上传失败的 `logger.exception` 会把回溯写进日志。
- 对话、登录、知识库上传的失败提示直接展示接口 `detail`。流式对话的 `error` 事件展示 `str(exc)`。连接中断走 `useChatStream` 的 `onError`，页面固定显示「连接中断」。FastAPI 默认 500 不带回溯；一旦 `detail` 或事件正文里出现回溯，页面会原样显示。前端没有测试运行器。
- 产品方案 §6.6 要求统一错误体 `{error: {code, message, ...}}`。SEC-002 的非目标是不重写全部错误码。本批保持现有 `{detail: ...}`。
- `RAG_VECTOR_STORE` 默认是 `local`。ADR-0002 第 5 节五条是：摄取后可检索、重解析后旧向量不可检索、删除后向量清理且计数可核对、租户过滤与 ADR-0001 一致、有可重复命令和失败回滚说明。未安装或未连上 Milvus 的用例可以 skip，skip 不能当通过。
- Compose 里有 `milvusdb/milvus:v2.5.11`，端口不发布到宿主机，应用容器的 `RAG_VECTOR_STORE` 仍是 `local`。INST-002 未安装 `pymilvus`。
- 交付排期第 6 节 1–16 是 2026-12-25 的 80% 分母。1–12、14–16 对应的任务已完成。第 13 项是本批。17–20 不计入这次 80%。

## 需要先改的允许路径

允许路径和 `SEC-004.depends_on` 里的 `SEC-002` 已经写入 `tasks.yaml`。下面是当时补上的范围。除此之外仍禁止改业务代码范围外的文件。

| 任务 | 增加的路径 | 原因 |
|---|---|---|
| SEC-001 | `app/api/routes/rag.py`、`.env.example`、`README.md`、`docs/plans/plan_sec_c6.md` | 拒绝必须发生在写盘之前；配置变更要同步示例和说明 |
| SEC-002 | `app/main.py`、`docs/plans/plan_sec_c6.md` | 进程启动时挂上日志过滤器 |
| SEC-003 | 已含 `docs/`、`tests/` | 只记证据，不改向量库实现 |
| SEC-004 | 已含 `docs/plans/` | 只写清点 |

`SEC-004.depends_on` 已补上 `SEC-002`。

## 已定的取舍

- 单文件上限默认 `10485760` 字节（10 MiB），环境变量 `RAG_UPLOAD_MAX_BYTES`。等于上限可以上传，超过返回 413。正文写明上限，例如「文件超过 10MB 上限」。
- 允许的扩展名默认与解析器注册表现有提示一致：`txt,md,json,xml,csv,doc,xls,ppt,docx,xlsx,pptx,pdf`。环境变量 `RAG_UPLOAD_ALLOWED_EXTENSIONS`，逗号分隔，不带点，比较时忽略大小写。只看最后一个后缀。
- 类型不符返回 400，正文列出当前允许的扩展名。`.bin` 仍然是 400，现有上传测试保持这个状态码。
- 先读入内存再判断大小。本批不做按块截断。超限或类型不符时不调用 `save_source_file`。批量请求先检查全部文件，全部通过后才创建批次并写盘。`UploadFile.read()` 只能读一次，检查用过的字节留给后续保存。
- 不做魔术数字、病毒扫描、统一错误码表，也不把 html、htm 加进允许列表。URL 导入不走这条限制。
- 页面上的上传说明改成与默认配置相同的扩展名和 10MB。真正拒绝时展示接口返回的 `detail`。
- 日志过滤器挂在根 logger 上。消息和格式化之后的回溯都经过 `LogSanitizer`，并清空 `args`。样本口令含空格；整段遮不住时就改 `app/security/log_sanitizer.py`。过滤失败时丢掉这条日志的原文，改写固定占位，避免脱敏异常把密钥打出去。
- 对话页、登录页、知识库上传这三处，正文里出现 `Traceback (most recent call last)` 或 `File "...", line` 时，改成「操作失败，请稍后重试」。其它已有的业务短句继续显示。接口响应不含 `Traceback` 由 pytest 锁定。页面文案在实现时各看一次。流式连接中断仍显示「连接中断」，不再套这一层替换。
- SEC-003 不安装 `pymilvus`，不改编排，不改 `RAG_VECTOR_STORE` 默认值，不改 `app/rag/vectorstore/milvus.py`。五条都写下准备执行的命令。跑不起来时结果写「未执行」和原因，skip 不算通过。
- SEC-004 不把缺证据的项标成完成，不把第 17–20 项算进 80%。Milvus 五条未全部通过时，第 13 项里的向量库子项记未完成，上传限制和日志脱敏按各自证据单独记录。

## SEC-001 上传类型与大小限制

已实现，行为见 `docs/plans/implementation_sec_001.md`。

- 目标：知识库拒绝不在配置里的扩展名和超过上限的文件，页面能看到这条拒绝原因。允许范围内的小文件仍能上传。
- 风险：L1。拒绝发生在写盘之前，回退本任务提交即可。已冻结的检索基线不动。
- 改动：
  - `app/core/config.py`：增加 `RAG_UPLOAD_MAX_BYTES` 与 `RAG_UPLOAD_ALLOWED_EXTENSIONS`。
  - `app/rag/`：新增纯函数，输入文件名和字节长度，返回允许，或返回类型错误 / 大小错误。扩展名列表从配置解析。大小用字节长度，不信任文件名里的数字。
  - `app/api/routes/rag.py`：单文件在 `save_source_file` 之前调用该函数。批量先把本请求的文件全部读入并检查，全部通过之后才 `create_import_batch` 和 `save_source_file`。类型错误 400，大小错误 413。任一文件不合规时，不创建批次、不创建导入任务、不写源文件。检查用过的字节留给后续保存，不再次 `read()`。
  - `frontend/src/pages/Knowledge.tsx`：去掉「仅 .txt 与 .md」的本地拦截。上传区写明默认扩展名和 10MB。失败 toast 展示接口 `detail`。
  - `.env.example`、`README.md`：写上两个配置项和默认值。
  - `tests/`：非法扩展名 400 且磁盘上没有新文件；超限 413 且没有新文件；上限边界内的 txt 仍成功。批量请求里第二个文件超限时返回 413，且没有新的批次、导入任务和源文件；第一个文件即使合法也不落盘。
- 非目标：不增加解析格式。不改切分、Embedding、检索。不做魔术数字和 ClamAV。不改 URL 导入。不修改 `document_storage` 的扩展名集合。同一请求按文件顺序返回第一个错误。
- 验收：非法类型和大文件被拒绝且未落盘；允许的小文件仍可上传；`pytest tests/ -k upload -v` 通过；`cd frontend; npm run typecheck` 通过。
- 验证：上面两条命令的退出码写入实现说明。页面上用一个 `.bin` 和一个超过 10MB 的文件确认 toast 文案；这一步在实现该任务时做。

## SEC-002 日志脱敏与界面错误提示

已实现，行为见 `docs/plans/implementation_sec_002.md`。

- 目标：对话、登录、上传打出的日志里没有密钥、令牌和密码；这三处失败不把回溯显示在页面上。
- 风险：L1。过滤器只改日志文本，不改业务结果。回退本任务提交即可。
- 改动：
  - `app/security/`：增加日志 `Filter`。先格式化 `exc_info` 得到回溯文本，再对消息和回溯调用 `LogSanitizer.sanitize`，然后清空 `args`，避免 `%` 再次格式化。脱敏过程自己出错时，消息改成「日志已省略」。提供一个安装函数，把过滤器挂到根 logger，重复调用不叠两层。带空格的口令若只能遮住第一段，就改 `log_sanitizer.py` 的字段正则，使整段值被换成 `***`。
  - `app/main.py`：应用创建时调用该安装函数。
  - `frontend/src/lib/http.ts`：增加纯函数，识别回溯特征并换成「操作失败，请稍后重试」。
  - `frontend/src/pages/Login.tsx`、`frontend/src/pages/Chat.tsx`、`frontend/src/hooks/useChatStream.ts`、`frontend/src/pages/Knowledge.tsx`：登录失败、对话生成失败、流式 `error` 事件、上传失败都经过这个函数。`onError` 里的「连接中断」保持原样，不再套这一层。知识库的删除、重建、发布不在本任务范围。
  - `tests/`：过滤器把含空格的 `password=`、`token=`、`Bearer eyJ...`、`sk-` 长密钥整段换成 `***`，回溯文本里的同一密钥也不出现。用测试客户端走登录失败、上传失败、对话失败，捕获日志，断言请求里放入的口令、令牌和密钥字符串不出现；响应正文不含 `Traceback`。前端替换函数没有测试运行器，不在后端复写一套当作已覆盖。
- 非目标：不重写全部错误码。不把内部异常类名或回溯返回给浏览器。不改审计详情的存储格式。不给其它页面统一换错误文案。
- 验收：三处页面路径的失败文案不含回溯；对应日志样本不含密钥和令牌；接口响应不含 `Traceback`。`pytest tests/ -k "security or log" -v` 通过；`cd frontend; npm run typecheck` 通过。
- 验证：命令退出码写入实现说明。登录错密码、上传失败、对话失败各看一次页面和日志。连接中断仍显示「连接中断」。

## SEC-003 Milvus 五条门槛核对

已核对，记录见 `docs/plans/record_milvus_five_gates.md`，说明见 `docs/plans/implementation_sec_003.md`。第 1 到第 4 条未执行，总结论未通过，默认仍是 `local`。

- 目标：按 ADR-0002 第 5 节留下五条可重复记录。任一条没有通过证据，结论就是未通过，默认向量库保持 `local`。
- 风险：L0。不改默认配置，不改已冻结基线 `evals/reports/rag-v0.1-baseline-20260919.json`。
- 做法：在 `docs/plans/` 写核对记录。每条都写下准备执行的命令、环境，以及结果。建议命令是在能连上 Milvus 且已安装 `pymilvus` 的环境里，设置 `RAG_VECTOR_STORE=milvus` 后执行摄取、重解析、删除、跨租户检索。当前 Compose 不把 19530 映射到宿主机，应用镜像也未保证装有 `pymilvus`。本任务不补这些条件。条件不满足时，命令仍写在记录里，结果写「未执行」并说明原因。skip 不算通过。
- 五条内容：
  1. 摄取后可以用查询命中刚写入的向量。
  2. 重解析后，旧向量的标识不再被检索命中。
  3. 删除文档后，向量被清理，删除计数与删除前的分块数一致。
  4. 租户过滤与本地向量库的读语义一致：其它租户的文档不会命中。
  5. 记录里有失败时的报告，以及把 `RAG_VECTOR_STORE` 改回 `local` 的回滚步骤。默认值本来就是 `local`，回滚说明写这一句即可。
- 非目标：不把 skip 写成通过。不改写已冻结检索基线。不把默认向量库改成 `milvus`。不修改 Milvus 适配代码来换通过。不把 Milvus 标成 `Implemented`。
- 验收：五条都有准备执行的命令，以及通过、失败或「未执行」的结果。未通过时 `app/core/config.py` 与 `.env.example` 里的 `RAG_VECTOR_STORE` 仍是 `local`。用 `tests/test_vectorstore_policy.py` 证明默认仍是 local。
- 验证：核对记录的路径和每条结论写入实现说明。

## SEC-004 16/20 交付包清点

清点表见 `docs/plans/record_delivery_16.md`，实现说明见 `docs/plans/implementation_sec_004.md`。完成 10 项，未完成 6 项（第 1、2、3、4、13、15 项）。80% 未达到。下面是实现前的计划，保留不动。

- 目标：对照 `docs/plans/plan_delivery_2027-03-25.md` 第 6 节，为第 1–16 项各写完成或未完成，并附证据。
- 风险：L0。只改文档、`tasks.yaml` 和 `AGENTS.md`。
- 做法：在 `docs/plans/` 写清点表。每一项列出对应任务、`tasks.yaml` 状态、实现说明或测试命令。没有命令输出或实现说明的项写成未完成。第 13 项拆开写上传限制、日志脱敏、Milvus 五条；五条未全过时，向量库子项未完成，并且不把整个 80% 写成已经达到。第 17–20 项不进入这张 80% 表。
- 证据位置（实现清点时逐项打开，不在本计划里预先标完成）：

| # | 交付包 | 现有任务 |
|---|---|---|
| 1–3 | A1–A3 | RAG-011～RAG-013 |
| 4 | B1 可安装 | INST-001～INST-003 |
| 5 | B2 租户与用户 | TEN-001～TEN-003 |
| 6 | B3 问答与来源 | QA-001～QA-003 |
| 7 | C1 认证与向导 | AUTH-001～AUTH-003 |
| 8 | C2 邀请与切换 | INV-001～INV-003 |
| 9 | C3 管理后台四页 | ADM-001～ADM-004 |
| 10 | C4 对话与会话 | CHAT-001～CHAT-004 |
| 11 | C5 模型路由 | ROUTE-001～ROUTE-002 |
| 12 | C5 沙箱 | SAND-001～SAND-002 |
| 13 | C6 安全与 Milvus | SEC-001～SEC-003 |
| 14 | B3 知识库与软删除 | QA-004 |
| 15 | C1 首次运行旅程 | AUTH-004 |
| 16 | C4 会话隔离 | CHAT-005 |

- 非目标：不实现新功能。不把 D1–D4 算进 80%。不因 Milvus 未通过而把第 13 项从分母里拿掉。
- 验收：清点覆盖第 6 节 1–16 项，每项有完成或未完成的证据。
- 验证：清点文档路径，以及其中仍缺证据的项，写入实现说明。

## 本批非目标

不实现资源级 ACL、病毒扫描、魔术数字校验、统一错误码表、配额和限流倒计时。不把默认向量库改成 Milvus。不改写已冻结的 rag-v0.1 基线。
