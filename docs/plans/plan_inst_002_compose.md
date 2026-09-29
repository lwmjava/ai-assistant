# INST-002：Compose 带上开发用 Milvus，并要求配置 JWT 与模型密钥

> 状态：已实现（INST-002 `done`）
> 来源：交付排期 B1；`tasks.yaml` INST-002
> 日期：2026-09-25
> 截止：2026-10-30
> 依赖：INST-001 已完成

配置好 JWT 和一个模型密钥后，`docker compose` 能拉起应用、PostgreSQL 和开发用 Milvus。未配置这两项时，编排不能再用仓库里的 JWT 占位口令假装已经配好。默认向量库仍是 `local`。

## 目标

1. `docker-compose.yml` 包含一个开发用 Milvus 服务。
2. 文档化启动步骤要求自行配置 `JWT_SECRET_KEY` 与 `LLM_API_KEY`。
3. 编排里的 `RAG_VECTOR_STORE` 默认仍是 `local`。

## 非目标

不核对 ADR-0002 第 5 节五条门槛，那是 C6。不把默认向量库改为 `milvus`。不把 Milvus 写成已经 `Implemented` 或生产可用。不安装 `pymilvus`，不改健康检查代码。不改应用里的 JWT 或模型校验逻辑。不实现租户、用户或控制台托管。不把真实密钥写入仓库，不修改 `.env`。

## 现状

- 编排只有 `db`（Postgres 16）和 `app`。`JWT_SECRET_KEY` 在未设置时回落到 `change-me-in-production-use-a-random-secret`。应用的 `ENV` 默认为 `production`。
- 生产环境下，这个占位口令会在进程启动时被拒绝。模型密钥为空时，只有真正创建模型提供商才会在生产环境报错；开发环境会降级为 Mock。编排目前没有把 `LLM_API_KEY` 传进应用容器。
- `RAG_VECTOR_STORE` 的代码默认值是 `local`。`.env.example` 里也是 `local`。`MILVUS_URI` 示例指向 `http://localhost:19530`，编排里没有对应服务。
- 健康检查只有在当前向量库配置为 `milvus` 时才会连接 `MILVUS_URI`。默认 `local` 时不探测 Milvus。

## 实现方案

只改编排、示例配置和文档。允许路径：`docker-compose.yml`、`.env.example`、`README.md`、`docs/plans/plan_b1_install.md`、`tasks.yaml`、`AGENTS.md`。

### 1. 开发用 Milvus 服务

在 `docker-compose.yml` 增加服务 `milvus`：

- 使用官方 Milvus 2.4 或 2.5 的一个固定补丁标签，不用 `latest`。实施时按该标签的官方单容器示例填写启动命令。
- 单容器、嵌入式元数据、本地文件存储，并挂数据卷。不另起 etcd 和对象存储，也不做成多节点集群。
- 容器内端口 `19530`。不把该端口发布到宿主机，除非文档需要本机直连；应用通过服务名访问。
- 为该服务配置官方示例中的健康检查。`app` 在数据库和 Milvus 都健康后再启动。

应用环境增加 `MILVUS_URI=http://milvus:19530`，供以后显式把向量库改成 `milvus` 时使用。本任务不设置 `RAG_VECTOR_STORE=milvus`。

文档称它为开发用 Milvus，对应 ADR 里的开发与试点用途。不写「生产已使用 Milvus」，也不写五条门槛已通过。

### 2. 启动前必须配置的两项

从 `app.environment` 去掉 JWT 占位口令的默认值。

改为 Compose 必填插值：

- `JWT_SECRET_KEY` 未设置或为空时，`docker compose config` 失败，错误信息说明必须配置，且不能使用仓库内占位口令。
- `LLM_API_KEY` 同样必填，并传入应用容器。未设置或为空时，`docker compose config` 失败。

占位字符串 `change-me-in-production-use-a-random-secret` 不再出现在 `docker-compose.yml`。若有人在 `.env` 里仍填这个占位值，现有生产启动校验会拒绝启动；本任务不改那段校验代码。

`.env.example` 里 `JWT_SECRET_KEY` 不再写成可直接拿去启动编排的值，改为空值并注明编排启动前必须换成随机密钥。`LLM_API_KEY` 保持为空，并注明编排启动前必须填写一个模型密钥。`RAG_VECTOR_STORE=local` 保持不变。

README 的 Docker 一节改为：复制 `.env.example` 为 `.env`，填入随机 `JWT_SECRET_KEY` 和一个 `LLM_API_KEY`，再执行 `docker compose up -d --build`。写明未填这两项时编排配置无法展开。不提供真实密钥样例。

Postgres 的开发口令、初始管理员口令不在本任务范围。

### 3. 默认向量库

编排显式传入 `RAG_VECTOR_STORE=${RAG_VECTOR_STORE:-local}`。未在环境里改写时就是 `local`。不在 `app` 服务里写死 `milvus`。

## 验收

- `docker compose config` 在缺少 `JWT_SECRET_KEY` 或 `LLM_API_KEY` 时失败，且展开结果不会含有仓库内 JWT 占位口令。
- 两项都设置为非空、且 JWT 不是占位口令时，`docker compose config` 成功，服务列表含 `db`、`milvus`、`app`，应用的 `RAG_VECTOR_STORE` 为 `local`，`MILVUS_URI` 指向 `http://milvus:19530`。
- 不把这次配置成功写成五条闭环门槛已过。

本任务的规定命令是 `docker compose config`。它检查编排定义，不要求拉镜像或启动容器。

## 风险与回滚

L1。回滚时恢复只有应用和 PostgreSQL 的编排，并恢复文档中的原启动命令。已写入外部 `.env` 的密钥留在本机，不提交、不回滚进仓库。

Milvus 单容器的启动参数随镜像标签变化。实施时以所选标签的官方示例为准，避免凭记忆填写存储环境变量。

## 验证结果（2026-09-29，EVD-002）

Docker 客户端 28.1.1。`docker compose config` 不连接引擎。本次没有执行 `docker compose up`。

项目目录中的 `.env` 会被 `docker compose` 自动读入。把 PowerShell 里的变量设成空字符串后直接执行 `docker compose config`，缺少 `JWT_SECRET_KEY` 的那次退出码为 0，展开结果含有仓库内占位口令 `change-me-in-production-use-a-random-secret`。展开正文未收录。

下面三次改用系统临时目录中的 env 文件代替默认 `.env`，并在 `cmd` 里设置对应的进程变量。临时文件只含当次探针值，跑完已删除。探针值不写入本文。占位口令检查的是字符串 `change-me-in-production-use-a-random-secret` 是否出现。

缺少 `JWT_SECRET_KEY`（另一项为非空探针），退出码 1，输出不含该占位口令：

```text
error while interpolating services.app.environment.JWT_SECRET_KEY: required variable JWT_SECRET_KEY is missing a value: 必须配置随机 JWT_SECRET_KEY，不能使用仓库内占位口令
```

缺少 `LLM_API_KEY`（JWT 为非空探针，且不是占位口令），退出码 1，输出不含该占位口令：

```text
error while interpolating services.app.environment.LLM_API_KEY: required variable LLM_API_KEY is missing a value: 必须配置一个模型密钥
```

两项都设置为非空、且 JWT 不是占位口令时，`docker compose config` 退出码 0。输出不含该占位口令。服务含 `db`、`milvus`、`app`。应用的 `RAG_VECTOR_STORE` 为 `local`，`MILVUS_URI` 为 `http://milvus:19530`，`SERVE_FRONTEND` 为 `"true"`。这次配置成功只说明编排定义能展开。Milvus 五条门槛未在本次核对，默认向量库仍是 `local`。
