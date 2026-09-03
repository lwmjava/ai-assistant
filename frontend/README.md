# ai-assistant 控制台前端

企业级 AI 助手平台 `ai-assistant`（FastAPI 后端）的配套 Web 控制台。

技术栈：**Vite 5 + React 18 + TypeScript + Tailwind CSS 3**，配合 React Router v6、
TanStack Query v5、Zustand、React Hook Form + Zod。

---

## 快速开始

```bash
cd frontend
npm install          # 首次安装（约 7 分钟，245 个包）
npm run dev          # 开发服务器 http://localhost:5173
```

生产构建：

```bash
npm run build        # 类型检查 + 打包，产物在 frontend/dist
npm run preview      # 本地预览构建产物
```

> 开发服务器默认把 `/api` 代理到 `http://127.0.0.1:8000`。若后端端口不同，
> 用 `VITE_PROXY_TARGET=http://127.0.0.1:8123 npm run dev` 覆盖。

### 前置条件

1. 后端已启动且可访问（默认 `http://127.0.0.1:8000`）。
2. 后端 `.env` 中 `CORS_ORIGINS` 包含前端来源（开发环境默认 `*` 即可）。
3. 已存在可登录账号（后端首启按 `INITIAL_ADMIN_USERNAME` / `INITIAL_ADMIN_PASSWORD` 引导创建）。

### 部署：由后端进程一并托管

构建产物可由 FastAPI 直接托管，**无需 Nginx 或额外的 Node 进程**：

```bash
npm run build                   # 产物写入 frontend/dist
# 后端 .env 中设置 SERVE_FRONTEND=true
cd .. && uvicorn app.main:app   # 访问 http://127.0.0.1:8000 即为控制台
```

托管与否由 `SERVE_FRONTEND` **显式决定**（默认 `false`），而不是取决于磁盘上
有没有构建产物——后者会让 `GET /` 的契约随本地构建状态漂移：
任何人在本地 `npm run build` 之后跑后端测试，断言 `GET /` 返回 JSON 的用例都会挂。
这个坑是实测踩出来的，因此改为配置项。

开启且产物存在时：

- 挂载 `/assets`，`GET /` 返回应用入口；
- 其余非 API 路径回退到 `index.html` 交给前端路由（支持直接刷新 `/chat`、`/workflows` 等深层路由）；
- `/api/*` **不参与回退**，未命中的 API 路径仍返回 JSON 404，不被 HTML 顶替；
- 回退时对目标路径做目录归属校验，`/../.env` 一类穿越请求一律落到 `index.html`。

开启但产物缺失时，启动日志给出警告并跳过托管，不会导致启动失败。

`index.html` 响应头为 `Cache-Control: no-cache`，保证发版后客户端立即取到新入口；
`/assets/*` 带内容哈希，可长期强缓存。

---

## 目录结构

```
frontend/
├── src/
│   ├── api/            # 各功能域的查询与变更 hooks（TanStack Query）
│   ├── components/
│   │   ├── chat/       # 会话列表、消息流、阶段指示器、输入区
│   │   ├── layout/     # 应用外壳、侧边栏、顶栏、页头
│   │   └── ui/         # Button / Field / Badge / Modal / Toast / Switch / 反馈组件
│   ├── hooks/          # useChatStream（SSE 流式对话）
│   ├── lib/            # http 客户端、SSE 解析、权限镜像、工具函数
│   ├── pages/          # Login / Chat / Knowledge / Tools / Workflows / Audit
│   ├── store/          # Zustand：auth（鉴权）、theme（主题）
│   ├── styles/         # 全局样式与设计令牌
│   └── types/          # 后端 API 契约类型
├── index.html
├── vite.config.ts      # 含 /api → 后端代理
└── tailwind.config.js  # 语义化颜色映射
```

---

## 已覆盖的后端能力

| 页面 | 路由 | 依赖端点 | 所需权限 |
|------|------|----------|----------|
| 登录 | `/login` | `POST /auth/login`、`GET /auth/me` | 公开 |
| 对话 | `/chat` | `POST /chat`（一次性）、`POST /chat/stream`（流式）、`GET /chat/conversations[/{id}]`、`DELETE /chat/conversations/{id}` | `conversations:*` |
| 知识库 | `/knowledge` | `/rag/documents` 增删改查、`/rag/documents/upload`、`POST /rag/search` | `knowledge_bases:*` |
| 工具与 MCP | `/tools` | `GET /chat/tools`、`/mcp/servers`、`/mcp/tools` | `agents:read` |
| 工作流 | `/workflows` | `/workflows` CRUD、`/run`、`/toggle`、`/{id}/executions` | `workflows:*` |
| 审计日志 | `/audit` | `GET /admin/audit-logs` | 仅 `system_admin` / `system_viewer` |

### 端点覆盖矩阵

后端共 27 个端点，前端接入 25 个。未接入的 2 个是**刻意跳过**，不是遗漏——
它们返回的数据结构与对应的列表端点完全一致，接了只会多一次无意义的请求：

| 端点 | 状态 | 说明 |
|---|---|---|
| `GET /rag/documents/{id}` | 未接 | `DocumentDetail` 目前**完全等于** `DocumentOut`（后端注释：预留扩展字段），列表数据已够用 |
| `GET /workflows/{id}` | 未接 | 返回的 `WorkflowOut` 与列表 `list[WorkflowOut]` 的元素一致，编辑直接复用列表数据 |

后端若给这两个详情端点补上列表没有的字段（如文档分块内容、工作流最近执行摘要），
再回来接入即可。

---

## 实现要点

### 0. 两种发送模式（流式 / 一次性）

对话页提供可切换的两种模式，均由输入区左下角按钮切换并记忆在本地
（`localStorage: aa-chat-mode`），二者走**同一条后端 Agent 管线**，差别只在返回方式：

| | 流式 `/chat/stream` | 一次性 `/chat` |
|---|---|---|
| 返回方式 | SSE 增量，边生成边显示 | 完整结果一次返回 |
| 中间过程 | 可见（管线阶段、工具调用） | 不可见，仅三点跳动指示 |
| `conversation_id` | **不回传**，需靠列表按 `updated_at` 兜底定位 | **回传**，可直接定位 |
| 落库时序 | `done` 之后才写库，刷新前需等待窗口 | 返回前已 commit，可立即拉取 |
| 中断 | `AbortController` 断 SSE | `AbortSignal` 断 fetch |
| 适用场景 | 交互式提问、观察 Agent 推理过程 | 长任务、弱网/代理会缓冲 SSE 的网关环境 |

选用一次性模式的实现位于 `src/api/chat.ts` 的 `sendMessage()`。
它刻意**没有**用 `useMutation`——`mutateAsync` 不转发 `AbortSignal`，
而一次性响应可能长达数十秒，必须支持中断。

### 1. POST + SSE 的流式解析

后端 `/chat/stream` 是 **POST + SSE**，浏览器原生 `EventSource` 只支持 GET 且不能带请求体，
因此 `src/lib/sse.ts` 用 `fetch` 读取 `ReadableStream`，按 SSE 帧规范手动解析
（`field: value` 行 + 空行分帧，兼容 `\n\n` 与 `\r\n\r\n`）。

最终文本以 **token 累积** 为准——后端 `ChatService.chat_stream` 落库用的也是
`"".join(collected)`，而非 `done` 事件里的 `state.answer`。二者实测并不相等，
保持一致才能避免界面显示与会话历史不一致。

### 2. 401 自动刷新

`src/lib/http.ts` 在收到 401 时触发一次 `refresh_token` 换取新令牌并重放原请求；
并发的 401 共享同一个刷新 Promise，避免重复刷新。刷新失败则清空本地会话，
由路由守卫跳转登录页。

### 3. 开关型模块的降级展示

工作流与 MCP 受后端开关门控，未启用时整组返回 **503**。前端不把它当普通错误，
而是展示「该模块未在服务端启用」并提示对应的环境变量名（如 `WORKFLOW_ENABLED`）。

### 4. 权限镜像

`src/lib/permissions.ts` 镜像了后端 `app/core/security.py` 的 `ROLE_PERMISSIONS`，
仅用于隐藏当前角色访问不了的入口与按钮。**权限判定始终在后端**，
前端这份只是体验优化；后端矩阵变更时必须同步此文件。

### 5. 主题与设计令牌

深色为默认，可在顶栏切换并持久化，首屏由 `index.html` 内联脚本防闪。
颜色全部以 HSL 分量存在 CSS 变量中（见 `src/styles/index.css`），
Tailwind 侧通过 `hsl(var(--x) / <alpha-value>)` 映射，明暗两套令牌共用同一套类名。

字体为本地打包（`@fontsource`），无运行时 CDN 依赖：
标题 Space Grotesk、正文 Plus Jakarta Sans，中文回退 PingFang SC / 微软雅黑。

---

## 已知约束

- **新建会话拿不到 ID（仅流式模式）**：后端流式响应不回传 `conversation_id`，
  前端在流结束后刷新会话列表并按 `updated_at` 取最新一条定位。
  切到一次性模式可绕开该问题（响应直接回传 ID）。若后端后续在事件中补传该字段，
  可移除 `src/pages/Chat.tsx` 中 `refreshAfterStream` 的这一段兜底。
- **消息落库时序（仅流式模式）**：后端在发出 `done` 事件之后才写库，
  前端刷新前固定等待 600ms（`PERSIST_DELAY_MS`），属工程折中而非协议保证。
  一次性模式在返回前已完成 commit，不走这个延迟。
- **文档格式**：后端仅接受 `.txt` / `.md` 的 UTF-8 文本，前端在上传前做扩展名预检。
- **审计 `details` 字段**是 JSON 字符串，前端解析失败时原样展示。

---

## 质量基线

- `npm run build` = `tsc -b`（严格模式，`noUnusedLocals` / `noUnusedParameters`）+ `vite build`，当前零错误。
- 首屏 JS 约 125 kB（gzip）：路由级懒加载，Markdown 解析器（约 47 kB gzip）仅在进入对话页时加载。
- 交互元素触摸目标 ≥ 44px（粗指针设备下通过 `.tap-target` 扩展点击区）。
- 尊重 `prefers-reduced-motion`：登录页阶段动画在该偏好下直接跳到终态。
