# REL-001、REL-002、REL-003、REL-004：Swagger、备份记录、测试门禁、一轮检索实验

> 状态：REL-001 已实现，说明见 `docs/plans/implementation_rel_001.md`。REL-002、REL-003、REL-004 尚未实现。`docs/reviews/2026-10-02-plan_d4评审.md` 的 6 条已写入本文。实现说明在各自完成后另写，不把未做项写成已完成。
> 来源：`tasks.yaml` 的 REL-001～REL-004；`docs/plans/plan_remaining_delivery.md` 对应四节；交付排期第 5 节 D4、第 6 节第 20 项。
> 日期：2026-10-02
> 截止：2027-03-25
> 依赖：REL-001、REL-002 只依赖已完成的 CLI-001。REL-003 依赖 REL-001。REL-004 依赖 REL-003。

一次只实现一条。顺序是 REL-001、REL-002、REL-003、REL-004。做完这四条只完成交付包第 20 项，不是企业上线通过，也不是需求 §6 的 CI、PostgreSQL、备份脚本和性能数字已经满足。

38 张企业卡本计划不重拆、不实现。和 D4 重叠的边界锁在下面，避免同一模块为迁就那些卡再改一遍。

## 目标

1. 每个已注册的 `/api` 业务路由及其 HTTP 方法都出现在本进程生成的 OpenAPI 里，并且该操作有摘要或说明。缺描述的补在路由上。README 端点表里已有的方法加路径必须是这份 OpenAPI 的子集。`POST /api/chat/stream` 的响应媒体类型标明 `text/event-stream`。不把 README 扩成第二份接口手册。
2. README 与演练使用同一种方法：标准库 `sqlite3.backup`。临时库恢复后能启动应用，`GET /api/health` 成功，且 `alembic_version` 与源库相同。不备份开发库 `data/ai_assistant.db`，不备份生产库。
3. 用项目指定的 conda 环境实际跑完后端测试、ruff、mypy、前端类型检查和前端构建，把命令、退出码、失败项、git 提交、日期和语言版本写入说明。失败保持失败。记录完成不等于发布合格。
4. 在已冻结的检索基线上只改「已经按 RRF 截断的那一批候选的最终排序」这一个变量，用真实嵌入跑一轮。三条采纳条件同时成立时，只把默认关闭的代码留下，并写明这不是生产检索质量结论。否则生产检索保持现状。

## 已核对的现状

核对日期：2026-10-02。

OpenAPI：

- `app/main.py` 创建 FastAPI 应用，`app.include_router(api_router, prefix="/api")`。`/docs` 使用框架生成的文档。
- 业务路由都在 `app/api/routes/`，由 `app/api/router.py` 挂上。前缀包括 health、auth、chat、export、rag、mcp、skills、workflow、admin audit、admin tenants、admin users、admin system、invitations。
- `GET /` 在未托管前端时进入 schema；托管前端时 `include_in_schema=False`。`/assets` 是静态挂载。`GET /{full_path:path}` 是前端回退，`include_in_schema=False`。这三条不是业务 API。
- 没有把路由表和 `app.openapi()` 对过账的测试。README 有「API 端点」一节，它不是覆盖证明。

备份：

- 默认库是 `sqlite:///./data/ai_assistant.db`（`app/core/config.py`）。本地向量写在同一库的分块表里。源文件在 `data/knowledge`（`app/rag/document_storage.py`），不在这个 SQLite 文件里。
- 默认 `RAG_VECTOR_STORE=local`。切到 Milvus 后，向量不在该 SQLite 文件里。
- 已有 `ai-assistant migrate`。没有 `scripts/backup.sh`。需求 §6.7 的 `pg_dump`、Milvus collection 备份和上传目录备份属于 `NFR-010`，本批不做。
- `data/gate-backup/milvus` 是 2026-09-29 门槛核对时的卷拷贝，不是本交付包的恢复记录。

测试门禁：

- 任务卡上的命令是 `pytest`、`ruff check .`、`cd frontend; npm run typecheck`、`cd frontend; npm run build`。本计划按发布清单另加 `mypy app/`，只记录退出码，不在本条修到 0。解释器是 conda 环境 `ai-assistant`（`D:\install\anaconda3\envs\ai-assistant\Scripts`）。
- 仓库没有 `.github` 流水线。流水线是 `NFR-001`。
- 2026-09-28 曾记录 8 个失败。那是当时的事实，不是本门禁的结果。本门禁以当次输出为准。修这 8 个失败是 `QA-005`，不在本批。

检索：

- 冻结基线 `evals/reports/rag-v0.1-baseline-20260919.json`：真实 `text-embedding-v3`、1024 维、Local、`native`、`structured` 切分、RRF k=60、深度 10。可排序案例 Recall@1 = 0.795，Recall@5 = 0.971。该文件禁止改写。
- `RAG-007` 已比较过 k=40/60/80，结论是保持 60。本批不再扫 k。
- 查询变换的进入条件是 Recall@5 低于 0.90。当前 0.971，本批不做查询变换。
- 没有独立重排实现，也没有 cross-encoder 依赖。`ChunkResult.score` 只带 RRF 分，不带稠密分和 BM25 分。
- 混合检索的分词函数是 `app.rag.embeddings.mock.tokenize`。函数放在该模块里，正式嵌入路径也调用它。用它做词面重排不等于改用 Mock 嵌入。
- 评测入口是 `scripts/run_rag_baseline.py --mode official`。正式模式遇到 Mock 嵌入或向量库不是 `local` 时直接退出。索引在 `data/eval_rag_v01.db`，不碰开发库和测试库。holdout 不得用于选择规则。

## 与需求文档的取舍

需求文档比这四条宽。本批以 `tasks.yaml` 的交付、非目标和验收为准。

| 需求文档 | 本批 |
|---|---|
| 所有 API 有 Swagger，README 另有端点清单 | 核对进程生成的 OpenAPI 与已注册业务路由。README 已有行必须是 OpenAPI 的子集，不把 README 列全 |
| §6.7：`pg_dump` + Milvus 备份 + 上传目录 + `scripts/backup.sh` + 记录 RTO | 只演练默认 SQLite 的标准库 `backup`，并证明应用能打开恢复文件。脚本、PostgreSQL、Milvus、上传目录和 RTO 留给 `NFR-010`、`OPS-004` |
| §4.5 覆盖率 ≥ 70% 且 CI 全绿 | 记录当次命令的退出码和失败项，并补记 mypy。不测覆盖率，不建流水线。非 0 只结束记录，发布结论写未达到发布合格 |
| 独立 Reranker（cross-encoder / LLM）或查询变换 | 只做下面写死的词面覆盖重排。采纳只保留默认关闭的代码，不标成 `Implemented`。不下载模型，不加依赖。查询变换不进入本批 |
| 统一错误码信封、结构化引用、MCP 契约 | 谁以后改契约，谁在自己的任务里更新 OpenAPI。不在 REL-001 预改这些响应 |

若要改回需求文档的宽口径，先改任务卡再实现。

## 已定的取舍

- 业务路由的定义：`include_in_schema=True` 且路径以 `/api` 开头的 `APIRoute`。每个声明的方法都要出现；框架自动加的 `HEAD` 和 `OPTIONS` 不单算一条。`GET /`、静态资源和前端回退不计入。某条 `/api` 路由若被标成 `include_in_schema=False`，测试失败，本批把它改回可见并补上说明，不删路由。反向用例用一张假路由表断言「缺少一条就失败」，不从正在使用的 `app` 上拆路由。
- 操作算有说明：OpenAPI 里该操作的 `summary` 或 `description` 至少一项非空。FastAPI 会把函数文档字符串收进 `description`。缺的补一句话文档字符串，不改请求体和响应模型。`POST /api/chat/stream` 的 OpenAPI 响应媒体类型包含 `text/event-stream`；若当前没有，只补这一条的媒体类型，不改事件字段。
- README「API 端点」表中每一行的方法加路径必须出现在当次 OpenAPI 里。对不上的行改到与路由一致。不要求该表列出每一个业务路由，也不把它扩成第二份手册。
- 备份演练只用新建的临时 SQLite。不打开、不复制、不覆盖 `data/ai_assistant.db` 和 `data/test_ai_assistant.db`。README 与演练都使用 Python 标准库 `sqlite3.backup`，不把裸文件复制写成步骤。先停写，再备份到第二个文件。然后用恢复文件作为 `DATABASE_URL` 启动应用，`GET /api/health` 成功，并且两边的 `alembic_version` 相同。标记行只证明备份内容还在，不能代替这次启动。回滚是删掉临时目录。WAL 和 SHM 若存在，记录里说明 `backup` 是否已把它们收进目标文件。
- README 新增「备份与恢复」，放在「快速开始」之后。写明：先停掉占用该库的进程；用标准库 `backup` 而不是只拷主文件；备份文件与数据库同等敏感，含口令哈希和对话时不得提交、不得写入日志；源文件在 `data/knowledge`，本次步骤不包含它；向量库保持 `local` 时向量在同一个 SQLite 里，改成 Milvus 后不在。文末指向以后的备份脚本任务，不在这里提供脚本。
- 门禁说明写入 `docs/plans/implementation_rel_003.md`。命令是 `pytest`、`ruff check .`、`mypy app/`、`cd frontend; npm run typecheck`、`cd frontend; npm run build`。每条都有退出码。失败测试列出节点名。记录同时写明 git 提交、日期、Python 版本、Node 版本和工作目录。不改断言、不跳过、不用另一个解释器重跑来换一个通过结果。不把没跑的覆盖率写成 ≥ 70%。
- 非 0 退出码可以结束 REL-003 的「留下记录」。发布结论另写「未达到发布合格」。每条失败写明负责人（个人开发者）、当次输出里的原因、处置任务或豁免，截止日期不晚于把本系统当作企业上线之前。不在本条改测试或业务代码。
- 检索只改一个变量，名字是 `lexical_coverage`。对照和处理共用同一次建库、同一次真实嵌入、同一次 `hybrid_search` 已经截断的结果（深度 10，k=60）。对照顺序是接口返回的 RRF 顺序。处理顺序只重排这已经截断的一批：词面覆盖率 `|查询词 ∩ 分块词| / max(|查询词|, 1)` 降序，覆盖率相同则保持原来的 RRF 次序。分词只用 `app.rag.embeddings.mock.tokenize`。不扩大候选池，不在截断之前重排完整融合列表，不改 k，不改切分，不改嵌入，不改 BM25，不改 Milvus 路径。
- 采纳规则在跑数之前写死，跑完不许改。数字只取 development 与 validation，与 `RAG-007` 相同。holdout 只附在报告里，不参与判断，也不用来改公式。越权案例用现行评测器，不用 2026-09-19 基线里后来已经改过定义的违规率。三条同时成立才采纳：Recall@1 高于对照，MRR 高于对照，越权案例数不高于对照。否则不采纳。只准备这一个公式，不做权重扫描。
- 采纳的含义只是把同一函数留在 `LocalVectorStore.hybrid_search` 里，并且默认关闭。位置在 RRF 截断之后、返回之前，只重排即将返回的那一批。环境变量 `RAG_LEXICAL_RERANK=false`。`.env.example` 写明默认关闭，打开后才与实验报告一致。`MilvusVectorStore` 不改。结论必须写明：语料 13 篇、37 个分块；该信号与 BM25 同源；本轮不能作为生产检索质量提升；独立 Reranker 仍是 `Planned`，不标成 `Implemented`。`PRAG-002` 只在采纳后做开关、灰度和开销，也不把重排写成已经上线。
- 不采纳时：不改 `app/rag/`、不改配置。评测脚本仍保留显式参数，默认不重排，以便按同一命令复现。生产检索顺序与现在相同。`PRAG-002` 直接关闭，不再落地开关。
- 正式报告写入新文件，文件名带日期，不覆盖 `rag-v0.1-baseline-20260919.json`。报告写明数据集版本、代码提交、嵌入 provider、模型、维度、向量库、机器规格，以及对照与处理的 Recall@1、MRR、越权案例数。写明 Mock 或 smoke 不能当作采纳证据。密钥和连接串不进入报告、README 和门禁说明。没有真实嵌入密钥时，REL-004 停在 `blocked`，不改用 Mock 或 smoke 出结论。
- 演练命令、评测命令和门禁命令都写明工作目录。实验超过一周仍没有正式报告时停止，不换第二个变量。

## 需要先改的允许路径

下表写入对应任务的 `allowed_paths`。未列入的文件不改。

| 任务 | 增加的路径 | 原因 |
|---|---|---|
| REL-001 | `docs/plans/plan_d4.md`、`docs/plans/implementation_rel_001.md`、`README.md` | 计划与实现说明；端点表里对不上的行要改到与 OpenAPI 一致 |
| REL-004 | `docs/plans/plan_d4.md`、`docs/plans/implementation_rel_004.md`、`scripts/run_rag_baseline.py`、`app/core/config.py`、`.env.example` | 对照参数在现有评测脚本上；只有采纳时才改配置。配置文件在不采纳时不得改出差异 |

REL-002、REL-003 的现有允许路径已经盖住 README、`docs/` 和 `docs/plans/`。

## REL-001 Swagger 覆盖现有端点

实现说明：`docs/plans/implementation_rel_001.md`。

- 目标：进程内已注册的每个 `/api` 业务路由方法都能在 OpenAPI 里找到，并且带有摘要或说明。README 端点表没有指向不存在的路径。流式对话在文档里标明 SSE。
- 现状：见上文「OpenAPI」。
- 方案：新增 `tests/test_openapi_coverage.py`。测试从 `app.routes` 收集业务路由，再从 `app.openapi()` 收集路径和方法，两边不一致就失败。说明为空也失败。`POST /api/chat/stream` 的响应媒体类型不含 `text/event-stream` 也失败。README「API 端点」表里的方法加路径若不在 OpenAPI 中，测试失败。反向用例把一条假路由放进比较函数，期望失败；不从正在使用的 `app` 上拆路由。失败后只给对应路由补文档字符串、补这一条媒体类型、去掉错误的 `include_in_schema=False`，或把 README 里对不上的行改到与路由一致。不删路由，不改其他响应模型，不把 README 列全。
- 交付：覆盖测试、为通过该测试所补的路由说明、流式响应的媒体类型，以及 README 中已改正的端点行。
- 非目标：不手写第二份 API 文档。不预改错误码信封、引用结构或 MCP 契约。不把前端回退和静态资源写进文档。
- 验收：`pytest tests/ -k openapi -v` 通过。假路由表能让比较函数失败，恢复该表后通过。现有 `/api` 路由没有被删除。README 表中每一行都能在 OpenAPI 里找到。`POST /api/chat/stream` 的 schema 含 `text/event-stream`。
- 验证：`pytest tests/test_openapi_coverage.py -v`；`ruff check tests/test_openapi_coverage.py app/api/`。

## REL-002 备份步骤与一次恢复记录

实现说明：`docs/plans/implementation_rel_002.md`。这份说明就是恢复记录。

- 目标：读 README 的人按其中的 `backup` 步骤可以恢复默认 SQLite。临时库上的恢复文件能启动应用，健康检查成功，并且 `alembic_version` 与源库相同。
- 现状：见上文「备份」。
- 方案：README 增加「备份与恢复」，步骤与演练相同，都是标准库 `sqlite3.backup`。演练在系统临时目录创建空库，执行 `ai-assistant migrate --yes`，写入标记行，备份到第二个文件。再用该文件作为 `DATABASE_URL` 启动应用，请求 `GET /api/health`。命令、工作目录、退出码、标记值、修订号和健康检查结果写入实现说明。密钥和连接串不写入。演练结束删除临时目录。
- 交付：README 步骤、一次恢复记录。
- 非目标：不备份生产库和现有开发库。不把裸文件复制写成步骤。不写 `scripts/backup.sh`。不做 `pg_dump`、Milvus 备份、上传目录备份，不测量 RTO。不把 `data/gate-backup/` 算作本次记录。
- 验收：实现说明里有工作目录、命令、退出码、标记值、两边的 `alembic_version`、健康检查成功、删除临时目录的回滚。README 能找到默认库路径、停写、标准库 `backup`、备份文件不得提交或写入日志、`data/knowledge` 不在本次备份内、`local` 与 Milvus 的差别。演练前后 `data/ai_assistant.db` 的修改时间不变；若该文件不存在，记录写明未触碰该路径。
- 验证：按实现说明中的命令在临时目录再执行一次，核对标记和 `/api/health`。不使用生产数据和 `.env` 里的密钥。

## REL-003 测试门禁

实现说明：`docs/plans/implementation_rel_003.md`。

- 目标：发布前这一组命令有一次完整记录，读者能看出哪条失败、失败的是哪些测试，以及这次记录是否达到发布合格。
- 现状：见上文「测试门禁」。
- 方案：在 conda 环境 `ai-assistant` 中依次执行 `pytest`、`ruff check .`、`mypy app/`，再在 `frontend/` 下执行类型检查和构建。若 `node_modules` 缺失，先在该目录执行 `npm ci`，并把 `npm ci` 的退出码一并写入。记录写明每条命令的工作目录、git 提交、日期、Python 版本和 Node 版本。不修改测试、不修改业务代码、不新增 CI 配置。
- 交付：门禁记录。任一命令非 0 时，记录中的发布结论是「未达到发布合格」。
- 非目标：不修 `QA-005`。不把覆盖率写成达标。不建 GitHub Actions。不因为失败把发布结论改成合格。
- 验收：记录包含五条命令、各自退出码、失败测试节点或构建错误摘要、git 提交、日期、Python 版本、Node 版本和工作目录。每条失败有负责人（个人开发者）、当次原因、处置任务或豁免，截止日期不晚于把本系统当作企业上线之前。退出码非 0 的命令仍保留在记录里。
- 验证：记录中的命令与本计划列出的命令一致，工作目录和解释器写明。不另换解释器补一次通过。

非 0 退出码可以结束「留下记录」。完成的含义是记录真实结果，不是套件全绿，也不是发布合格。

## REL-004 一轮检索实验结论

实现说明：`docs/plans/implementation_rel_004.md`。指标结论同时写入 `docs/evaluations/` 下的新报告，并另存一份 JSON 到 `evals/reports/`，文件名含日期。

- 目标：用一轮事先写死的实验，决定词面覆盖重排是否以默认关闭的形式留在本地检索路径。该决定不是生产检索质量结论。
- 现状：见上文「检索」。
- 方案：给 `scripts/run_rag_baseline.py` 增加 `--rerank lexical`。省略该参数时顺序与现在相同。正式模式一次建库，先记对照，再记处理，写入同一份新报告。对照和处理的嵌入、切分、k、深度和已经截断的候选集合相同。重排发生在截断之后。决策数字只取 development 与 validation。按上文采纳规则下结论。不采纳则到此为止。采纳则再改 `local.py`、`config.py` 和 `.env.example`，函数放在截断之后、返回之前，默认关闭，并补一个测试：关闭时顺序不变，打开时与实验函数一致。结论写明语料规模、信号与 BM25 同源、独立 Reranker 仍是 `Planned`。
- 交付：新的评测报告、采纳或不采纳的结论。采纳时另有默认关闭的本地重排。
- 非目标：不改冻结基线文件。不扫 k。不做查询变换。不引入 cross-encoder 或新依赖。不改 Milvus。不在截断之前重排完整融合列表。不用 holdout 调参。不用 Mock 或 smoke 作为采纳证据。不把本轮写成生产检索质量提升或重排已上线。不在本条做开关灰度、开销说明和默认开启；采纳之后这些才是 `PRAG-002`，并且 `PRAG-002` 也不把重排标成 `Implemented`。
- 验收：结论写明变量名、工作目录、命令、数据集版本、代码提交、嵌入 provider、模型、维度、向量库、机器规格、对照与处理在 development 与 validation 上的 Recall@1、MRR、越权案例数，以及采纳或不采纳。三条规则与跑数前写在本计划里的句子一致。报告含语料 13 篇、37 个分块，以及不能作为生产检索质量结论。密钥和连接串不在报告中。不采纳时 `app/rag/` 与配置相对本任务开始时没有行为差异。采纳时默认配置下现有检索测试仍按 RRF 顺序通过，打开开关后的顺序与实验函数一致，且重排发生在截断之后。
- 验证：在仓库根目录执行 `python scripts/run_rag_baseline.py --mode official --rerank lexical`，退出码记入说明。采纳分支另跑覆盖该开关的 pytest。密钥缺失时不跑 smoke 充数，任务改为 `blocked` 并写明缺的是嵌入密钥。

## 风险与回滚

- REL-001 补说明或补 SSE 媒体类型时，路径和事件字段不变。README 只改正对不上的行。回滚是回退该提交。
- REL-002 若误用开发库或测试库，会碰到正在使用的数据。演练命令只接收临时目录。开始前核对路径不是 `data/ai_assistant.db` 和 `data/test_ai_assistant.db`。恢复出的文件与库同等敏感，演练结束删除，不提交。
- REL-003 的非 0 退出码是记录的一部分，发布结论写未达到发布合格。不得为了把本条做成发布合格去改测试或降级门禁。
- REL-004 若把重排默认打开，或在截断之前重排，线上排序会偏离实验。默认必须关闭，代码只重排已经截断的那一批。不采纳时不留下生产分支。采纳也不表示生产检索质量已经提升。真实嵌入调用有费用，只跑一轮，不扫描参数。回滚是回退该提交，冻结基线文件始终不改。

## 38 张卡：本计划不重拆

这 38 张仍全部是 `backlog`。下面只记录和 D4 边界有关的判断，避免实现 D4 时把它们提前做掉，也避免以后为了修拆分而返工 D4。

这些边界保持现状，D4 按本计划实现：

- `NFR-001` 是流水线，`REL-003` 是一次记录。两条都留着。
- `NFR-010` 才做数据库、向量库、上传目录三份脚本。`REL-002` 不提前写脚本。
- `PRAG-002` 只在 REL-004 采纳后做开关、灰度和开销，并且不把重排标成 `Implemented`。不采纳则关闭，不再做第二次检索实验。
- `ERR-001`、`PRAG-003`、`PMCP-001` 以后若改契约，在各自任务里更新 OpenAPI，不回改 REL-001 的范围。

这些拆分确实别扭，但放到第 20 项完成之后再改卡，不插进 D4：

- `PWFL-003` 同时依赖 `PWFL-001` 和 `OPS-001`，同组的重试、超时、告警可以先做，并发幂等要等到多实例外部化之后。工作流健壮性被拆成两截。
- `NFR-005`、`NFR-008`、`OPS-001` 都依赖 `REL-004`。PostgreSQL 验证、审计保留和多实例状态并不使用检索实验的指标，却被排到实验结论之后。
- `NFR-006`、`NFR-007`、`OPS-006`、`OPS-007` 写在 E 阶段分组里，依赖却只到 `NFR-001` 或 `NFR-002`。按分组会误以为很晚才能做。
- `QA-005` 的标题钉死了 2026-09-28 的 8 个失败。门禁当次若已不是这 8 个，应先改卡上的事实再修，不能按过时清单改测试。

第 20 项完成后再对照需求清点收窄项。那一次只给「38 张卡没有覆盖、且仍在约定范围内」的缺口开新卡。§4.4 明确不做的能力、P2 的调试模式、对话分支、Prometheus，以及测评大盘，继续只列表、不排期。

## 未纳入本批

- 38 张卡的实现和重拆。
- 需求里的 Alpaca/ShareGPT 导出、CLI 全集、Token 配额、配额页、资源级 ACL、查询变换、cross-encoder。
- 测评大盘。进入条件仍是第 20 项完成。
