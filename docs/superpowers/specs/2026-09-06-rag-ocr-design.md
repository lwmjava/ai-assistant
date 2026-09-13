# RAG OCR 扫描版 PDF 识别设计

## 1. 背景

当前 RAG 文档导入已经具备：

- 多格式文件解析
- 异步导入任务平台
- 批量导入、URL 导入
- 失败治理、重试、重解析
- 版本链与去重

但扫描版 PDF 仍然无法被摄取。现状是：

- `app/rag/document_parsers/pdf.py` 可以提取带文本层的 PDF
- 当 PDF 可打开但提取不到文本时，会抛出“需要 OCR，但当前未启用 OCR”

这会导致企业里常见的扫描件、合同扫描版、盖章 PDF 无法进入知识库。

本设计的目标是在**不推翻现有导入平台**的前提下，为扫描版 PDF 增加 OCR 能力，并为未来云 OCR 扩展预留统一接口。

## 2. 目标与非目标

### 2.1 目标

本期需要完成：

1. 为扫描版 PDF 增加 OCR 文本提取能力。
2. OCR 采用 provider 抽象，默认实现为本地 Tesseract。
3. 预留云 OCR provider 的接入位置，但本期不实现真实云调用。
4. 首批 OCR 语言支持中文简体与英文。
5. 当 OCR 未启用、依赖缺失、执行失败、识别为空时，给出明确错误语义。
6. 与现有导入任务平台集成，复用失败治理、重试与重解析。
7. 通过测试保证：
   - 带文本层 PDF 不误走 OCR
   - 扫描版 PDF 可通过 OCR 成功入库
   - OCR 失败可稳定落为任务失败原因

### 2.2 非目标

本期明确不做：

1. 图片文件上传 OCR（如 jpg/png）。
2. doc/ppt 内嵌图片 OCR。
3. 网页截图 OCR。
4. 云 OCR 真实对接。
5. OCR 结果人工校对界面。
6. OCR 后处理增强（版面分析、表格结构恢复、坐标信息输出）。

## 3. 方案对比

### 方案 A：直接在 PDF 解析器里写死 Tesseract

优点：

- 实现最快
- 改动面小

缺点：

- 后续接云 OCR 需要再拆分
- 测试会和 PDF 解析器内部耦合过深
- 配置策略不够清晰

### 方案 B：OCR provider 抽象 + Tesseract 默认实现 + 云 OCR 预留

优点：

- 与当前导入平台的可扩展方向一致
- 后续接云 OCR 不需要重写主流程
- 解析器只关心“是否拿到文本”，职责更清晰

缺点：

- 比写死实现多一层抽象
- 需要补少量工厂与配置

### 方案 C：把 OCR 做成 ImportJob 层单独子流程

优点：

- 状态机表达最细，可以单独记录 OCR 阶段

缺点：

- 会把当前任务模型显著拉复杂
- 一期投入偏大，不适合先补齐能力缺口

### 结论

采用**方案 B**：

- OCR 以独立 provider 模块存在
- `pdf.py` 通过统一接口调用 OCR
- 导入任务平台保持现状，只消费 OCR 成功或失败结果

## 4. 架构设计

### 4.1 模块划分

新增目录：

- `app/rag/ocr/base.py`
- `app/rag/ocr/factory.py`
- `app/rag/ocr/tesseract.py`

职责如下：

#### `app/rag/ocr/base.py`

定义统一 OCR 抽象：

- `OcrProvider` 接口
- `OcrResult` 数据结构
- OCR 专用异常

这样 PDF 解析器只依赖抽象，不依赖具体厂商或本地命令。

#### `app/rag/ocr/tesseract.py`

实现本地 Tesseract provider，负责：

- 检测 `tesseract` 是否可执行
- 组装语言参数（`chi_sim+eng`）
- 调用 OCR 命令
- 处理超时与退出码
- 将底层错误翻译为系统可理解的业务错误

#### `app/rag/ocr/factory.py`

按配置返回 OCR provider。

本期规则：

- `RAG_OCR_ENABLED=False` 时返回禁用态错误
- `RAG_OCR_PROVIDER=tesseract` 时返回本地 provider
- `RAG_OCR_PROVIDER=cloud` 时抛出“当前 provider 未实现”的明确错误

#### `app/rag/document_parsers/pdf.py`

解析顺序调整为：

1. 先尝试 `pypdf` 文本层提取
2. 如果提取到文本，直接返回，`used_ocr=False`
3. 如果 PDF 可打开但无文本，调用 OCR provider
4. OCR 成功则返回文本，`used_ocr=True`
5. OCR 失败则抛出明确异常

#### `app/rag/import_jobs.py`

不单独新增 OCR 子任务状态。

原因：

- 当前任务平台已经支持失败原因、重试与重解析
- OCR 只是“PDF 解析的一条分支”，本期无需单独拆任务状态机

因此导入任务仍保持：

- `pending`
- `running`
- `success`
- `failed`

OCR 的细节通过 `error` 和 `parser_name/used_ocr` 体现。

### 4.2 数据流

扫描版 PDF 导入的数据流如下：

1. 用户上传 PDF 或通过 URL 导入 PDF。
2. 导入平台保存源文件或远程快照。
3. `pdf.py` 尝试文本层提取。
4. 如果无文本，转入 OCR provider。
5. OCR provider 产出文本。
6. 文本进入现有 `RAGService.ingest_text(...)`。
7. 完成分块、嵌入、向量存储与版本治理。

## 5. 错误语义

OCR 相关失败统一进入明确的业务错误语义。

### 5.1 OCR 未启用

场景：

- `RAG_OCR_ENABLED=False`

错误消息：

`该 PDF 需要 OCR 才能提取文本，但当前系统未启用 OCR`

### 5.2 OCR 依赖缺失

场景：

- 未安装 `tesseract`
- 缺少 `chi_sim` 或 `eng` 语言包

错误消息：

`OCR 依赖缺失：未检测到 tesseract 或 chi_sim/eng 语言包`

### 5.3 OCR 执行失败

场景：

- 调用命令失败
- PDF 转 OCR 输入失败
- 超时
- 命令返回非零退出码

错误消息：

`OCR 执行失败，请检查服务器 OCR 环境或稍后重试`

### 5.4 OCR 成功但无文本

场景：

- 图片质量过低
- 页面空白
- 识别结果为空

错误消息：

`OCR 已执行，但未提取到可用文本`

### 5.5 错误落点

同步上传接口中：

- 解析阶段失败仍可映射为 `400`

异步导入平台中：

- OCR 相关问题进入 `ImportJob.error`
- 任务状态置为 `failed`

这里不新增特殊状态，避免任务模型过早膨胀。

## 6. 配置策略

新增配置项：

- `RAG_OCR_ENABLED: bool = False`
- `RAG_OCR_PROVIDER: str = "tesseract"`
- `RAG_OCR_LANGUAGES: str = "chi_sim+eng"`
- `RAG_OCR_TIMEOUT_SECONDS: float = 60.0`
- `RAG_OCR_CLOUD_ENDPOINT: str = ""`
- `RAG_OCR_CLOUD_API_KEY: str = ""`

配置解释：

- `RAG_OCR_ENABLED`
  - 总开关，未开启时扫描版 PDF 不尝试 OCR
- `RAG_OCR_PROVIDER`
  - 当前支持 `tesseract`
  - 保留 `cloud` 取值位供后续扩展
- `RAG_OCR_LANGUAGES`
  - 默认中英双语：`chi_sim+eng`
- `RAG_OCR_TIMEOUT_SECONDS`
  - 单次 OCR 超时限制
- `RAG_OCR_CLOUD_ENDPOINT` / `RAG_OCR_CLOUD_API_KEY`
  - 本期仅作为配置预留，不在运行路径中真正使用

## 7. 测试策略

### 7.1 PDF 解析器测试

覆盖：

1. 带文本层 PDF：直接成功，不走 OCR
2. 扫描版 PDF：触发 OCR provider
3. OCR 成功：返回文本，`used_ocr=True`
4. OCR 未启用：返回明确错误
5. OCR 依赖缺失：返回明确错误
6. OCR 执行失败：返回明确错误
7. OCR 结果为空：返回明确错误

### 7.2 OCR provider 测试

覆盖：

1. `tesseract` 不存在
2. 语言包缺失
3. 命令超时
4. 正常识别返回文本

这层测试通过 monkeypatch 子进程调用完成，不要求测试机安装真实 Tesseract。

### 7.3 导入任务回归测试

覆盖：

1. 扫描版 PDF 通过 OCR 成功入库
2. OCR 失败时任务标记 `failed`
3. 修复依赖后，失败任务可重试成功
4. 重解析会重新走 OCR 链路

### 7.4 配置行为测试

覆盖：

1. `RAG_OCR_ENABLED=False`
2. `RAG_OCR_PROVIDER=tesseract`
3. `RAG_OCR_PROVIDER=cloud` 时返回“当前未实现”的明确错误

## 8. 落地顺序

按以下顺序实施：

1. 先写 OCR 失败与成功路径测试
2. 新增 `app/rag/ocr/` 抽象与本地 Tesseract provider
3. 改造 `pdf.py`，将“需要 OCR”改为“尝试 OCR”
4. 接入配置项和 provider factory
5. 跑解析层与导入任务回归测试
6. 更新 README / 部署说明，写清本地 Tesseract 与语言包安装要求

## 9. 风险与缓解

### 风险 1：服务器未安装 OCR 环境

缓解：

- 明确报错
- 在 README 中补安装说明
- 将能力默认关闭，避免未配置环境直接误触发

### 风险 2：OCR 识别率不稳定

缓解：

- 本期仅承诺“能提取文本”，不承诺版面结构恢复
- 通过重试与重解析支持后续替换 OCR provider

### 风险 3：扫描件较大导致执行时间过长

缓解：

- 配置超时
- 先复用异步任务平台，不在同步请求里扩展长耗时链路

## 10. 完成定义

满足以下条件即视为本期 OCR 子项目完成：

1. 扫描版 PDF 在 OCR 开启且环境完备时可成功导入。
2. 带文本层 PDF 仍沿用原文本提取路径，不误走 OCR。
3. OCR 未启用、依赖缺失、执行失败、结果为空时，错误语义清晰。
4. 导入任务平台可承接 OCR 的失败、重试与重解析。
5. 相关自动化测试通过。
6. README 中补充 OCR 依赖安装与配置说明。
