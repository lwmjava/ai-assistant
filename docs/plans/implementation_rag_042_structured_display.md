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

应用位置（两处，共用同一个展示组件 `frontend/src/components/knowledge/ChunkContent.ts`）：

- `frontend/src/pages/Knowledge.tsx` 搜索结果正文（`SearchResultRow`，`useSearch`）；
- `frontend/src/pages/Knowledge.tsx` 文档分块正文（`DocumentChunkList`，`useDocumentChunks`）。

抽组件而不是在两处各写一遍 `className={chunkContentClassName(...)}`，是为了让渲染级测试能直接渲染
生产组件本身（见第 3.2 / 3.3 节），也让「两处行为一致」由构造而非约定来保证。

---

## 3. 不执行上传文本中的 HTML/脚本 —— 证据

### 3.1 静态证据

全仓检索 `dangerouslySetInnerHTML`：

```
$ grep -rn "dangerouslySetInnerHTML" .     # 仓库根目录
No matches found
```

结论：零处。本次改动没有引入 `innerHTML`、没有引入 markdown/富文本渲染器（`react-markdown` 为仓库既有依赖，仅用于对话消息，本次未接入知识库正文）。

### 3.2 渲染级证据（不靠人眼）

静态 grep 只能证明「没写那行代码」，证明不了「输出真的被转义」。因此补了渲染级测试：
`frontend/src/components/knowledge/ChunkContent.test.ts` 用 `react-dom/server` 的
`renderToStaticMarkup` 渲染**真实的** `ChunkContent` 组件（不是复制品），断言最终 HTML 字符串。
`react-dom` 是本仓库既有依赖，未新增任何依赖。

真实输出片段（`node --input-type=module -e` 直接渲染组件后打印）：

```
[盒图] <p class="whitespace-pre font-mono overflow-x-auto text-sm leading-relaxed text-text-muted">┌─────┐
│ 订单 │
└─────┘</p>
[缩进] <p class="whitespace-pre font-mono overflow-x-auto text-sm leading-relaxed text-text-muted">    def f():
        pass</p>
[脚本] <p class="whitespace-pre-wrap break-words text-sm leading-relaxed text-text-muted">&lt;script&gt;alert(1)&lt;/script&gt;</p>
[属性] <p class="whitespace-pre-wrap break-words text-sm leading-relaxed text-text-muted">&lt;img src=x onerror=alert(1)&gt;</p>
[普通] <p class="whitespace-pre-wrap break-words text-sm leading-relaxed text-text-muted">退款政策如下。
七日内可申请。</p>
```

读出来的结论：

1. **盒图字符与换行原样保留**：`┌─────┐ / │ 订单 │ / └─────┘` 三行连同 `\n` 一字不差出现在输出里；
2. **列位置保留**：`    def f():`（4 空格）与 `        pass`（8 空格）的前导空格完整保留；
3. **HTML/脚本不执行**：`<script>` 变成 `&lt;script&gt;`，`<img ... onerror=...>` 整段被转义成文本，输出里不存在未转义的 `<script` / `<img` 标签起始；
4. 输出中不含 `dangerouslySetInnerHTML` 痕迹（同时对渲染结果做字符串断言，并再次 grep 全仓确认零处）；
5. 普通自然语言走非结构化分支（无 `whitespace-pre`），但仍保留换行。

### 3.3 为什么组件用 `createElement` 而不是 JSX

Node 内置的类型擦除只认 `.ts`，加载 `.tsx` 直接失败（实测，Node v22.20.0）：

```
TypeError [ERR_UNKNOWN_FILE_EXTENSION]: Unknown file extension ".tsx" for ...\comp.tsx
    at Object.getFileProtocolModuleFormat [as file:] (node:internal/modules/esm/get_format:219:9)
```

（`--experimental-transform-types` 同样报此错。）为了让**生产组件本身**能被 `node --test` 直接加载并做渲染级断言，`frontend/src/components/knowledge/ChunkContent.ts` 用 `createElement` 表达、以 `.ts` 结尾。渲染语义与 JSX 版完全一致（正文始终是 React 文本子节点），代价只是这一个单元素组件不写 JSX 标签。另一条路（挂 esbuild loader 钩子把 `.tsx` 转译后再给 Node 加载）会往仓库里塞测试基建，且依赖 `esbuild` 这个传递依赖，不划算。

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

### 4.1 已知取舍：文本摄取型文档也会显示下载入口

**取舍：保留入口 + 明确 404 反馈，而不是靠 `source` 猜着隐藏。**

- 原因一：`DocumentOut` 不带「是否有源文件」字段，前端无从判断。
- 原因二：按 `source` 猜不可靠——文本摄取接口在 `source` 缺省时会写入 `"text"`，而上传文档
  `source` 才是文件名，两者无法用前端可见字段稳定区分（手工摄取时填了文件名也一样混淆）。
- 原因三：`app/` 不在本卡范围，不能补字段。

保留入口后，点击这类文档会得到 404 的明确中文提示（"源文件不存在：该文档可能不是通过文件上传创建的，或源文件已被清理"），
正好落在验收条款「源文件缺失有明确反馈」上，比悄悄藏掉按钮更诚实。

**后续项（需另开卡）**：后端 `DocumentOut` 补 `has_source_file`（或等价字段）后，前端可改为
`canDownload && doc.has_source_file` 隐藏入口。本卡不做。

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

### 5.1b 渲染级测试（`react-dom/server`，既有依赖）

```
$ cd frontend
$ D:/DepTooL/nodejs/node.exe --test src/components/knowledge/ChunkContent.test.ts
# tests 6
# pass 6
# fail 0
```

6 条断言见第 3.2 节的真实输出（盒图/换行保留、4 与 8 空格列位置、`<script>` 转义、`<img onerror=>` 转义、输出无 `dangerouslySetInnerHTML`、普通段落走非结构化分支）。

**合计 25 条**（19 纯函数 + 6 渲染级）：

```
$ D:/DepTooL/nodejs/node.exe --test src/lib/chunk-content.test.ts src/lib/download.test.ts src/components/knowledge/ChunkContent.test.ts
# tests 25
# pass 25
# fail 0
```

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

每处变异后立刻还原，最后 `grep -rn "MUTATION\|dangerouslySetInnerHTML" frontend/src/` 只剩测试断言与注释、25/25 全绿。

第一轮（纯函数阶段，19 条）：

| # | 变异 | 变红的测试 | 失败条数 |
| --- | --- | --- | --- |
| 1 | `STRUCTURED_CLASS` 去掉 `whitespace-pre` | `结构化正文的渲染 class 保留空白、等宽、可横向滚动`、`盒图正文拿到结构化 class，普通段落拿到换行 class` | 2 / 19 |
| 2 | `isStructuredBlock` 恒 `false` | `盒图正文判定为结构化`、`只含一个制表符也算结构化`、`三反引号代码围栏判定为结构化`、`缩进结构…`、`制表符缩进同样算结构化`、`边界：超长行带上盒图字符仍然是结构化`、`盒图正文拿到结构化 class…` | 7 / 19 |
| 3 | 404 不再区分源文件缺失（退回兜底文案） | `404：提示源文件不存在（后端 detail 带「源文件」时原样带上）` | 1 / 19 |

第二轮（渲染级阶段，25 条）：

| # | 变异 | 变红的测试 | 失败条数 |
| --- | --- | --- | --- |
| 4 | 组件改用 `dangerouslySetInnerHTML` | `脚本注入：<script> 被转义，输出里没有可执行的标签`、`属性注入：<img onerror=...> 整段作为文本转义，没有真的标签` | 2 / 25 |
| 5 | `STRUCTURED_CLASS` 去掉 `whitespace-pre` | `盒图正文：盒图字符与换行原样保留，且套上结构化 class`、`缩进与列位置：4 / 8 个前导空格必须还在`、`结构化正文的渲染 class 保留空白…`、`盒图正文拿到结构化 class…` | 4 / 25 |
| 6 | 组件渲染时把 `\n` 替换成空格 | `盒图正文：盒图字符与换行原样保留…`、`普通自然语言段落：走非结构化分支，但仍保留换行` | 2 / 25 |

补充：#1/#5 的同一条断言同时覆盖 `font-mono` 与 `overflow-x-auto`（去掉任一个同样变红），故未单列。

### 5.4 无残留证明

```
$ grep -rn "MUTATION" frontend/src/          # 无输出（第二轮后同样无输出）
$ cd frontend && node --test <三个测试文件>   # pass 25 / fail 0
$ git diff --exit-code HEAD -- frontend/src  # 退出码 0
$ git status --porcelain -- frontend/src     # 无输出
```

工作区零残留：所有改动（含新增文件）均已随 commit 落库。累计改动面为 4 个既有生产文件
（`api/rag.ts`、`lib/http.ts`、`lib/permissions.ts`、`pages/Knowledge.tsx`）+ 新增 3 个实现/组件文件
（`lib/chunk-content.ts`、`lib/download.ts`、`components/knowledge/ChunkContent.ts`）+ 3 个测试文件。

---

## 6. 局限与尚未闭合的验收点

1. ~~**没有浏览器端视觉验收**~~ → **已闭合（改为渲染级证据）**：第 3.2 节用 `react-dom/server` 渲染真实组件，
   直接证明了「盒图字符与换行原样保留」「4/8 空格列位置保留」「HTML/脚本被转义」。
   **仍未自动化的是 Tailwind 类真正作用到像素的那一步**：`whitespace-pre` / `overflow-x-auto`
   是否在本项目的 Tailwind 构建里真的产出了对应 CSS（类是否被 purge 掉、窄屏是否真的出现横向滚动条），
   这条只能靠人工在窄屏浏览器里看一次（上传含盒图文档 → 检索 / 展开分块）。
   间接证据：`dist/assets/index-*.css` 在构建后为 66.59 kB，且这三个类由 `chunk-content.ts`
   里的**字面量常量**提供（不是拼出来的动态类名），Tailwind 3.4 的扫描能命中，不会被 purge。
2. **下载成功路径没有自动化验证**。401/403/404/5xx/网络的文案映射有单测，但「blob 落盘」依赖浏览器
   `URL.createObjectURL`，未在自动化测试中执行，需人工点一次下载按钮确认文件可保存、文件名与 `source` 一致。
3. ~~文本摄取型文档显示下载入口~~ → **已裁定**：保留入口 + 明确 404 反馈，理由与后续项见第 4.1 节
   （后端补 `has_source_file` 后可改为隐藏入口，需另开卡）。
4. **已知取舍（不改）**：等宽长行没有加最大高度。超长代码块会撑高卡片，需要纵向滚动页面本身；
   加 `max-h-*` 反而会让盒图被纵向截断、看不到全貌，因此维持当前行为。
5. `canControlDocument` 是后端判定镜像。若后端 `can_control_document` 规则变更，本文件需同步（已在注释中标注），
   否则只会出现「按钮点了报 403」或「该有按钮的没出现」这类体验问题，**不影响后端安全边界**。
6. `ChunkContent.ts` 用 `createElement` 而非 JSX（原因见第 3.3 节）。这是为了让生产组件能被 Node
   直接加载做渲染级断言而付的代价；若将来仓库引入正式的测试运行器（vitest 等），可改回 JSX 并保留同一套断言。
