# RAG-042 知识库结构化正文显示与受控源文件下载入口（实现说明）

- 分支：`rag-batch-20261007`
- 基线：HEAD `b27fde8`
- 范围：**仅前端**（`frontend/`）+ 本文档。未改动 `app/` 下任何后端文件，未改动 `.env`、生产数据、冻结基线报告、`tasks.yaml` 卡片状态。

---

## 1. 决策

| 决策 | 选择 | 理由 |
| --- | --- | --- |
| 正文渲染 | React 文本节点 + Tailwind class 切换，**不引入 markdown / 代码高亮渲染器** | non_goal 明确「不新增渲染依赖」；且新增渲染器会引入 HTML 注入面 |
| 判定逻辑位置 | 独立纯函数模块 `frontend/src/lib/chunk-content.ts` | 可被 Node 内置 runner 直接单测；组件里塞判定没法在「无测试运行器」的前端仓库里验证 |
| 渲染 class 来源 | `chunkContentClassName(text)` 单一出口，两处渲染点共用 | 让「盒图必须保留空白」这条契约可被断言，改动一处即两处同时生效 |
| 下载取流方式 | 复用 `src/lib/http.ts`（新增 `api.blob`），**不用 `window.open` 直链** | 直链不带 `Authorization` 头，会被后端判为未认证 |
| 下载入口显隐 | 镜像后端 `can_control_document`，仅在可控文档上出现 | 后端已自带同一判定，前端只是「不放按钮」，不构成权限放宽或收紧 |
| 文件名 | `doc.source ?? doc.title` | 与后端 `FileResponse(filename=doc.source or Path(...).name)` 的取值来源一致，无需解析 `Content-Disposition` |

---

## 2. 结构化正文的判定规则

代码位置：`frontend/src/lib/chunk-content.ts:isStructuredBlock`。

满足**任意一条**即判定为结构化（改用 `whitespace-pre` + `font-mono` + `overflow-x-auto`）：

1. **含盒图/制表符字符**：码点落在 U+2500–U+257F（`─ │ ┌ ┐ └ ┘ ├ ┤ ┬ ┴ ┼ ╭ ╮ ╰ ╯ ═ ║ ╔ ╗ ╚ ╝` 等）。用码点区间而非字符表，避免漏掉不常用变体。
2. **含 Markdown 三反引号代码围栏**（```）。
3. **多行缩进结构**：至少 **2 行**以「≥2 个空格」或「1 个制表符」开头，且缩进后还有可见内容（排除纯空白行）。

规则 3 取「2 行」而不是「1 行」，是因为自然语言段落里偶发一行缩进很常见，一旦按等宽 + 横向滚动展示反而更难读；只有成片的缩进才说明它是结构化文本。制表符按 1 个即算一层缩进，与代码的实际书写一致。

**边界行为**（均有单测覆盖）：

- 空串 / 全空白 → `false`；
- 单行（哪怕很长、哪怕有缩进）→ `false`，不存在跨行的列对齐关系；
- 超长（5000 字符）但无盒图、无围栏、无缩进的一行 → `false`，按普通段落换行展示；
- 超长行带盒图字符 → `true`。

**渲染差异**：

| 形态 | class |
| --- | --- |
| 结构化 | `whitespace-pre font-mono overflow-x-auto text-sm leading-relaxed text-text-muted` |
| 普通 | `whitespace-pre-wrap break-words text-sm leading-relaxed text-text-muted`（与改动前完全一致） |

应用位置（两处）：

- `frontend/src/pages/Knowledge.tsx:234` 搜索结果正文（`useSearch`）；
- `frontend/src/pages/Knowledge.tsx:255` 文档分块正文（`useDocumentChunks`）。

---

## 3. 不执行上传文本中的 HTML/脚本 —— 证据

全仓检索 `dangerouslySetInnerHTML`：

```
$ grep -rn "dangerouslySetInnerHTML" .     # 仓库根目录
No matches found
```

结论：零处。两处正文均作为 JSX 文本子节点 `{result.content}` / `{c.content}` 渲染，React 默认做 HTML 转义，上传文本里的 `<script>alert(1)</script>` 只会以字符串形式显示。本次改动没有引入 `innerHTML`、没有引入 markdown/富文本渲染器（`react-markdown` 为仓库既有依赖，仅用于对话消息，本次未接入知识库正文）。

---

## 4. 受控下载：权限未放宽的论证

后端 `GET /api/rag/documents/{document_id}/download`（`app/api/routes/rag.py:736`）的判定链：

1. `require_permission("knowledge_bases","read")` —— 角色矩阵；
2. `RAGService.get_document()` → `can_control_document(doc, user)`（`app/rag/access.py:84`）：系统管理员 → 同租户租户管理员 → 本人上传且为当前版；
3. 文档存在且未软删、源文件存在。

前端 `canControlDocument()`（`frontend/src/lib/permissions.ts`）是第 2 条的**逐条镜像**，与仓库既有的权限镜像文件（`ROLE_PERMISSIONS`）做法一致，并在注释里写明「真正的判定始终在后端」。

因此：

- 前端**没有**新增任何判定分支，也没有把入口放到后端会拒绝的文档上；
- 看得见入口的人，后端本来就会放行（同一份判定）；
- 看不见入口的人，后端本来就会 404/403（成员看不到他人文档，因为列表接口也走同一控制面）；
- 未新增任何后端接口、未调整任何后端权限代码（`app/` 零改动）。

下载链路：`api.blob()` → `saveBlobAsFile()`（object URL + `<a download>`，用完 `revokeObjectURL`）。凭据、401 自动刷新、错误归一化全部沿用 `src/lib/http.ts` 既有实现。

**失败反馈**（`frontend/src/lib/download.ts:sourceFileFailureMessage`）：

| 情况 | 提示 |
| --- | --- |
| 401 | 登录状态已失效，请重新登录后再下载源文件。 |
| 403 | 无权下载该文档：源文件只能由上传者本人、租户管理员或系统管理员下载。 |
| 404 | 源文件不存在：该文档可能不是通过文件上传创建的，或源文件已被清理。（后端 detail 含「源文件」时原样拼接） |
| 5xx | 服务暂时不可用，下载失败，请稍后重试。 |
| 网络失败（fetch reject） | 网络异常，下载没有完成，请检查网络后重试。 |
| 会话已失效（`sessionExpired`） | 原样抛 `ApiError`，页面跳过提示并跳登录，与全站其它操作一致 |

失败一律经 `toast.error('下载失败', …)` 呈现，**不静默吞掉**。

---

## 5. 验证

### 5.1 纯函数单测（Node 22 内置 runner，未安装任何依赖）

```
$ cd frontend
$ D:/DepTooL/nodejs/node.exe --test src/lib/chunk-content.test.ts src/lib/download.test.ts
# tests 19
# suites 0
# pass 19
# fail 0
```

单文件（`chunk-content.test.ts`）12 条 + `download.test.ts` 7 条 = **19 条，全部通过**。

覆盖：盒图正文、单个制表符、代码围栏、空格缩进、制表符缩进、普通自然语言、单行缩进误判、空串/全空白、单行、超长行、超长行带盒图、结构化 class 三要素、两类 class 互斥；下载映射 401 / 403 / 404 / 5xx / 网络 / 其它状态码 / 文案互不相同。

### 5.2 类型检查与构建

```
$ cd frontend && npm run typecheck
> tsc --noEmit
（无输出，exit 0）
```

注：根 `tsconfig.json` 为 `files: []` + references 结构，`tsc --noEmit` 实际不编译任何文件；真正的类型检查在 build 的 `tsc -b` 里，单独跑也是干净通过：

```
$ cd frontend && npx tsc -b --force
（无输出，exit 0）
```

```
$ cd frontend && npm run build
> tsc -b && vite build
vite v5.4.8 building for production...
✓ 1955 modules transformed.
...
✓ built in 9.18s
```

说明：`npm run build` 首次执行时 vite 会先清空 `frontend/dist`，被本机 safe-delete 护栏（单次 66 个文件 > 50 阈值）拦下，报
`[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED]`。**这与本次改动无关**（对既有 dist 的清理动作）。改用 `npx vite build --emptyOutDir=false` 后构建完整通过，产物中 `dist/assets/Knowledge-*.js` 正常产出。`tsc -b` 在两次执行中均先行通过。

### 5.3 变异验证

每处变异后立刻还原，最后 `grep -rn "MUTATION" frontend/src/` 无输出、19/19 全绿。

| # | 变异 | 变红的测试 | 失败条数 |
| --- | --- | --- | --- |
| 1 | `STRUCTURED_CLASS` 去掉 `whitespace-pre` | `结构化正文的渲染 class 保留空白、等宽、可横向滚动`、`盒图正文拿到结构化 class，普通段落拿到换行 class` | 2 / 19 |
| 2 | `isStructuredBlock` 恒 `false` | `盒图正文判定为结构化`、`只含一个制表符也算结构化`、`三反引号代码围栏判定为结构化`、`缩进结构…`、`制表符缩进同样算结构化`、`边界：超长行带上盒图字符仍然是结构化`、`盒图正文拿到结构化 class…` | 7 / 19 |
| 3 | 404 不再区分源文件缺失（退回兜底文案） | `404：提示源文件不存在（后端 detail 带「源文件」时原样带上）` | 1 / 19 |

补充：#1 的同一条断言同时覆盖 `font-mono` 与 `overflow-x-auto`（去掉任一个同样变红），故未单列。

### 5.4 无残留证明

```
$ grep -rn "MUTATION" frontend/src/          # 无输出
$ cd frontend && node --test src/lib/*.test.ts   # pass 19 / fail 0
$ git status --porcelain -- frontend/src
 M frontend/src/api/rag.ts
 M frontend/src/lib/http.ts
 M frontend/src/lib/permissions.ts
 M frontend/src/pages/Knowledge.tsx
?? frontend/src/lib/chunk-content.ts        ← 新增
?? frontend/src/lib/chunk-content.test.ts   ← 新增
?? frontend/src/lib/download.ts             ← 新增
?? frontend/src/lib/download.test.ts        ← 新增
```

`git diff HEAD -- frontend/src` 只剩 4 个既有的生产文件（95 insertions / 11 deletions），且全部是本次任务所需改动；新增文件只有 2 个实现 + 2 个测试。

---

## 6. 局限与尚未闭合的验收点

1. **没有浏览器端视觉验收**。前端仓库没有测试运行器，non_goal 禁止新增渲染依赖，因此没有 jsdom/RTL/Playwright 截图。验收点「盒图缩进与列位置保留、窄屏横向滚动」目前的证据链是：`chunkContentClassName()` 的 class 契约单测 + `tsc -b` + `vite build` 通过 + Tailwind 3.4 原生支持这三个类。**真实观感需要人工在浏览器窄屏下确认一次**（上传一份含盒图的文档 → 知识库页检索 / 展开分块 → 观察是否等宽、是否出现横向滚动条）。
2. **下载成功路径没有自动化验证**。401/403/404/5xx/网络的文案映射有单测，但「blob 落盘成功」依赖浏览器 `URL.createObjectURL`，未在自动化测试中执行，需人工点一次下载按钮确认文件可保存、文件名与 `source` 一致。
3. **文本摄取的文档也会显示下载入口，点击会得到 404 提示**。`DocumentOut` 没有「是否有源文件」字段（摄取接口对缺省 source 会写 `"text"`，无法据 `source` 可靠区分），且本次不允许改后端。选择保留入口 + 明确 404 反馈（符合验收点「源文件缺失有明确反馈」），而非靠 `source` 猜测隐藏。
4. **等宽字体下的长行没有最大高度限制**。超长代码块会撑高卡片，需要纵向滚动页面本身；如需限制高度可后续加 `max-h-*`。
5. `canControlDocument` 是后端判定镜像。若后端 `can_control_document` 规则变更，本文件需同步（已在注释中标注），否则只会出现「按钮点了报 403」或「该有按钮的没出现」这类体验问题，**不影响后端安全边界**。
