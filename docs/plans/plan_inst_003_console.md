# INST-003：镜像带上已构建的控制台

> 状态：已实现（INST-003 `done`）
> 来源：交付排期 B1；`tasks.yaml` INST-003
> 日期：2026-09-25
> 截止：2026-10-30
> 依赖：INST-002 已完成

按文档启动编排后，浏览器打开应用地址就能看到控制台。页面来自镜像内的构建产物，由同一个 API 进程提供，不另起前端开发服务器。

## 目标

1. 镜像构建时产出 `frontend/dist`，并放进应用镜像。
2. 编排启动后打开该进程即可看到控制台。
3. 本地测试在未开启托管时，`GET /` 仍返回 JSON。

## 非目标

不改知识库、对话或租户页面的业务行为。不实现租户与用户管理页面。不把默认向量库改为 Milvus。不改 JWT、模型密钥或 Milvus 服务的启动条件。不把 Vite 开发服务器放进镜像或编排。

## 现状

- `Dockerfile` 只安装 Python 依赖并启动 uvicorn，不构建前端。
- 应用已支持托管：`SERVE_FRONTEND=true` 且 `frontend/dist` 存在时，`GET /` 返回 `index.html`，`/assets` 提供静态文件，其余非 API 路径回退到入口页。`/api/*` 仍走接口。该开关默认 `false`，避免本机一旦构建前端，测试里的 `GET /` JSON 契约就变掉。
- 控制台请求使用相对路径 `/api`，与 API 同源，不需要在构建时写入后端地址。
- 编排没有设置 `SERVE_FRONTEND`。

## 实现方案

只改编排、镜像、说明和任务状态。允许路径：`Dockerfile`、`docker-compose.yml`、`README.md`、`frontend/`、`docs/plans/plan_b1_install.md`、`tasks.yaml`、`AGENTS.md`。现有托管逻辑已满足页面打开方式，不改 `app/main.py`，也不改 `SERVE_FRONTEND` 的代码默认值。

### 1. 镜像内构建控制台

`Dockerfile` 增加 Node 构建阶段，使用固定的 Node 22 补丁标签，不用 `latest`。

- 只复制 `frontend/package.json`、`frontend/package-lock.json` 和构建所需源码。
- 执行 `npm ci`，再执行 `npm run build`。产物目录为 `frontend/dist`。
- Python 阶段在复制应用之后，用构建阶段的 `dist` 覆盖镜像内的 `frontend/dist`。最终镜像不保留 Node 和 `node_modules`。
- 构建产物目录交给运行用户可读。

不在本阶段改页面、接口调用或样式。

### 2. 编排打开托管

`app` 服务增加 `SERVE_FRONTEND=true`。这样文档化的 `docker compose up` 会由同一进程提供控制台。未走编排、直接在本机跑测试时，默认仍不托管。

不改 `.env.example` 里的默认值。该文件不在本任务允许路径内，本地开发继续按需自行打开。

### 3. 文档

README 的 Docker 一节写明：配置 JWT 与模型密钥并启动后，浏览器访问 `http://localhost:8000` 打开的是已构建的控制台。`/api/health` 仍是健康检查。开发热更新仍使用 `frontend` 下的 `npm run dev`，那个地址不是本安装路径。

## 验收

- `cd frontend; npm run typecheck` 通过。
- `cd frontend; npm run build` 通过，并生成 `frontend/dist`。
- `docker compose config` 中应用的 `SERVE_FRONTEND` 为真，`RAG_VECTOR_STORE` 仍为 `local`。
- 不把 Vite 开发服务器写成安装后的控制台。

规定命令是前端类型检查和构建。它们证明产物能编出来。镜像是否把该产物打进去，由 Dockerfile 的构建阶段保证；本方案不把拉起完整容器写成必做命令。

## 风险与回滚

L1。回滚时去掉 Node 构建阶段和编排中的 `SERVE_FRONTEND`，镜像恢复为只启动 API。本地 `frontend/dist` 可留在磁盘，不提交。

镜像构建会多一次前端依赖安装，时间变长。Node 阶段与 Python 阶段分开，避免把 Node 留在最终镜像里。
