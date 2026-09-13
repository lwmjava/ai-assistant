# RAG 老 Office 格式（doc / xls / ppt）支持设计

## 1. 背景

当前文档解析层（`app/rag/document_parsers`）已支持现代 Office 格式：

- `docx`：`python-docx`
- `xlsx`：`openpyxl`
- `pptx`：`python-pptx`

但企业中仍大量存在老二进制格式 `doc / xls / ppt`，尚未接入。老格式使用微软 OLE 二进制容器，没有可靠、统一、维护良好的纯 Python 文本提取库；其中 `.xls` 有较成熟的纯 Python 读取库 `xlrd`，而 `.doc` / `.ppt` 需要借助外部转换器。

## 2. 目标与非目标

### 2.1 目标

1. 支持上传并摄取 `.doc`、`.xls`、`.ppt` 三类文件。
2. 采用**混合方案**：
   - `.xls` 用纯 Python 的 `xlrd` 直接提取，无额外系统依赖；
   - `.doc` / `.ppt` 用 headless LibreOffice（`soffice`）转换为现代格式，再复用现有解析器。
3. 保持解析层插件化：新增解析器只在注册表登记，路由 / 服务层不改。
4. 依赖缺失（LibreOffice 未安装）时优雅降级，任务失败并给出明确原因，不新增任务状态。
5. 与现有 OCR 的“外部依赖 + 优雅降级”语义保持一致。

### 2.2 非目标

1. 不做 `.doc/.xls/.ppt` 之外的旧格式。
2. 不引入 `unstructured` / `textract` 等重型三方解析框架（它们底层仍依赖 LibreOffice / antiword / catdoc）。
3. 不保证老格式中 OLE 嵌入对象、宏、复杂版式/表格结构被完整还原，仅提取可读文本。
4. 不把 `RAG_BACKEND=langchain/llamaindex` 用于文件解析；它们只负责分块 / 检索。

## 3. 方案说明

### 3.1 模块划分

新增文件：

- `app/rag/document_parsers/legacy_office.py`

修改文件：

- `app/rag/document_parsers/service.py`（注册新解析器）
- `app/rag/document_parsers/registry.py`（更新支持的格式提示）
- `pyproject.toml` / `requirements.txt`（新增 `xlrd`）
- `Dockerfile`（安装 LibreOffice writer / impress）
- `README.md`（格式支持与部署要求）

#### `legacy_office.py` 职责

1. `XlsDocumentParser`：`xlrd` 读取 `.xls`，按 sheet / 行聚合文本。
2. `DocDocumentParser`：将 `.doc` 转 `docx` 后复用 `DocxDocumentParser`。
3. `PptDocumentParser`：将 `.ppt` 转 `pptx` 后复用 `PptxDocumentParser`。
4. `_convert_with_libreoffice`：调用 `soffice --headless --convert-to ...` 完成格式转换。

### 3.2 转换流程（doc / ppt）

1. 将原始字节写入临时文件（保留原扩展名）。
2. 调用 `soffice --headless --convert-to docx|pptx --outdir <tmp> <input>`。
3. 读取转换产物字节。
4. 交回对应现代解析器提取文本，最终 `ParsedDocument` 仍标记原扩展名 / 元数据。

### 3.3 xls 提取流程

1. `xlrd.open_workbook(file_contents=...)` 读取 workbook。
2. 遍历 sheets，按 `# Sheet: <name>` 分段，逐行聚合非空单元格。
3. 输出格式与现有 `XlsxDocumentParser` 保持一致。

## 4. 错误语义

沿用解析层异常体系，不新增异常类型：

| 场景 | 异常 | 消息 |
|------|------|------|
| LibreOffice 未安装 | `DocumentParseError` | `老 Office 格式依赖缺失：未检测到 LibreOffice（soffice）` |
| 转换 / 解析失败（损坏、加密等） | `DocumentParseError` | `文件解析失败，请确认文件未损坏或未加密` |
| 解析成功但无文本 | `DocumentTextEmptyError` | `文件中未提取到可用文本`（服务层统一） |

错误落点与现有解析一致：

- 同步上传：解析阶段错误按现有路由语义处理；
- 异步导入：进入 `ImportJob.error`，状态置为 `failed`，可重试。

## 5. 测试策略

- `.xls` 解析器：mock `xlrd.open_workbook`，验证 sheet / 行聚合与 `parser_name="xls"`。
- `.doc` / `.ppt`：mock `_convert_with_libreoffice` 返回真实 `docx` / `pptx` 字节（用 `python-docx` / `python-pptx` 现场生成），验证复用现代解析器且 `parser_name` / `extension` 保持原格式。
- 转换器依赖缺失 / 子进程失败 → 验证错误语义。
- 注册表：验证 `.doc/.xls/.ppt` 能被默认服务正确解析，提示文案更新。
- 导入任务回归：老格式成功入库、失败任务正确置 `failed`。

## 6. 落地顺序

1. 补依赖（`xlrd`）并写设计 / 计划文档。
2. 先写失败测试。
3. 实现 `legacy_office.py` 并接入注册表 / 服务。
4. 更新 Dockerfile（LibreOffice）与 README。
5. 全量回归与 lint。

## 7. 完成定义

1. `.doc/.xls/.ppt` 可被上传并成功摄取为知识文档。
2. `.xls` 纯 Python 提取（不依赖 LibreOffice）。
3. `.doc/.ppt` 在 LibreOffice 可用时正常工作；缺失时任务失败且原因明确。
4. 相关测试通过，解析层注释齐全，README 说明同步。