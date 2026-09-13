# RAG 多格式文档上传解析设计

## 背景

当前 `POST /api/rag/documents/upload` 仅支持 `.txt` 与 `.md`，并直接按 UTF-8 文本解码后交给 `RAGService.ingest_text(...)`。

这套实现对企业场景不够友好：

- 用户并不知道 `docx`、`xlsx`、`pptx`、`pdf` 是否属于 UTF-8；
- 文件类型解析逻辑混在路由层，难以扩展；
- 后续若接入 OCR、老 Office 格式或按租户配置能力，现有结构不便演进。

本设计目标是把“上传文件转为可摄取纯文本”独立为解析服务层，在不改变现有 RAG 入库主流程职责的前提下，支持更多常见企业文档格式，并为插件化扩展预留接口。

## 目标

- 支持上传并解析以下文件类型：
  - `txt`
  - `md`
  - `json`
  - `xml`
  - `csv`
  - `docx`
  - `xlsx`
  - `pptx`
  - `pdf`
- 上传接口不再要求用户理解 UTF-8 概念；系统按文件类型自动解析内容。
- 路由层保持精简，只负责接收文件、调用解析服务、调用 RAG 摄取与写审计日志。
- 解析层采用“服务层 + 注册表”的方式组织，支持后续新增解析器或 OCR 能力。
- 为 PDF OCR 预留扩展口，但本次不启用 OCR。

## 非目标

- 本次不支持扫描版 PDF 的 OCR 识别。
- 本次不支持老 Office 格式：`doc`、`xls`、`ppt`。
- 本次不处理压缩包、附件递归解包或多文件批量上传。
- 本次不改造 `POST /api/rag/documents/ingest` 的输入语义。

## 方案概览

采用“解析服务层为主，插件化注册表为辅”的设计：

1. 路由层接收上传文件并读出原始字节。
2. 文档解析服务根据文件名和 `content_type` 在注册表中选择解析器。
3. 解析器提取文本并返回统一的解析结果对象。
4. 路由层将解析结果传入 `RAGService.ingest_text(...)`。
5. 上传成功后沿用现有审计日志链路。

这让文件解析与 RAG 摄取解耦：

- 文件格式差异留在解析层处理；
- `RAGService` 继续只关心文本分块、嵌入、落库；
- 后续新增 OCR 或更多格式时，无需不断膨胀 `rag.py`。

## 模块设计

建议新增目录 `app/rag/document_parsers/`，内部拆分如下：

### `base.py`

定义统一协议与公共模型：

- `ParsedDocument`
  - `text: str`
  - `title: str`
  - `source: str`
  - `extension: str`
  - `content_type: str | None`
  - 可选扩展字段：`metadata: dict[str, str | int | bool | None]`
- `DocumentParser`
  - `supports(filename: str, content_type: str | None) -> bool`
  - `extract(file_bytes: bytes, filename: str, content_type: str | None) -> ParsedDocument`

同时定义解析层异常：

- `UnsupportedDocumentTypeError`
- `DocumentParseError`
- `DocumentTextEmptyError`
- `DocumentOcrRequiredError`

### `registry.py`

负责解析器注册与匹配顺序管理：

- 按顺序保存已注册解析器；
- 暴露 `register(parser)`；
- 暴露 `resolve(filename, content_type)`；
- 当没有解析器命中时抛出 `UnsupportedDocumentTypeError`。

### `service.py`

提供统一入口，例如 `DocumentParserService.parse_upload(...)`：

- 接收文件字节、文件名、`content_type`；
- 调用注册表选择解析器；
- 获取 `ParsedDocument`；
- 归一化标题和来源；
- 统一做空文本校验。

### 具体解析器

建议按文件类型拆分为独立模块：

- `text.py`
- `json_xml.py`
- `csv.py`
- `docx.py`
- `xlsx.py`
- `pptx.py`
- `pdf.py`

后续如需支持 `doc/xls/ppt`、OCR 或租户级能力控制，可继续新增模块并注册。

## 路由层改造

`app/api/routes/rag.py` 的 `upload_document(...)` 调整为：

1. 读取上传文件的原始字节；
2. 调用 `DocumentParserService.parse_upload(...)` 提取文本；
3. 使用解析结果中的 `text/title/source` 调用 `RAGService.ingest_text(...)`；
4. 写入审计日志；
5. 返回 `DocumentOut`。

路由层不再：

- 以扩展名白名单直接限制为 `.txt/.md`；
- 直接执行 `raw.decode("utf-8")`；
- 承担不同文件格式的解析职责。

## 支持格式与解析策略

### 文本类

- `txt`
- `md`
- `json`
- `xml`
- `csv`

策略：

- 优先按 UTF-8 解码；
- 对 `json/xml/csv` 不要求用户声明编码；
- 若无法用预设文本解码策略解析，则判定为文件解析失败，而不是提示用户“不是 UTF-8 文本”。

说明：

- 本次实现可以先采用有限编码尝试策略，以减少误报；
- 具体编码兜底范围由实现阶段结合依赖与测试结果确定，但对外错误语义统一为“解析失败”。

### Office 类

- `docx`
- `xlsx`
- `pptx`

策略：

- 使用对应 Python 解析库直接提取文本；
- `xlsx` 需要汇总多个 sheet 的文本单元格；
- `pptx` 需要汇总各页幻灯片中的文本元素。

### PDF

策略：

- 先支持可直接提取文本的 PDF；
- 若 PDF 能打开但未提取到文本，则区分为：
  - 普通空内容：`DocumentTextEmptyError`
  - 推断为扫描件、需要 OCR：`DocumentOcrRequiredError`

OCR 预留：

- 在 `pdf.py` 中预留 OCR 分支入口；
- 本次默认不开启 OCR；
- 后续接入 OCR 时只需扩展 `PdfParser` 或注册新的 PDF 解析器实现。

## 错误语义

### 返回 400 的场景

以下属于客户端提交的文件不满足系统可处理条件，应返回 `400 Bad Request`：

- 不支持的文件类型；
- 文件可识别，但内容损坏、加密或无法解析；
- 文件解析成功，但没有可摄取文本；
- PDF 需要 OCR 才能提取文本，但当前系统未启用 OCR。

建议错误消息：

- `当前仅支持 txt、md、json、xml、csv、docx、xlsx、pptx、pdf 文件`
- `文件解析失败，请确认文件未损坏或未加密`
- `文件中未提取到可用文本`
- `该 PDF 需要 OCR 才能提取文本，当前系统未启用 OCR`

### 返回 500 的场景

若文件已经成功解析出文本，但后续 `RAGService.ingest_text(...)` 失败，应返回 `500 Internal Server Error`，因为这属于系统内部处理失败，不再归类为客户端请求错误。

适用场景包括但不限于：

- 文本切分后未生成有效 chunk；
- 嵌入流程失败；
- 落库或后续服务状态异常。

建议处理方式：

- 记录 `logger.exception(...)`；
- 返回稳定错误消息：`文档已解析，但知识库摄取失败，请稍后重试或联系管理员`
- 不直接把内部异常文本暴露给客户端。

### 与 `documents/ingest` 的边界

`POST /api/rag/documents/ingest` 仍保留当前语义：

- `text/title` 为空等输入问题仍返回 `400`；
- 本设计中的“解析成功后 RAG 摄取失败返回 500”的规则，仅适用于文件上传链路。

## 审计与元信息

解析服务返回 `ParsedDocument` 后，路由层继续沿用现有审计逻辑，并建议在 `details` 中增加可扩展信息：

- `filename`
- `source: "upload"`
- `extension`
- `content_type`
- 可选的 `parser_name`
- 后续可扩展的 `page_count`、`sheet_count`、`used_ocr`

本次不要求修改数据库模型，仅在审计与内存中的解析结果里保留这些信息。

## 依赖选择建议

建议优先选择生态成熟、系统依赖轻的 Python 库：

- `python-docx`：解析 `docx`
- `openpyxl`：解析 `xlsx`
- `python-pptx`：解析 `pptx`
- `pypdf`：解析 `pdf`

文本类文件优先使用标准库能力处理。

依赖原则：

- 不引入必须依赖本地 Office 或外部二进制转换器的方案；
- 优先可纯 Python 运行的库，降低部署复杂度；
- 对 OCR 保持接口预留，不在本次引入 OCR 引擎依赖。

## 测试策略

遵循测试先行，测试覆盖分三层。

### 解析层单元测试

新增针对解析服务和注册表的测试，覆盖：

- 各支持格式能正确匹配解析器；
- 不支持格式返回 `UnsupportedDocumentTypeError`；
- 空文本返回 `DocumentTextEmptyError`；
- PDF 无文本但需要 OCR 返回 `DocumentOcrRequiredError`；
- 文件损坏时返回 `DocumentParseError`。

### 路由层接口测试

扩展 `tests/test_rag.py`，覆盖：

- `txt/md/json/xml/csv/docx/xlsx/pptx/pdf` 上传成功；
- 不支持类型返回 `400`；
- 文件解析失败返回 `400`；
- PDF 需要 OCR 返回 `400`；
- 解析成功但 `RAGService.ingest_text(...)` 抛错时返回 `500`。

### 回归测试

确保以下既有行为不退化：

- 审计日志仍写入；
- 文档列表 / 详情 / 删除链路不受影响；
- `documents/ingest` 语义不变。

## 实施步骤

1. 新增解析层基础协议、注册表、服务入口；
2. 先写解析层与路由层失败测试；
3. 实现文本类解析器；
4. 实现 `docx/xlsx/pptx/pdf` 解析器；
5. 改造 `upload_document(...)` 接入解析服务；
6. 补充审计细节与文档说明；
7. 运行相关测试并回归。

## 风险与约束

- 不同文件类型的文本抽取质量存在天然差异，尤其是表格与幻灯片内容；
- PDF “是否需要 OCR”的判断可能需要启发式规则，初版可先以“可打开但提取为空”作为近似判断；
- 新增第三方依赖后，需要同步更新安装说明与可能的打包配置；
- 若用户上传极大文件，后续可能还需要补充文件大小与页数限制，但不在本次范围内。

## 结论

本次采用“解析服务层 + 注册表 + 解析器接口”的结构，在保持现有 RAG 入库能力不变的前提下，为多格式文件上传提供清晰的扩展边界。

这套方案能先满足企业文档的主流格式支持需求，同时为后续 OCR、老 Office 格式、租户级能力开关等能力保留演进空间。
