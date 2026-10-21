# 单实例生产编排

此编排提供一个应用进程、PostgreSQL、内部 Milvus（etcd 与 MinIO）和持久卷。默认向量库为 `milvus`，仍为 `Partial`，不因配置存在成为生产验收通过。2026-10-17 的隔离启动证据只覆盖当时的 app/db，不能证明当前默认 Milvus 已经启动。数据持久化复读、真实 Milvus 启动和备份恢复仍未演练。

## 部署前配置

开发与生产编排均要求显式提供非空的 `POSTGRES_PASSWORD`、`JWT_SECRET_KEY` 和 `CORS_ORIGINS`，不再回退固定数据库口令或通配来源。
2026-10-18 已按 NFR-012 完成两份编排的默认值验收，14 项合成配置检查及同业务提交 CI 通过，见 [关闭记录](../reviews/2026-10-18-nfr-012-close.md)。这不代表已轮换现有数据库凭据或完成生产部署。
`.env.example` 的来源仅供本地开发，生产应替换为实际 HTTPS 来源。
执行 `python deploy/check_compose_defaults.py` 可只读核对两份编排的必填声明；它不检查密钥强度、不展开配置，也不能替代实际启动验收。
显式填写 `*` 仍会被 Compose 当作普通输入，本检查器只保证没有通配默认值；部署时必须按下表填写真实来源。
开发编排直接将数据库口令拼入连接串，只应用 URL 安全字符组成的随机口令；含特殊字符时使用生产编排的独立、已编码 `DATABASE_URL`。

在仓库根目录执行下述命令。使用仓库外、权限受限的新配置文件，命令始终显式 `--env-file`，不要依赖工作区 `.env`。本次开发不创建真实部署凭据、不构建或启动生产服务。

`ai-assistant init --env-file /secure/assistant.prod.env` 可独占创建含随机 JWT 密钥的新文件；已有文件会拒绝覆盖。该参数只选择生成路径，不改变 Settings 固定加载位置。容器不加载该文件为 env_file，而由 Compose 将所需变量显式注入；请在受控编辑器中补充以下配置，不向聊天、终端日志或 Git 输出口令。

| 变量 | 要求 |
|---|---|
| JWT_SECRET_KEY | init 生成的随机值；禁止默认、空白值 |
| POSTGRES_USER / POSTGRES_DB | 默认均为 aiassistant；数据库不向宿主开放端口 |
| POSTGRES_PASSWORD | 独立随机明文口令，仅作为数据库环境变量注入 |
| DATABASE_URL | 完整 PostgreSQL 连接串，主机必须为 `db`、端口 5432；用户、库名及口令与上项一致；口令中的 `@ / : # %` 等字符必须 URL 编码，禁止直接拼接明文口令 |
| LLM_API_KEY / LLM_PROVIDER / LLM_BASE_URL / LLM_DEFAULT_MODEL | 有效模型配置；兼容 OpenAI 的服务使用 openai provider |
| EMBEDDING_PROVIDER / EMBEDDING_API_KEY / EMBEDDING_BASE_URL / EMBEDDING_MODEL / EMBEDDING_DIM | 真实 Embedding 配置；维度与模型一致，禁止用 Mock 证明生产检索可用 |
| CORS_ORIGINS | 逗号分隔的实际 HTTPS 前端来源，禁止 `*` |
| INITIAL_ADMIN_USERNAME / INITIAL_ADMIN_PASSWORD | 可选环境引导；均为空时保留一次性 setup 路径 |
| INITIAL_SYSTEM_PASSWORD / INITIAL_CULTURE_PASSWORD | 可选三账号引导；两项与管理员口令全部非空且管理员名为 admin；新建账号首次登录必须改密 |
| APP_BIND_HOST / APP_PORT | 默认 127.0.0.1:8000；公开部署应经受控 HTTPS 反向代理，本卡不提供代理专项验收 |

使用独立 project 名隔离本次验证卷；不要复用真实部署的 project 名、卷或数据库。显式提供环境文件的命令如下：

```sh
docker compose --env-file /secure/assistant.prod.env -p assistant-stage -f docker-compose.prod.yml config --quiet
docker compose --env-file /secure/assistant.prod.env -p assistant-stage -f docker-compose.prod.yml up -d --build --wait --wait-timeout 600
docker compose --env-file /secure/assistant.prod.env -p assistant-stage -f docker-compose.prod.yml ps
```

`config --quiet` 仅证明参数与 YAML 可展开，不证明服务启动，也不会输出展开后的密钥。不要发布普通 `config` 输出。`up --wait` 需要 Docker Engine 与镜像下载许可；启动完成后从实际部署客户端验证登录、首次改密和业务操作。隔离项目的启动、健康和依赖未就绪结果以验收记录为准，不能用配置展开代替。

## 默认 Milvus

默认生产编排启动并等待内部 Milvus、etcd 和 MinIO。应用镜像默认安装现有 Milvus 客户端。显式把 `RAG_VECTOR_STORE` 改回 `local` 只影响新的本地向量读写，不会把 Milvus 里的向量搬回来，也不会在 Milvus 故障时自动改走 local。切换前仍要核对索引身份、真实模型与维度。

额外配置 `MINIO_ROOT_USER`、`MINIO_ROOT_PASSWORD`，均不得使用镜像默认凭据。Milvus 对象存储与 MinIO 使用同一组配置。覆盖文件沿用 Milvus 2.5.11、外部 etcd 和 MinIO，服务不发布宿主端口；Milvus Lite 只适用于开发。可选 `MILVUS_TOKEN` 按部署认证状态注入；当前覆盖未配置 Milvus 服务端认证，不得将内部端口额外开放到公网。

```sh
docker compose --env-file /secure/assistant.prod.env -p assistant-milvus-stage -f docker-compose.prod.yml -f deploy/compose.milvus.prod.yml config --quiet
docker compose --env-file /secure/assistant.prod.env -p assistant-milvus-stage -f docker-compose.prod.yml -f deploy/compose.milvus.prod.yml up -d --build --wait --wait-timeout 600
docker compose --env-file /secure/assistant.prod.env -p assistant-milvus-stage -f docker-compose.prod.yml -f deploy/compose.milvus.prod.yml ps
```

默认镜像 `INSTALL_MILVUS` 为 true，只安装现有 `milvus` extra，不升级客户端版本。app 等待数据库和 Milvus 健康，Milvus 等待 etcd/MinIO 健康。资源上限为初始限制，未证明满足容量或延迟指标。覆盖文件仍可与默认文件一起使用，它不会把向量库改回 local。

## 健康、故障和回滚

app 继承 Dockerfile 的健康探针，统一维护健康端点。该探针同时校验 `/api/v1/health` HTTP 200 与响应 `status=ok`，不能把 degraded 的 200 当就绪。数据库用 pg_isready；Milvus/MinIO/etcd 使用各自健康端点。数据库就绪仅代表连接能力，不代替应用迁移、PostgreSQL 演练或业务验收。

服务 unhealthy 时先检查 `ps` 和受限访问的日志：数据库凭据/连接串、迁移失败、模型配置、服务内存限制和向量服务依赖。日志也可能含业务数据，不粘贴原始日志和环境输出到公开渠道。依赖健康条件只控制初始启动顺序；运行中依赖故障不会自动触发所有下游重启，应用失败由重启策略处理，恢复仍需业务验证。

停止验证可用相同参数的 `stop`；禁止为回滚使用 `down -v`、删除命名卷或重新初始化数据库。`pgdata` 保留 PostgreSQL，`appdata` 保留源文件和 local 向量数据；可选卷保留 Milvus、etcd、MinIO 数据。应用以单 worker 运行，不声称多实例状态已经外部化。

代码回退使用经过验证的旧镜像与相同受控配置，先核对 schema/索引兼容并按备份恢复手册处理。镜像或 Compose 回退不自动回滚数据库迁移与索引；真实数据恢复、重要数据覆盖需要独立授权。

静态契约检查：`python scripts/check_production_compose.py`。它不读取 `.env`、不展开密钥、不访问 Docker；通过结果不能替代上述容器与业务验收。

## 两种 Milvus 模式的启停和回滚

默认配置使用编排里的 Standalone。开发进程只在启动时选择一次：本机 `127.0.0.1:19530` 就绪就用 Standalone；只有连接拒绝或超时才把模式记为 Lite。生产环境和远程地址失败时直接不可用，不改用 Lite，也不改回本地向量库。

启动后查看健康检查里的 `runtime` 和 `fallback_reason`。停止用原来的 Compose `stop`，不要 `down -v`。已经记成 Lite 的登记行在 Docker 恢复后仍保持 Lite；要回到 Standalone 必须另行明确切换，不能靠重启自动换库。回滚代码时保留 Lite 文件、索引登记和命名卷，不删除、不合并两个实例。

验收修复后，选择失败时普通API继续启动，向量能力固定为不可用，健康检查503且`runtime=unavailable`；核对配置后重启恢复，不逐请求重试换库。已登记Lite也必须通过开关、开发环境、本机地址、运行库和文件路径校验，关闭开关不会切回Standalone。索引归属保存模式与实例位置摘要；换Standalone端点或Lite文件会被拒绝。历史缺完整实例归属的索引不自动认领，须先受控核验。回滚到旧代码前必须确认其能识别实例绑定，禁止用清表解决错误。

2026-10-21 在本机探测 `127.0.0.1:19530` 超时，且没有 Lite 运行库。上面的步骤是操作说明，不是已完成的真实启停演练。

## 备份与恢复边界

命名卷不是备份。默认开发 SQLite 的备份/恢复步骤仍见 README；生产编排使用 PostgreSQL，不能将复制 SQLite 文件当作本编排的恢复方案。部署者需在受控位置保存数据库逻辑备份、源文件卷，以及启用 Milvus 时对应的元数据/对象存储备份，并保留版本与校验记录。

数据库逻辑备份使用容器内已有 `pg_dump`，将输出重定向到受限访问的备份文件；不要把数据内容或凭据打印到公共日志。恢复时先核对目标为独立空库，使用同版本 PostgreSQL 的 `psql`/`pg_restore` 按备份格式导入，再核验迁移修订、用户/租户/对话、源文件及适用索引身份，最后执行健康与业务检查。覆盖已有重要数据或恢复到生产前必须另获明确授权，不能自动执行清库或删卷。

本批未运行 PostgreSQL 或 Milvus 的真实备份/恢复；上述步骤是运行说明，不能作为已演练证据。自动备份脚本和生产数据库/恢复指标演练仍由相应专项承担，NFR-003 不因此关闭。
