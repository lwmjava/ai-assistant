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
| 文件名 | **优先后端 `Content-Disposition` 的权威取值**（含 RFC 5987 的 `filename*=UTF-8''…`），其次 `doc.source`，最后 `doc.title` | 后端 `FileResponse` 可能按 `Path(file_path).name` 兜底，前端自行拼接会与之分歧；以响应头为准可消除两端不一致。详见 §4 与 §6.7 |

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

**作用域要写清楚：只查仓库自有源码。** 在仓库根目录直接 grep 会命中 `node_modules/react-dom/*`
的 291 处（第三方自带），没有任何意义。可复现的命令与真实输出：

```
$ grep -rn "dangerouslySetInnerHTML" frontend/src app/     # 只查自有源码
frontend/src/components/knowledge/ChunkContent.test.ts:61:test('输出中不出现 dangerouslySetInnerHTML 痕迹', () => {
frontend/src/components/knowledge/ChunkContent.test.ts:62:  assert.ok(!render(BOX).includes('dangerouslySetInnerHTML'))
frontend/src/components/knowledge/ChunkContent.test.ts:63:  assert.ok(!render('<script>alert(1)</script>').includes('dangerouslySetInnerHTML'))
frontend/src/lib/chunk-content.ts:11: * `dangerouslySetInnerHTML`，正文一律作为 React 文本节点渲染（React 默认转义），
```

命中只有两类：**注释**与**测试断言**；`frontend/src` 与 `app/` 的**生产代码零处**。
（早先版本把这行写成「仓库根目录 No matches found」，是当时的检索工具默认排除了 `node_modules`，
命令本身不可复现，已更正。）

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

**文件名取值**（`frontend/src/api/rag.ts:downloadDocumentSource`）：

1. **后端 `Content-Disposition` 里的名字（权威值）**——后端是 `FileResponse(filename=doc.source or Path(file_path).name)`，
   响应头就是它自己算出来的结果；前端用 `contentDispositionFilename()` 解析
   （支持 RFC 5987 的 `filename*=UTF-8''…`，非 ASCII 文件名也能还原）；
2. 头缺失时退回 `doc.source`；
3. 再退回 `doc.title`。

> 修订记录：初版是 `doc.source || doc.title`，而后端在 `source` 为空时落的是**磁盘文件基名**
> `Path(file_path).name`，两者在 `source` 缺省的边界下会不一致（L-02）。改为「优先后端响应头的权威值」后，
> 前端不再自己造名字，只在连响应头都拿不到（例如被网关剥掉）时才逐级兜底。

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

### 5.1c 渲染类与 Tailwind safelist 的一致性（补审查 L-03）

`chunk-content.test.ts` 里 3 条：

- 常量里的每个 token 都必须在 `tailwind.config.js` 的 `safelist` 里；
- safelist 与常量集合**双向完全一致**（多一个少一个都红）；
- 常量必须是**单引号字面量**：测试直接读 `chunk-content.ts` 源码取字面量，与运行时值比对。

为什么读源码而不是只比对运行时值：Tailwind 提取器对**文件原文**做正则，类名一旦写成拼接形式
（`${WS}pre …`）就扫不到，而 `structuredClassTokens()` 仍会返回同样的 token——只比运行时值的话这个改动
会完全逃逸（审查变异 4 就是这么跑掉的）。要求单引号字面量比 Tailwind 更严格，换来的是可静态校验。

`tailwind.config.js` 侧的 `safelist` 无条件保留这 8 个 token，于是即使常量被改坏，
构建产物里这些规则也不会消失——从「静默降级」变成「CSS 仍在、但一致性测试立刻红」。

### 5.1d 权限镜像测试（补审查 M-01）

```
$ cd frontend
$ D:/DepTooL/nodejs/node.exe --test src/lib/permissions.test.ts
# tests 6
# pass 6
# fail 0
```

覆盖后端 `can_control_document` 的五条判定 + 未登录守卫：system_admin（含跨租户）放行、
跨租户一律拒绝、同租户 tenant_admin 对他人文档放行、成员仅「本人上传且 `is_current`」放行、
`is_current=false` 拒绝、`user=null` 拒绝。另有一条说明「控制面只看身份关系，`can(read)` 另把关」，
与 `Knowledge.tsx` 里 `canRead && canControlDocument(...)` 的合取一致。

### 5.1e 下载成功路径（补审查 H-01）

**(a) 前端落盘**（`download.test.ts`，约 40 行最小 DOM 桩替换 `document` / `URL.createObjectURL` /
`URL.revokeObjectURL`，零新依赖）：文件名取自传入值、点击前必须先挂到 DOM、点击后再移除节点、
`revokeObjectURL` 与 `createObjectURL` 一一配平；`contentDispositionFilename` 解析三种头格式。

**(b) 服务端往返**（`tests/test_rag_042_download_roundtrip.py`，`TestClient` 为后端既有依赖）：

```
$ DATABASE_URL="sqlite:///./data/test_rag042.db" /d/DepTooL/anaconda3/python.exe -m pytest \
      tests/test_rag_042_download_roundtrip.py -q --basetemp=data/pytest-tmp/rag042
3 passed in 11.85s
```

- `test_download_returns_uploaded_bytes_and_filename`：上传 `refund-policy.txt` → 下载 200、
  `resp.content == raw`（**字节完全相同**）、`Content-Disposition` 含原文件名；
- `test_other_member_cannot_download_others_source_file`：换同租户普通成员下载他人文档 → 404；
- `test_missing_source_file_returns_404`：删掉落盘源文件后下载 → 404「源文件不存在」。

两条环境注意事项（与改动无关）：
1. 本机共享测试库 `data/test_ai_assistant.db` 当前有 schema 漂移（缺 `rag_document_chunks.index_id`，
   由并行任务卡的未提交模型改动引起），**所有**后端用例都会因此报错，故用 `DATABASE_URL` 指向独立库；
   该库本身也有既有的内容去重行为——上传内容必须每次唯一，否则第二次运行会命中已有文档、
   新落盘的源文件被丢弃，下载必然 404，测试文件已在 docstring 里写明。
2. `--basetemp` 指向独立子目录：默认 `data/pytest-tmp/run` 累积够 3 个历史目录后，pytest 的
   tmp 清理会触发本机 safe-delete 护栏（`SAFE_DELETE_BULK_GUARD_ERROR` → `SystemExit: 1`）。

**合计 37 条**（前端 `node --test`）：15 条 chunk-content（含 3 条 safelist 一致性）+ 10 条 download
+ 6 条权限镜像 + 6 条渲染级。

```
$ D:/DepTooL/nodejs/node.exe --test src/lib/chunk-content.test.ts src/lib/download.test.ts \
      src/lib/permissions.test.ts src/components/knowledge/ChunkContent.test.ts
# tests 37
# pass 37
# fail 0
```

另有服务端往返 3 条（pytest，见 5.1e）。

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

第三轮（审查整改，34 条）——**本轮重点是补上此前两个完全逃逸的方向**：

| # | 变异 | 变红的测试 | 失败条数 |
| --- | --- | --- | --- |
| 7 | `canControlDocument` 对所有已登录用户返回 `true`（审查变异 5 复现） | `跨租户一律拒绝（系统管理员除外）`、`成员：只有本人上传且为当前版才可控`、`控制面只看身份关系，不看角色权限矩阵（read 权限另由 can() 把关）` | **3 / 34** |
| 8 | `saveBlobAsFile` 去掉 `URL.revokeObjectURL` | `objectURL 用完即释放，与创建次数配平` | **1 / 34** |
| 9 | `contentDispositionFilename` 恒返回 `null` | `Content-Disposition 文件名解析：优先后端给的权威取值` | 1 / 34 |

补充：#1/#5 的同一条断言同时覆盖 `font-mono` 与 `overflow-x-auto`（去掉任一个同样变红），故未单列。
审查发现的**类名拼接**（`${WS}pre …`）逃逸方向仍未守护——它没有单测，
只能靠构建后 grep 产物 CSS；已列入第 6 节已知取舍与后续项（safelist 或 CI grep）。

第四轮（L-03 整改，37 条）：

| # | 变异 | 变红的测试 | 失败条数 |
| --- | --- | --- | --- |
| 10 | 从 `tailwind.config.js` 的 safelist 里删掉 `whitespace-pre` | `渲染类常量必须逐 token 出现在 Tailwind safelist 里`、`safelist 与常量集合双向完全一致：任一侧多一个/少一个都要红` | 2 / 37 |
| 11 | 常量改成拼接形式 `` `${WS}pre font-mono …` ``（审查变异 4 复现） | `safelist 与常量集合双向完全一致：任一侧多一个/少一个都要红`、`常量必须是静态字面量：拼接写法会让提取器失效` | 2 / 37 |

第 11 条此前**完全逃逸**（25/25 全绿），现已闭合。配套做法是 `tailwind.config.js` 加 `safelist`
无条件保留这 8 个 token：即使有人绕过测试改坏常量，构建产物里规则仍在，不会静默降级。

### 5.4 无残留证明

```
$ grep -rn "MUTATION" frontend/src/ frontend/tailwind.config.js   # 无输出（四轮后均无输出）
$ cd frontend && node --test <四个测试文件>   # pass 37 / fail 0
$ git diff --exit-code HEAD -- frontend/src  # 退出码 0
$ git status --porcelain -- frontend/src     # 无输出
```

工作区零残留：所有改动（含新增文件）均已随 commit 落库。累计改动面为 5 个既有生产文件
（`api/rag.ts`、`lib/http.ts`、`lib/permissions.ts`、`pages/Knowledge.tsx`、`tailwind.config.js`）
+ 3 个新增实现/组件文件（`lib/chunk-content.ts`、`lib/download.ts`、`components/knowledge/ChunkContent.ts`）
+ 4 个测试文件（3 个前端 + `tests/test_rag_042_download_roundtrip.py`）。

---

## 6. 局限与尚未闭合的验收点

1. **Tailwind 类是否落进产物 CSS：已自动化验证（不再是「只能人工」）。**
   做法是构建产物级查证 + 对照实验（关键坑：Tailwind 提取器**也扫注释**，它对文件原文做正则、
   不剥注释，所以「CSS 里有这个类」本身不能证明是常量被扫到——必须做对照实验）：

   ```
   $ grep -o '\.whitespace-pre[^{]*{[^}]*}' dist/assets/index-D6SNo6pV.css   # 本次产物
   .whitespace-pre{white-space:pre}
   .whitespace-pre-wrap{white-space:pre-wrap}

   $ grep -o '\.whitespace-pre[^{]*{[^}]*}' dist/assets/index-BZr_Fs9Y.css   # 基线产物
   .whitespace-pre-wrap{white-space:pre-wrap}
   ```

   基线只有 `whitespace-pre-wrap`，本次产物多出 `.whitespace-pre{white-space:pre}`。
   **决定性对照实验**：保留 `chunk-content.ts` 的字面量常量不变，仅把注释里的 `whitespace-pre`
   全部替换掉（使 `src` 下非测试文件中的 `whitespace-pre` 只剩常量本身），重建后产物**仍**含
   `.whitespace-pre{white-space:pre}` —— 证明该规则由常量编译而来。purge 疑虑解除。
   `.font-mono` / `.overflow-x-auto` 同样在产物中，基线已被其它页面用到。
   **仍然只能人眼的部分**：窄屏下是否真的出现可用横向滚动条、盒图是否被压扁（L-01，已知取舍）。
2. **下载成功路径：已自动化到「字节级 + 落盘动作级」，只剩浏览器保存对话框需人眼。**
   服务端往返（200 + 字节完全相同 + `Content-Disposition` 文件名）与前端落盘
   （文件名 / 挂载后 click / `revokeObjectURL` 配平）都已有测试，见第 5.1d 节。
   仍未自动化的是「浏览器是否真的弹出保存对话框并把文件写进磁盘」——这确实需要人工点一次。
3. ~~文本摄取型文档显示下载入口~~ → **已裁定**：保留入口 + 明确 404 反馈，理由与后续项见第 4.1 节
   （后端补 `has_source_file` 后可改为隐藏入口，需另开卡）。
4. **已知取舍（不改）**：等宽长行没有加最大高度。超长代码块会撑高卡片，需要纵向滚动页面本身；
   加 `max-h-*` 反而会让盒图被纵向截断、看不到全貌，因此维持当前行为。
5. `canControlDocument` 是后端判定镜像，**现已由 `permissions.test.ts` 6 条用例守护**
   （审查变异「放开全部用户」会让其中 3 条变红）。若后端 `can_control_document` 规则变更，
   本文件需同步（已在注释中标注）；即便不同步，后端仍会独立拦一道，**不影响安全边界**。
6. `ChunkContent.ts` 用 `createElement` 而非 JSX（原因见第 3.3 节）。这是为了让生产组件能被 Node
   直接加载做渲染级断言而付的代价；若将来仓库引入正式的测试运行器（vitest 等），可改回 JSX 并保留同一套断言。
   **触发条件已写进组件注释**：一旦这个组件需要第二个元素或条件分支，优先改回 JSX。
7. **文件名兜底与后端仍有理论分歧**：已改为优先后端 `Content-Disposition` 的权威值，
   只有在响应头被网关剥掉时才会落到 `source` → `title`。真正彻底的解法是后端补
   `has_source_file`（第 4.1 节后续项），一并消除入口显隐的猜测。
8. ~~类名被改成拼接形式仍会静默失效~~ → **已闭合**：`tailwind.config.js` 加了 `safelist`
   无条件保留这 8 个 token，`chunk-content.test.ts` 另加 3 条一致性测试（双向相等 + 必须是字面量）。
   构建期兜底与防漂移成对出现：改坏常量 → 测试立刻红；绕过测试 → CSS 规则仍在。
   **残留风险**：safelist 只保 token 存在，不保它们仍被正确使用（例如常量改成了别的类，
   一致性测试会红，但如果连测试一起绕过，safelist 会白白保留旧 token——无害但会有冗余 CSS）。
