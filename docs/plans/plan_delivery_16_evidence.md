# 前 16 项里未完成 6 项的处理计划

> 状态：计划已按 2026-09-28 评审修订，四条均未实现。版本不匹配采用不改镜像、失败时记为 blocked。
> 来源：`docs/plans/record_delivery_16.md`；`docs/plans/plan_delivery_2027-03-25.md` 第 6 节。
> 日期：2026-09-28
> 任务：`tasks.yaml` 的 `EVD-001`～`EVD-004`。
> 顺序：`EVD-001` → `EVD-002` → `EVD-003` → `EVD-004`。一次只做一条。实现时沿用本文件对应小节，不必再写第二份计划。

这 6 项在 `tasks.yaml` 里已经是 `done`。缺的是写下来的命令结果，或 Milvus 五条的执行结果。不把这些任务改回未完成，也不重做功能。

清点行改成「完成」只发生在本文件写明的验收达到之后。命令失败、环境起不来或旅程没走完时，该行保持未完成，输出原样记入说明。不改测试、不放宽验收、不把第 13 项移出 16 项分母。六项没有全部变成完成之前，不写 80% 已经达到。

`FLOW-001` 直接依赖 `EVD-004`。`EVD-004` 再依赖 `EVD-003`、`EVD-002`、`EVD-001`，所以这四条都会先做完。D1 排在这六项的证据补齐之后。

## 共同约束

- 不改应用代码、前端、解析、切分、Embedding、检索融合、默认 `RAG_VECTOR_STORE`、`.env.example`、已提交的 `docker-compose.yml`、`requirements.txt`、`pyproject.toml` 主依赖，以及 `evals/reports/rag-v0.1-baseline-20260919.json`。
- 不使用生产库、生产密钥，不把密钥、口令或令牌写入计划、说明、日志或提交。
- 测试库使用现有测试配置或单独的 SQLite 文件。`data/` 已被忽略。
- 一条里的命令失败时停止。失败记录写进该条的实现说明，任务不标 `done`，清点行不改成完成。修复另开任务，不在本条里改业务代码。
- 每条完成后写 `docs/plans/implementation_evd_00N.md`，并只改 `docs/plans/record_delivery_16.md` 中对应行。
- 各条允许改动里的 `tasks.yaml` 只改该条状态和阻塞原因。`AGENTS.md` 只在该条结束时更新下一项和条数。不改其他任务的范围。

## EVD-001 补记第 1–3 项的命令结果

- 问题：RAG-011、RAG-012、RAG-013 的计划第 8 节列出了命令，没有通过条数或退出码。
- 现状：三项状态都是 `done`。第 8 节同时写明不做真实模型、Milvus 闭环、生产库迁移和知识库页点击。
- 方案：在仓库根目录按下面三组命令原样执行。RAG-012 与 RAG-013 的命令文本相同，跑一次，两份计划都贴上同一份输出，并写明命令文本一致。RAG-011 单独跑。把输出补进各计划第 8 节之后的「验证结果」，不删原来的命令。
- 交付：三份计划里各有命令和输出；清点表第 1–3 行在全部命令退出码为 0 时改为完成。
- 非目标：不补浏览器走查。不跑 Milvus。不改知识库权限、软删除或版本状态的实现。
- 验收：下面每条命令都有输出和退出码。退出码都是 0 时，第 1–3 行改为完成。任一非 0 则三行都保持未完成。
- 风险：L0。只读验证加文档。
- 允许改动：`docs/plans/plan_rag_011_control_plane.md`、`docs/plans/plan_rag_012_soft_delete.md`、`docs/plans/plan_rag_013_version_states.md`、`docs/plans/record_delivery_16.md`、`docs/plans/implementation_evd_001.md`、`tasks.yaml`、`AGENTS.md`。

RAG-011：

```powershell
pytest tests/test_rag_access.py tests/test_rag_permission_semantics.py tests/test_rag_import_jobs.py -v
ruff check app/rag/access.py app/rag/import_jobs.py app/rag/service.py app/api/routes/rag.py
mypy app/rag/access.py app/rag/import_jobs.py app/rag/service.py
cd frontend; npm run typecheck
```

RAG-012 与 RAG-013（同一组，执行一次）：

```powershell
pytest tests/test_rag_access.py tests/test_rag_permission_semantics.py tests/test_rag.py tests/test_rag_import_jobs.py -v
ruff check app/rag/ app/models/rag.py app/api/routes/rag.py
mypy app/rag/access.py app/rag/service.py app/models/rag.py
cd frontend; npm run typecheck
```

`cd frontend; npm run typecheck` 若与上一组是同一次退出码 0 的运行，可以引用同一次输出，并写明没有重跑。

## EVD-002 补记第 4 项的命令结果

- 问题：INST-001～INST-003 写了健康检查、`docker compose config` 和前端构建应如何判定，没有记录输出。
- 现状：三项状态都是 `done`。INST-002 的计划规定命令是 `docker compose config`，检查编排定义，不要求拉镜像或启动容器。
- 方案：执行下面三组。成功的 `docker compose config` 使用进程环境变量传入非空 `JWT_SECRET_KEY` 和 `LLM_API_KEY`。JWT 不得使用仓库内占位口令 `change-me-in-production-use-a-random-secret`。这两项只留在当次进程里，不写入仓库、不写入说明。说明里只记退出码，以及展开结果是否含有 `db`、`milvus`、`app`，`RAG_VECTOR_STORE` 是否为 `local`，`MILVUS_URI` 是否为 `http://milvus:19530`，`SERVE_FRONTEND` 是否为真。缺少任一项时的失败输出可以原文收录，因为其中不应含密钥。
- 交付：`docs/plans/plan_b1_install.md` 补上健康检查输出；`docs/plans/plan_inst_002_compose.md` 补上两次 `docker compose config` 的退出码和上述字段；`docs/plans/plan_inst_003_console.md` 补上类型检查、构建和 `SERVE_FRONTEND` 字段。全部达到验收时，清点表第 4 行改为完成。
- 非目标：不执行 `docker compose up`。不把配置成功写成五条门槛已过。不改编排文件来让命令通过。
- 验收：
  - `pytest tests/ -k health -v` 与 `ruff check app/api/routes/health.py` 退出码为 0。
  - 未设置 `JWT_SECRET_KEY` 或未设置 `LLM_API_KEY` 时，`docker compose config` 失败，且输出不含仓库内 JWT 占位口令。
  - 两项都设置为非空且 JWT 不是占位口令时，`docker compose config` 退出码为 0，服务含 `db`、`milvus`、`app`，应用的 `RAG_VECTOR_STORE` 为 `local`，`MILVUS_URI` 为 `http://milvus:19530`，`SERVE_FRONTEND` 为真。
  - `cd frontend; npm run typecheck` 与 `npm run build` 退出码为 0。
- 风险：L0。`docker compose config` 不启动容器。本机没有 Docker 时，记下命令的失败输出，第 4 行保持未完成，任务不标 `done`。
- 允许改动：`docs/plans/plan_b1_install.md`、`docs/plans/plan_inst_002_compose.md`、`docs/plans/plan_inst_003_console.md`、`docs/plans/record_delivery_16.md`、`docs/plans/implementation_evd_002.md`、`tasks.yaml`、`AGENTS.md`。

```powershell
pytest tests/ -k health -v
ruff check app/api/routes/health.py
```

缺少密钥的两次配置检查各跑一次：一次去掉 `JWT_SECRET_KEY`，一次去掉 `LLM_API_KEY`。成功的那次两项都设置。记录时不要打印这两个变量的值。

```powershell
cd frontend
npm run typecheck
npm run build
```

## EVD-003 按 README 走完第 15 项

- 问题：AUTH-004 的状态是 `done`，实现说明没有记录按 README 走到发出一条消息。
- 现状：README「快速开始」是五步：填写环境、启动、建立管理员、注册或登录、发送第一条消息。当前开发库已有系统管理员，打开 `/setup` 会离开向导。这条路径不能代替空库上的第 3 步。
- 方案：使用单独的 SQLite 文件 `data/readme_walk.db`，不要用现有开发库或 `data/test_ai_assistant.db`。启动后端前，只在该进程里设置 `DATABASE_URL=sqlite:///./data/readme_walk.db`。进程环境变量会盖过 `.env`。不改 `.env`。未设置这个变量就按 README 原样启动时，会连上默认的 `data/ai_assistant.db`，这次旅程立即停止。该进程不设置 `INITIAL_ADMIN_USERNAME` 和 `INITIAL_ADMIN_PASSWORD`。按 README 五步操作并逐步记录：健康检查的 `status`、`/setup` 创建管理员后进入对话、向导随后关闭、`/register` 或 `/login` 的结果、`/chat` 发出一条消息后能看到助手回复。开发环境没有模型密钥时，回复来自模拟模型，记录里写明这只说明对话链路通了。口令不写入说明，只写「已提交」。
- 交付：`docs/plans/plan_auth_c1.md` 增加这次旅程的逐步结果。五步都有结果且消息已发出时，清点表第 15 行改为完成。
- 非目标：不改 README。不实现 `ai-assistant init`。不把 Docker 安装路径当成这次必做步骤。发现文档与页面不一致时停止并保持第 15 行未完成，不在本条里改文案或页面。
- 验收：说明里能按 README 五步指出每一步的结果，并且有一条已发出的消息。空库向导必须实际走过。只核对已有管理员时 `/setup` 跳走，不算完成。
- 风险：L0。只写单独的 SQLite。页面若要输入口令，由操作者在本机完成；说明不记录口令。
- 允许改动：`docs/plans/plan_auth_c1.md`、`docs/plans/record_delivery_16.md`、`docs/plans/implementation_evd_003.md`、`tasks.yaml`、`AGENTS.md`。

## EVD-004 重跑第 13 项的 Milvus 五条

- 问题：上传限制和日志脱敏已有命令结果。向量库子项未完成，因为第 1–4 条未执行。因此第 13 项整项未完成。
- 现状：默认 `RAG_VECTOR_STORE` 是 `local`。可选依赖是 `pymilvus==2.4.7`，不在主依赖和镜像里。已提交的编排不把 `19530` 发布到宿主机。摄取不会调用 `MilvusVectorStore.add`。接口删除是软删除，不调用 `delete_by_document`。准备执行的步骤在 `docs/plans/record_milvus_five_gates.md`。ADR-0002 第 5 节的第 5 条包含备份；上次只写了失败报告和回滚，没有做备份，不能把备份记成通过。
- 方案：
  1. 在当前解释器安装 `pymilvus==2.4.7`。不修改 `requirements.txt` 和 `pyproject.toml`。
  2. 新建 `scripts/milvus_five_gates.py`。应用不导入它。脚本按 `docs/plans/record_milvus_five_gates.md` 的准备步骤把五条收成可重复命令，断言失败即停止，并留下回滚说明。
  3. 启动 Milvus 时不改已提交的 `docker-compose.yml`。增加被忽略的 `docker-compose.gate.local.yml`，只给 `milvus` 服务加上 `127.0.0.1:19530:19530`。覆盖文件不改 `image`，服务端仍是已提交的 `milvusdb/milvus:v2.5.11`。用独立项目名启动，避免碰到现有卷：

```powershell
docker compose -p aigates -f docker-compose.yml -f docker-compose.gate.local.yml up -d milvus
```

编排文件里的应用服务在解析时就要求 `JWT_SECRET_KEY` 和 `LLM_API_KEY`。这两项用当次进程环境变量提供，不写入仓库和说明。只启动 `milvus`，不启动 `app` 和 `db`。
  4. 等待该服务健康检查通过后，在宿主机用单独 SQLite `data/milvus_gate_check.db` 执行 `scripts/milvus_five_gates.py`。进程内 `RAG_VECTOR_STORE=milvus`，`MILVUS_URI=http://127.0.0.1:19530`。摄取后对分块显式调用 `MilvusVectorStore.add`。重解析后对新分块再调用 `add`，并确认旧主键不再命中。删除使用 `delete_by_document`，返回条数等于删除前的分块数，随后按 `document_id` 查询集合必须为空。另一个 `tenant_id` 的检索不得命中第一个租户的文档。嵌入若是 Mock，结果只记为链路，五条不算通过。客户端 `pymilvus==2.4.7` 与服务端 `v2.5.11` 次版本不一致。连接、握手或调用因此失败时，记为 `blocked`，原因写版本不匹配。这不记成五条业务断言失败，也不改镜像、不改 `pyproject.toml`。
  5. 第 5 条除失败报告和把进程变量设回 `local` 之外，先停止 `aigates` 的 milvus 容器，再把该卷拷贝到 `data/gate-backup/`，然后删除容器和卷。不在容器运行中拷贝。记录备份和恢复时要停的是这个核对项目。这不是生产备份演练。
  6. 删除命令是 `docker compose -p aigates -f docker-compose.yml -f docker-compose.gate.local.yml down -v`。不删除默认项目的卷，不改 `app/core/config.py` 与 `.env.example`。拷贝必须发生在这条删除命令之前。
- 交付：新的核对记录覆盖五条的通过、失败或未执行。五条全部通过时，清点表第 13 行改为完成，并写明上传限制与日志脱敏仍引用原来的实现说明。默认向量库仍是 `local`。把默认改成 `milvus` 不在本条里做。
- 非目标：不把 `pymilvus` 放进主依赖或镜像。不把 `19530` 写进已提交的编排。不在覆盖文件里把镜像改成 2.4。不修改 `MilvusVectorStore` 或摄取代码来换通过。不把 skip 或 Mock 写成通过。不把 Milvus 标成 `Implemented`。不改冻结检索基线。
- 验收：五条都有本次输出。全部通过且停止容器后的备份拷贝存在时，第 13 行改为完成，`tests/test_vectorstore_policy.py` 仍证明默认是 `local`。任一未执行或失败时，第 13 行保持未完成，任务不标 `done`。解释器没有 `pymilvus`、没有 Docker、`19530` 连接不上，或失败原因是 `pymilvus==2.4.7` 与 `milvusdb/milvus:v2.5.11` 不匹配时，本条标为 `blocked`，并写明是哪一种。
- 风险：L1。客户端 `pymilvus==2.4.7` 与已提交镜像 `milvusdb/milvus:v2.5.11` 次版本不一致，可能在握手或调用时失败。这种失败按上一行记为 `blocked`，不改镜像。只写核对用的 SQLite 和独立 Compose 项目的卷。回滚是把进程里的 `RAG_VECTOR_STORE` 设回 `local`，并在备份拷贝完成之后删掉 `aigates` 项目。
- 允许改动：`scripts/milvus_five_gates.py`（只被这次核对调用，应用不导入）、`.gitignore`（忽略 `docker-compose.gate.local.yml`）、`docs/plans/record_milvus_five_gates.md` 或同目录的新核对记录、`docs/plans/record_delivery_16.md`、`docs/plans/implementation_evd_004.md`、`tasks.yaml`、`AGENTS.md`。
- 禁止改动：`app/`、`docker-compose.yml`、`.env.example`、`requirements.txt`、`pyproject.toml`、冻结基线、`.env`。

五条的判定与 `docs/plans/record_milvus_five_gates.md` 的「准备执行的命令」一致。新建的 `scripts/milvus_five_gates.py` 把每条收成可重复命令，断言失败即停止并留下回滚说明。

## 本批非目标

不实现 D1–D4。不补第 10、11、12 项里已经写明没做的浏览器或 Linux 实机项；那些行已经有命令结果，保持完成。不因为本计划存在就把清点表提前改成完成。
