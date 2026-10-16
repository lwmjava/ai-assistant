# 单实例生产编排

此编排提供一个应用进程、PostgreSQL 和持久卷。默认向量库为 `local`，只启动 app/db；Milvus 为显式覆盖，仍为 `Partial`，不因配置存在成为生产验收通过。当前宿主没有可用 Docker Engine，实际构建、启动、健康探针、数据持久化和 Linux 运行尚未验证。

## 部署前配置

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

`config --quiet` 仅证明参数与 YAML 可展开，不证明服务启动，也不会输出展开后的密钥。不要发布普通 `config` 输出。`up --wait` 需要 Docker Engine 与镜像下载许可；启动完成后从实际部署客户端验证登录、首次改密和业务操作。当前没有一键启动通过或五分钟上手达标的运行证据。

## 可选 Milvus

默认路径不启动或等待 Milvus，未启用的服务不计入 local 启动计时。启用前核对已有索引身份、真实模型/维度以及数据迁移和回滚授权；切换配置本身不会迁移 local 数据。

额外配置 `MINIO_ROOT_USER`、`MINIO_ROOT_PASSWORD`，均不得使用镜像默认凭据。Milvus 对象存储与 MinIO 使用同一组配置。覆盖文件沿用 Milvus 2.5.11、外部 etcd 和 MinIO，服务不发布宿主端口；Milvus Lite 只适用于开发。可选 `MILVUS_TOKEN` 按部署认证状态注入；当前覆盖未配置 Milvus 服务端认证，不得将内部端口额外开放到公网。

```sh
docker compose --env-file /secure/assistant.prod.env -p assistant-milvus-stage -f docker-compose.prod.yml -f deploy/compose.milvus.prod.yml config --quiet
docker compose --env-file /secure/assistant.prod.env -p assistant-milvus-stage -f docker-compose.prod.yml -f deploy/compose.milvus.prod.yml up -d --build --wait --wait-timeout 600
docker compose --env-file /secure/assistant.prod.env -p assistant-milvus-stage -f docker-compose.prod.yml -f deploy/compose.milvus.prod.yml ps
```

覆盖将镜像 `INSTALL_MILVUS` build 参数置为 true，仅安装现有 `milvus` extra，默认镜像保持关闭。app 等待数据库和 Milvus 健康，Milvus 等待 etcd/MinIO 健康。资源上限为初始限制，未证明满足容量或延迟指标。

## 健康、故障和回滚

app 继承 Dockerfile 的健康探针，统一维护健康端点。该探针同时校验 `/api/v1/health` HTTP 200 与响应 `status=ok`，不能把 degraded 的 200 当就绪。数据库用 pg_isready；Milvus/MinIO/etcd 使用各自健康端点。数据库就绪仅代表连接能力，不代替应用迁移、PostgreSQL 演练或业务验收。

服务 unhealthy 时先检查 `ps` 和受限访问的日志：数据库凭据/连接串、迁移失败、模型配置、服务内存限制和向量服务依赖。日志也可能含业务数据，不粘贴原始日志和环境输出到公开渠道。依赖健康条件只控制初始启动顺序；运行中依赖故障不会自动触发所有下游重启，应用失败由重启策略处理，恢复仍需业务验证。

停止验证可用相同参数的 `stop`；禁止为回滚使用 `down -v`、删除命名卷或重新初始化数据库。`pgdata` 保留 PostgreSQL，`appdata` 保留源文件和 local 向量数据；可选卷保留 Milvus、etcd、MinIO 数据。应用以单 worker 运行，不声称多实例状态已经外部化。

代码回退使用经过验证的旧镜像与相同受控配置，先核对 schema/索引兼容并按备份恢复手册处理。镜像或 Compose 回退不自动回滚数据库迁移与索引；真实数据恢复、重要数据覆盖需要独立授权。

静态契约检查：`python scripts/check_production_compose.py`。它不读取 `.env`、不展开密钥、不访问 Docker；通过结果不能替代上述容器与业务验收。

## 备份与恢复边界

命名卷不是备份。默认开发 SQLite 的备份/恢复步骤仍见 README；生产编排使用 PostgreSQL，不能将复制 SQLite 文件当作本编排的恢复方案。部署者需在受控位置保存数据库逻辑备份、源文件卷，以及启用 Milvus 时对应的元数据/对象存储备份，并保留版本与校验记录。

数据库逻辑备份使用容器内已有 `pg_dump`，将输出重定向到受限访问的备份文件；不要把数据内容或凭据打印到公共日志。恢复时先核对目标为独立空库，使用同版本 PostgreSQL 的 `psql`/`pg_restore` 按备份格式导入，再核验迁移修订、用户/租户/对话、源文件及适用索引身份，最后执行健康与业务检查。覆盖已有重要数据或恢复到生产前必须另获明确授权，不能自动执行清库或删卷。

本批未运行 PostgreSQL 或 Milvus 的真实备份/恢复；上述步骤是运行说明，不能作为已演练证据。自动备份脚本和生产数据库/恢复指标演练仍由相应专项承担，NFR-003 不因此关闭。
