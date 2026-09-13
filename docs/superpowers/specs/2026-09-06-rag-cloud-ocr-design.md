# RAG 云 OCR（OpenAI 兼容视觉）设计

## 1. 背景

当前 RAG OCR 一期已经具备：

- OCR provider 抽象
- 本地 Tesseract provider
- 扫描版 PDF 在无文本层时自动转入 OCR
- OCR 错误语义、失败治理、重试与重解析

但本地 Tesseract 方案仍有明显边界：

- 依赖服务器安装 `tesseract`
- 语言包与运行环境需要额外维护
- 识别质量受本地环境与图片质量影响较大

为提高部署灵活性与识别效果，需要在现有 OCR 抽象之下增加**云 OCR provider**。本期云 OCR 选型为 **OpenAI 兼容视觉接口**，并继续保持“PDF 先按页转图片，再逐页识别”的策略。

## 2. 目标与非目标

### 2.1 目标

本期需要完成：

1. 在现有 OCR provider 抽象下新增 `cloud` provider。
2. `cloud` provider 使用 OpenAI 兼容视觉接口逐页识别 PDF 图片。
3. OCR 配置支持两套来源：
   - OCR 专用配置优先
   - 未配置时回退到现有 LLM OpenAI 兼容配置
4. 保持 `pdf.py` 只依赖 OCR 抽象，不依赖具体 provider。
5. 保持导入任务平台、失败治理、重试与重解析逻辑不变。
6. 给出清晰的配置缺失、HTTP 失败、空结果等错误语义。

### 2.2 非目标

本期明确不做：

1. 非 OpenAI 兼容协议的云 OCR 服务商接入。
2. 整份 PDF 直传云 OCR。
3. 多模态复杂版面分析、表格结构恢复、坐标输出。
4. 按租户动态切换不同云 OCR 服务商。
5. 图片上传 OCR、网页截图 OCR、Office 内嵌图片 OCR。

## 3. 方案对比

### 方案 A：复用现有 LLM 配置直接调用视觉模型

优点：

- 配置最少
- 接入最快

缺点：

- OCR 和普通 LLM 共用配置，隔离性不足
- 后续切换 OCR 专用模型时不够灵活

### 方案 B：只使用 OCR 专用配置

优点：

- 配置边界最清晰
- 运维和模型选择更独立

缺点：

- 初次接入成本更高
- 已有 OpenAI 兼容 LLM 配置时仍需重复填写

### 方案 C：OCR 专用配置优先，未配置时回退到现有 LLM 配置

优点：

- 兼顾灵活性和接入成本
- 与当前项目“OpenAI 兼容接口可复用”的技术路线一致
- 后续若 OCR 与普通 LLM 分离，不需要修改调用方

缺点：

- 配置解析逻辑比单一路径稍复杂

### 结论

采用**方案 C**：

- 优先使用 `RAG_OCR_*` 专用配置
- 如果未配置，则回退到现有 `LLM_*` OpenAI 兼容配置

## 4. 架构设计

### 4.1 模块划分

新增文件：

- `app/rag/ocr/openai_vision.py`

修改文件：

- `app/rag/ocr/factory.py`
- `app/core/config.py`
- `app/rag/document_parsers/pdf.py`（仅保持注入抽象，不感知实现细节）

#### `app/rag/ocr/openai_vision.py`

职责：

1. 接收 PDF 渲染后的页面图片字节。
2. 将图片编码为可发送给 OpenAI 兼容视觉接口的请求体。
3. 逐页调用视觉模型提取文本。
4. 聚合页面结果并返回统一 `OcrResult`。
5. 将 HTTP / 配置 / 空结果等错误翻译为统一 OCR 错误。

#### `app/rag/ocr/factory.py`

新增 `cloud` 分支：

- `RAG_OCR_PROVIDER=tesseract` -> 本地 provider
- `RAG_OCR_PROVIDER=cloud` -> OpenAI 兼容视觉 provider

#### `app/core/config.py`

新增云 OCR 专用配置项：

- `RAG_OCR_BASE_URL`
- `RAG_OCR_API_KEY`
- `RAG_OCR_MODEL`

解析策略：

1. 若以上三项完整存在，则使用 OCR 专用配置
2. 否则回退到：
   - `LLM_BASE_URL`
   - `LLM_API_KEY`
   - `LLM_DEFAULT_MODEL`

### 4.2 数据流

扫描版 PDF 走云 OCR 的数据流如下：

1. `pdf.py` 尝试提取文本层。
2. 如果无文本层，则调用 OCR provider。
3. OCR factory 根据配置返回 `OpenAiVisionOcrProvider`。
4. provider 将 PDF 渲染为图片。
5. provider 逐页调用 OpenAI 兼容视觉接口。
6. 聚合识别结果，返回 `OcrResult(text=..., provider="cloud_openai_vision")`
7. `pdf.py` 将结果包装为 `ParsedDocument(metadata={"used_ocr": True, ...})`
8. 后续进入现有 RAG 摄取与任务平台。

## 5. 配置策略

### 5.1 新增配置项

- `RAG_OCR_BASE_URL: str = ""`
- `RAG_OCR_API_KEY: str = ""`
- `RAG_OCR_MODEL: str = ""`

### 5.2 配置解析规则

按以下优先级解析：

1. 若 `RAG_OCR_BASE_URL`、`RAG_OCR_API_KEY`、`RAG_OCR_MODEL` 均非空，使用 OCR 专用配置。
2. 否则回退到：
   - `LLM_BASE_URL`
   - `LLM_API_KEY`
   - `LLM_DEFAULT_MODEL`
3. 若回退后仍缺配置，则报“配置缺失”错误。

### 5.3 说明

这样可以同时满足：

- 直接复用已有 OpenAI 兼容 LLM 配置
- 后续为 OCR 指定独立模型与服务地址

## 6. 请求策略

### 6.1 逐页转图片

本期固定采用：

- PDF -> 按页渲染为图片
- 图片 -> 逐页发送到视觉模型

原因：

1. 与本地 OCR 的 PDF 渲染过程一致，可复用现有渲染逻辑。
2. 兼容性优于“整份 PDF 直传”。
3. 逐页失败时更容易定位问题。

### 6.2 OpenAI 兼容请求形式

每页图片单独构造一次聊天/视觉请求，消息中包含：

- 识别指令提示词
- base64 图片

返回内容按纯文本提取，不做结构化版面恢复。

## 7. 错误语义

### 7.1 OCR 配置缺失

场景：

- OCR 专用配置不完整
- 且现有 LLM 配置也不足以回退

错误消息：

`OCR 配置缺失，请检查 RAG_OCR_BASE_URL / RAG_OCR_API_KEY / RAG_OCR_MODEL 或现有 LLM 配置`

### 7.2 云 OCR 调用失败

场景：

- HTTP 请求失败
- 鉴权失败
- 上游返回非 2xx
- 解析响应失败

错误消息：

`云 OCR 调用失败，请检查网络、鉴权或服务配置`

### 7.3 OCR 成功但无文本

场景：

- 模型返回空文本
- 所有页面聚合结果为空

错误消息：

`OCR 已执行，但未提取到可用文本`

### 7.4 错误落点

与现有 OCR 方案一致：

- 同步上传接口：解析失败可映射为 `400`
- 异步导入任务：进入 `ImportJob.error`，状态置为 `failed`

不新增新的任务状态。

## 8. 测试策略

### 8.1 云 provider 单测

覆盖：

1. OCR 专用配置优先
2. OCR 专用配置缺失时回退到 LLM 配置
3. 回退后仍缺配置时报错
4. HTTP 调用失败时报错
5. 成功返回识别文本
6. 返回空文本时报错

### 8.2 factory 测试

覆盖：

1. `RAG_OCR_PROVIDER=cloud` 时返回云 provider
2. `RAG_OCR_PROVIDER=tesseract` 时不受影响

### 8.3 PDF 解析器测试

覆盖：

1. 无文本层 PDF 注入云 provider 后可成功返回 OCR 文本
2. 云 provider 返回配置缺失或 HTTP 错误时，错误语义正确

### 8.4 导入任务回归

覆盖：

1. 扫描版 PDF 在 `cloud` provider 下可成功入库
2. 云 OCR 失败时任务正确置为 `failed`
3. 重试 / 重解析仍复用现有任务平台

## 9. 落地顺序

1. 先写云 OCR provider 失败与成功测试
2. 新增 `openai_vision.py`
3. 修改 `factory.py` 和配置项
4. 跑 provider 与 parser 测试
5. 补导入任务回归
6. 更新 README 配置说明

## 10. 风险与缓解

### 风险 1：上游视觉模型协议差异

缓解：

- 本期仅支持 OpenAI 兼容接口
- 请求体严格按兼容格式构造
- 单测覆盖响应解析与 HTTP 失败分支

### 风险 2：逐页识别成本偏高

缓解：

- 本期先保证正确性
- 后续如需优化，可增加页数限制、并发或模型切换策略

### 风险 3：配置来源混淆

缓解：

- 明确专用配置优先，LLM 配置回退
- README 中写清优先级规则

## 11. 完成定义

满足以下条件即视为云 OCR 子项目完成：

1. `RAG_OCR_PROVIDER=cloud` 时可以调用 OpenAI 兼容视觉接口。
2. OCR 专用配置优先、LLM 配置回退逻辑可用。
3. 扫描版 PDF 可通过云 OCR 成功导入。
4. 配置缺失、HTTP 失败、空结果等错误语义清晰。
5. 相关自动化测试通过。
6. README 中补充云 OCR 配置与优先级说明。
