# RAG Upload Parser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 RAG 文档上传接口增加多格式解析能力，支持常见企业文档类型，并将“解析成功但后续 RAG 摄取失败”统一按系统错误处理。

**Architecture:** 在 `app/rag/document_parsers/` 下新增解析服务层、注册表和解析器接口，上传路由只负责接收文件、调用解析服务和调用 `RAGService.ingest_text(...)`。解析层按扩展名和内容类型分发到不同解析器，并用统一异常类型表达不支持、解析失败、无文本和 OCR 需求。

**Tech Stack:** Python 3.11, FastAPI, pytest, python-docx, openpyxl, python-pptx, pypdf

---

### Task 1: 为上传新语义补失败测试

**Files:**
- Modify: `tests/test_rag.py`
- Test: `tests/test_rag.py`

- [ ] **Step 1: 写失败测试，描述上传链路的新行为**

```python
def test_upload_accepts_supported_text_types(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.rag.document_parsers.base import ParsedDocument

    def fake_parse(file_bytes: bytes, filename: str, content_type: str | None) -> ParsedDocument:
        assert filename == "doc.json"
        return ParsedDocument(
            text='{"name": "alice"}',
            title="doc",
            source="doc.json",
            extension="json",
            content_type=content_type,
            metadata={},
        )

    monkeypatch.setattr("app.api.routes.rag.parse_uploaded_document", fake_parse)
    resp = client.post(
        "/api/rag/documents/upload",
        files={"file": ("doc.json", io.BytesIO(b'{\"name\": \"alice\"}'), "application/json")},
    )
    assert resp.status_code == 200
```

```python
def test_upload_returns_ocr_required_for_pdf(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.rag.document_parsers.base import DocumentOcrRequiredError

    def fake_parse(file_bytes: bytes, filename: str, content_type: str | None) -> None:
        raise DocumentOcrRequiredError("该 PDF 需要 OCR 才能提取文本，当前系统未启用 OCR")

    monkeypatch.setattr("app.api.routes.rag.parse_uploaded_document", fake_parse)
    resp = client.post(
        "/api/rag/documents/upload",
        files={"file": ("scan.pdf", io.BytesIO(b"%PDF"), "application/pdf")},
    )
    assert resp.status_code == 400
```

```python
def test_upload_returns_500_when_ingest_fails(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.rag.document_parsers.base import ParsedDocument

    def fake_parse(file_bytes: bytes, filename: str, content_type: str | None) -> ParsedDocument:
        return ParsedDocument(
            text="解析后的文本",
            title="doc",
            source="doc.txt",
            extension="txt",
            content_type=content_type,
            metadata={},
        )

    async def fake_ingest_text(self, text: str, title: str, source: str | None, user_id: str, *, backend: str | None = None):
        raise RuntimeError("embedding service down")

    monkeypatch.setattr("app.api.routes.rag.parse_uploaded_document", fake_parse)
    monkeypatch.setattr("app.api.routes.rag.RAGService.ingest_text", fake_ingest_text)
    resp = client.post(
        "/api/rag/documents/upload",
        files={"file": ("doc.txt", io.BytesIO(b\"hello\"), "text/plain")},
    )
    assert resp.status_code == 500
    assert resp.json()["detail"] == "文档已解析，但知识库摄取失败，请稍后重试或联系管理员"
```

- [ ] **Step 2: 运行新测试，确认因缺少解析层而失败**

Run: `pytest tests/test_rag.py -k "upload_" -v`
Expected: FAIL，提示 `parse_uploaded_document` 或相关解析类型尚不存在

- [ ] **Step 3: 根据失败信息补全最小实现边界**

```python
# app/api/routes/rag.py
from app.rag.document_parsers import parse_uploaded_document
from app.rag.document_parsers.base import (
    DocumentOcrRequiredError,
    DocumentParseError,
    DocumentTextEmptyError,
    UnsupportedDocumentTypeError,
)
```

- [ ] **Step 4: 再次运行上传相关测试**

Run: `pytest tests/test_rag.py -k "upload_" -v`
Expected: 仍有失败，但失败点已从“符号不存在”收缩到“实现未完成”

### Task 2: 建立解析层协议、注册表和基础单测

**Files:**
- Create: `app/rag/document_parsers/__init__.py`
- Create: `app/rag/document_parsers/base.py`
- Create: `app/rag/document_parsers/registry.py`
- Create: `app/rag/document_parsers/service.py`
- Create: `tests/test_document_parsers.py`
- Test: `tests/test_document_parsers.py`

- [ ] **Step 1: 写解析层单测，先覆盖注册表与统一错误**

```python
def test_registry_rejects_unsupported_extension() -> None:
    from app.rag.document_parsers.service import parse_uploaded_document
    from app.rag.document_parsers.base import UnsupportedDocumentTypeError

    with pytest.raises(UnsupportedDocumentTypeError):
        parse_uploaded_document(b"binary", "data.bin", "application/octet-stream")
```

```python
def test_service_rejects_empty_text(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.rag.document_parsers.base import DocumentParser, ParsedDocument, DocumentTextEmptyError
    from app.rag.document_parsers.registry import DocumentParserRegistry
    from app.rag.document_parsers.service import DocumentParserService

    class EmptyParser(DocumentParser):
        def supports(self, filename: str, content_type: str | None) -> bool:
            return filename.endswith(".txt")

        def extract(self, file_bytes: bytes, filename: str, content_type: str | None) -> ParsedDocument:
            return ParsedDocument(text="   ", title="a", source=filename, extension="txt", content_type=content_type, metadata={})

    service = DocumentParserService(DocumentParserRegistry([EmptyParser()]))
    with pytest.raises(DocumentTextEmptyError):
        service.parse_upload(b"abc", "a.txt", "text/plain")
```

- [ ] **Step 2: 运行单测确认失败**

Run: `pytest tests/test_document_parsers.py -v`
Expected: FAIL，提示解析层文件尚未创建

- [ ] **Step 3: 写最小实现让测试通过**

```python
# app/rag/document_parsers/base.py
@dataclass(slots=True)
class ParsedDocument:
    text: str
    title: str
    source: str
    extension: str
    content_type: str | None
    metadata: dict[str, str | int | bool | None] = field(default_factory=dict)
```

```python
# app/rag/document_parsers/registry.py
class DocumentParserRegistry:
    def __init__(self, parsers: Sequence[DocumentParser] | None = None) -> None:
        self._parsers = list(parsers or [])

    def resolve(self, filename: str, content_type: str | None) -> DocumentParser:
        for parser in self._parsers:
            if parser.supports(filename, content_type):
                return parser
        raise UnsupportedDocumentTypeError(...)
```

```python
# app/rag/document_parsers/service.py
class DocumentParserService:
    def parse_upload(self, file_bytes: bytes, filename: str, content_type: str | None) -> ParsedDocument:
        parser = self._registry.resolve(filename, content_type)
        parsed = parser.extract(file_bytes, filename, content_type)
        if not parsed.text.strip():
            raise DocumentTextEmptyError("文件中未提取到可用文本")
        return parsed
```

- [ ] **Step 4: 运行单测确认通过**

Run: `pytest tests/test_document_parsers.py -v`
Expected: PASS

### Task 3: 实现文本类解析器

**Files:**
- Create: `app/rag/document_parsers/text.py`
- Modify: `app/rag/document_parsers/__init__.py`
- Modify: `app/rag/document_parsers/service.py`
- Modify: `tests/test_document_parsers.py`
- Test: `tests/test_document_parsers.py`

- [ ] **Step 1: 增加文本类解析测试**

```python
def test_parse_json_file() -> None:
    from app.rag.document_parsers.service import parse_uploaded_document

    parsed = parse_uploaded_document('{"k": "v"}'.encode("utf-8"), "doc.json", "application/json")
    assert parsed.extension == "json"
    assert '"k": "v"' in parsed.text
```

```python
def test_parse_csv_with_gbk_fallback() -> None:
    from app.rag.document_parsers.service import parse_uploaded_document

    parsed = parse_uploaded_document("姓名,部门".encode("gb18030"), "staff.csv", "text/csv")
    assert "姓名" in parsed.text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_document_parsers.py -k "json or csv" -v`
Expected: FAIL

- [ ] **Step 3: 写最小文本解析实现**

```python
TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "utf-16", "gb18030")

class TextDocumentParser(DocumentParser):
    _extensions = {"txt", "md", "json", "xml", "csv"}

    def supports(self, filename: str, content_type: str | None) -> bool:
        return _extension(filename) in self._extensions

    def extract(self, file_bytes: bytes, filename: str, content_type: str | None) -> ParsedDocument:
        text = _decode_text(file_bytes)
        return ParsedDocument(
            text=text,
            title=_title_from_filename(filename),
            source=filename,
            extension=_extension(filename),
            content_type=content_type,
            metadata={},
        )
```

- [ ] **Step 4: 运行文本类测试**

Run: `pytest tests/test_document_parsers.py -k "json or csv" -v`
Expected: PASS

### Task 4: 实现 Office 与 PDF 解析器

**Files:**
- Create: `app/rag/document_parsers/office.py`
- Create: `app/rag/document_parsers/pdf.py`
- Modify: `app/rag/document_parsers/__init__.py`
- Modify: `tests/test_document_parsers.py`
- Modify: `pyproject.toml`
- Test: `tests/test_document_parsers.py`

- [ ] **Step 1: 先写依赖边界和解析单测**

```python
def test_docx_parser_extracts_text(tmp_path: Path) -> None:
    from docx import Document
    from app.rag.document_parsers.service import parse_uploaded_document

    path = tmp_path / "sample.docx"
    doc = Document()
    doc.add_paragraph("企业知识库文档")
    doc.save(path)

    parsed = parse_uploaded_document(path.read_bytes(), path.name, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert "企业知识库文档" in parsed.text
```

```python
def test_pdf_parser_reports_ocr_required(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.rag.document_parsers.pdf import PdfDocumentParser
    from app.rag.document_parsers.base import DocumentOcrRequiredError

    class FakePage:
        def extract_text(self) -> str:
            return ""

    class FakeReader:
        is_encrypted = False
        pages = [FakePage()]

    monkeypatch.setattr("app.rag.document_parsers.pdf.PdfReader", lambda stream: FakeReader())
    with pytest.raises(DocumentOcrRequiredError):
        PdfDocumentParser().extract(b"%PDF", "scan.pdf", "application/pdf")
```

- [ ] **Step 2: 运行这些测试确认失败**

Run: `pytest tests/test_document_parsers.py -k "docx or pdf" -v`
Expected: FAIL

- [ ] **Step 3: 实现 Office / PDF 解析器，并更新依赖**

```python
# pyproject.toml
dependencies = [
    ...
    "python-docx>=1.1,<2.0",
    "openpyxl>=3.1,<4.0",
    "python-pptx>=1.0,<2.0",
    "pypdf>=5.0,<6.0",
]
```

```python
class DocxDocumentParser(DocumentParser):
    def extract(self, file_bytes: bytes, filename: str, content_type: str | None) -> ParsedDocument:
        document = Document(io.BytesIO(file_bytes))
        text = "\n".join(p.text for p in document.paragraphs if p.text.strip())
        ...
```

```python
class PdfDocumentParser(DocumentParser):
    def extract(self, file_bytes: bytes, filename: str, content_type: str | None) -> ParsedDocument:
        reader = PdfReader(io.BytesIO(file_bytes))
        if reader.is_encrypted:
            raise DocumentParseError("文件解析失败，请确认文件未损坏或未加密")
        texts = [page.extract_text() or "" for page in reader.pages]
        if texts and not "".join(texts).strip():
            raise DocumentOcrRequiredError("该 PDF 需要 OCR 才能提取文本，当前系统未启用 OCR")
        ...
```

- [ ] **Step 4: 运行 Office / PDF 测试**

Run: `pytest tests/test_document_parsers.py -k "docx or pdf" -v`
Expected: PASS

### Task 5: 改造上传路由并补回归测试

**Files:**
- Modify: `app/api/routes/rag.py`
- Modify: `tests/test_rag.py`
- Modify: `frontend/src/api/rag.ts`
- Modify: `README.md`
- Test: `tests/test_rag.py`

- [ ] **Step 1: 改路由前先写回归测试**

```python
def test_upload_uses_parser_metadata_in_audit(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.rag.document_parsers.base import ParsedDocument

    def fake_parse(file_bytes: bytes, filename: str, content_type: str | None) -> ParsedDocument:
        return ParsedDocument(
            text="content",
            title="slides",
            source="slides.pptx",
            extension="pptx",
            content_type=content_type,
            metadata={"parser_name": "pptx"},
        )

    monkeypatch.setattr("app.api.routes.rag.parse_uploaded_document", fake_parse)
    resp = client.post(
        "/api/rag/documents/upload",
        files={"file": ("slides.pptx", io.BytesIO(b"pptx"), "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
    )
    assert resp.status_code == 200
```

- [ ] **Step 2: 运行回归测试确认失败或语义未达标**

Run: `pytest tests/test_rag.py -k "upload" -v`
Expected: FAIL 或部分用例失败

- [ ] **Step 3: 写最小路由改造**

```python
raw = await file.read()
try:
    parsed = parse_uploaded_document(raw, file.filename or "未命名文档", file.content_type)
except (
    UnsupportedDocumentTypeError,
    DocumentParseError,
    DocumentTextEmptyError,
    DocumentOcrRequiredError,
) as exc:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

try:
    doc = await rag.ingest_text(parsed.text, parsed.title, parsed.source, current_user.id)
except Exception:
    logger.exception("上传文档解析成功，但知识库摄取失败: filename=%s", file.filename)
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="文档已解析，但知识库摄取失败，请稍后重试或联系管理员",
    )
```

- [ ] **Step 4: 更新前端和 README 文案**

```ts
/** 上传知识文档，后端按文件类型自动解析文本。 */
```

```md
| 知识库 | `POST` | `/api/rag/documents/upload` | 上传 txt/md/json/xml/csv/docx/xlsx/pptx/pdf 文件 |
```

- [ ] **Step 5: 运行上传接口相关测试**

Run: `pytest tests/test_rag.py -k "upload" -v`
Expected: PASS

### Task 6: 全量回归并整理输出

**Files:**
- Modify: `tests/test_document_parsers.py`
- Modify: `tests/test_rag.py`

- [ ] **Step 1: 运行解析层和 RAG 相关测试**

Run: `pytest tests/test_document_parsers.py tests/test_rag.py -v`
Expected: PASS

- [ ] **Step 2: 运行静态检查**

Run: `ruff check app tests`
Expected: PASS

- [ ] **Step 3: 人工检查文档与异常消息**

```text
核对 README、前端注释、接口错误消息是否与实现一致。
```

- [ ] **Step 4: 记录未覆盖风险**

```text
说明扫描版 PDF 的 OCR 仍未启用，doc/xls/ppt 仍未支持。
```
