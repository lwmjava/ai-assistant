# RAG 云 OCR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为扫描版 PDF 增加基于 OpenAI 兼容视觉接口的云 OCR provider，并支持 OCR 专用配置优先、LLM 配置回退的策略。

**Architecture:** 在现有 `app/rag/ocr/` 抽象下新增 `openai_vision.py`，由 `factory.py` 在 `RAG_OCR_PROVIDER=cloud` 时返回该 provider。`pdf.py` 仍只依赖统一的 `OcrProvider` 接口，导入任务平台继续复用现有失败治理、重试与重解析能力。

**Tech Stack:** Python 3.11, FastAPI, pytest, httpx, PyMuPDF, OpenAI-compatible vision API

---

### Task 1: 为云 OCR provider 先写失败测试

**Files:**
- Create: `tests/test_cloud_ocr.py`
- Test: `tests/test_cloud_ocr.py`

- [ ] **Step 1: 写云 OCR provider 的失败测试**

```python
def test_cloud_ocr_prefers_dedicated_config(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings
    from app.rag.ocr.openai_vision import OpenAiVisionOcrProvider

    monkeypatch.setattr(settings, "RAG_OCR_BASE_URL", "https://ocr.example.com/v1")
    monkeypatch.setattr(settings, "RAG_OCR_API_KEY", "ocr-key")
    monkeypatch.setattr(settings, "RAG_OCR_MODEL", "ocr-model")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://llm.example.com/v1")
    monkeypatch.setattr(settings, "LLM_API_KEY", "llm-key")
    monkeypatch.setattr(settings, "LLM_DEFAULT_MODEL", "llm-model")

    provider = OpenAiVisionOcrProvider.from_settings()
    assert provider.base_url == "https://ocr.example.com/v1"
    assert provider.api_key == "ocr-key"
    assert provider.model == "ocr-model"
```

```python
def test_cloud_ocr_falls_back_to_llm_config(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings
    from app.rag.ocr.openai_vision import OpenAiVisionOcrProvider

    monkeypatch.setattr(settings, "RAG_OCR_BASE_URL", "")
    monkeypatch.setattr(settings, "RAG_OCR_API_KEY", "")
    monkeypatch.setattr(settings, "RAG_OCR_MODEL", "")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://llm.example.com/v1")
    monkeypatch.setattr(settings, "LLM_API_KEY", "llm-key")
    monkeypatch.setattr(settings, "LLM_DEFAULT_MODEL", "llm-model")

    provider = OpenAiVisionOcrProvider.from_settings()
    assert provider.base_url == "https://llm.example.com/v1"
    assert provider.api_key == "llm-key"
    assert provider.model == "llm-model"
```

```python
def test_cloud_ocr_reports_missing_config(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings
    from app.rag.ocr.base import OcrProviderError
    from app.rag.ocr.openai_vision import OpenAiVisionOcrProvider

    monkeypatch.setattr(settings, "RAG_OCR_BASE_URL", "")
    monkeypatch.setattr(settings, "RAG_OCR_API_KEY", "")
    monkeypatch.setattr(settings, "RAG_OCR_MODEL", "")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_DEFAULT_MODEL", "")

    with pytest.raises(OcrProviderError, match="OCR 配置缺失"):
        OpenAiVisionOcrProvider.from_settings()
```

- [ ] **Step 2: 运行测试，确认先失败**

Run: `pytest tests/test_cloud_ocr.py -v`

Expected: FAIL，提示 `OpenAiVisionOcrProvider` 或 `from_settings()` 尚不存在。

- [ ] **Step 3: 写最小类定义，只让配置测试能跑起来**

```python
# app/rag/ocr/openai_vision.py
class OpenAiVisionOcrProvider(OcrProvider):
    def __init__(self, *, base_url: str, api_key: str, model: str, timeout_seconds: float) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_settings(cls) -> "OpenAiVisionOcrProvider":
        ...
```

- [ ] **Step 4: 再次运行测试，确认失败收缩**

Run: `pytest tests/test_cloud_ocr.py -v`

Expected: 仍可能失败，但失败原因已经从“模块不存在”收缩到“配置解析未完成”。

- [ ] **Step 5: Commit**

```bash
git add tests/test_cloud_ocr.py app/rag/ocr/openai_vision.py
git commit -m "test(rag): add failing cloud OCR config tests"
```

### Task 2: 实现云 OCR provider 的配置解析与工厂接线

**Files:**
- Modify: `app/core/config.py`
- Modify: `app/rag/ocr/factory.py`
- Create: `app/rag/ocr/openai_vision.py`
- Modify: `app/rag/ocr/__init__.py`
- Modify: `tests/test_ocr.py`
- Modify: `tests/test_cloud_ocr.py`
- Test: `tests/test_ocr.py`
- Test: `tests/test_cloud_ocr.py`

- [ ] **Step 1: 先写工厂测试**

```python
def test_get_ocr_provider_returns_cloud_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings
    from app.rag.ocr.factory import get_ocr_provider
    from app.rag.ocr.openai_vision import OpenAiVisionOcrProvider

    monkeypatch.setattr(settings, "RAG_OCR_ENABLED", True)
    monkeypatch.setattr(settings, "RAG_OCR_PROVIDER", "cloud")
    monkeypatch.setattr(settings, "RAG_OCR_BASE_URL", "https://ocr.example.com/v1")
    monkeypatch.setattr(settings, "RAG_OCR_API_KEY", "ocr-key")
    monkeypatch.setattr(settings, "RAG_OCR_MODEL", "ocr-model")

    provider = get_ocr_provider()
    assert isinstance(provider, OpenAiVisionOcrProvider)
```

- [ ] **Step 2: 运行工厂与配置测试，确认失败**

Run: `pytest tests/test_ocr.py tests/test_cloud_ocr.py -v`

Expected: FAIL，提示 `cloud` 分支或 `from_settings()` 还未实现。

- [ ] **Step 3: 在配置中增加云 OCR 专用字段**

```python
# app/core/config.py
RAG_OCR_BASE_URL: str = ""
RAG_OCR_API_KEY: str = ""
RAG_OCR_MODEL: str = ""
```

- [ ] **Step 4: 实现 `from_settings()` 与工厂 cloud 分支**

```python
@classmethod
def from_settings(cls) -> "OpenAiVisionOcrProvider":
    base_url = settings.RAG_OCR_BASE_URL or settings.LLM_BASE_URL
    api_key = settings.RAG_OCR_API_KEY or settings.LLM_API_KEY
    model = settings.RAG_OCR_MODEL or settings.LLM_DEFAULT_MODEL
    if not base_url or not api_key or not model:
        raise OcrProviderError(
            "OCR 配置缺失，请检查 RAG_OCR_BASE_URL / RAG_OCR_API_KEY / RAG_OCR_MODEL 或现有 LLM 配置"
        )
    return cls(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout_seconds=settings.RAG_OCR_TIMEOUT_SECONDS,
    )
```

```python
# app/rag/ocr/factory.py
if settings.RAG_OCR_PROVIDER == "cloud":
    return OpenAiVisionOcrProvider.from_settings()
```

- [ ] **Step 5: 运行工厂与配置测试**

Run: `pytest tests/test_ocr.py tests/test_cloud_ocr.py -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/core/config.py app/rag/ocr/factory.py app/rag/ocr/openai_vision.py app/rag/ocr/__init__.py tests/test_ocr.py tests/test_cloud_ocr.py
git commit -m "feat(rag): wire cloud OCR provider config"
```

### Task 3: 为云 OCR 的 HTTP 调用先写失败测试

**Files:**
- Modify: `tests/test_cloud_ocr.py`
- Test: `tests/test_cloud_ocr.py`

- [ ] **Step 1: 写 HTTP 失败与空结果测试**

```python
async def test_cloud_ocr_reports_http_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx
    from app.rag.ocr.base import OcrProviderError
    from app.rag.ocr.openai_vision import OpenAiVisionOcrProvider

    provider = OpenAiVisionOcrProvider(
        base_url="https://ocr.example.com/v1",
        api_key="ocr-key",
        model="ocr-model",
        timeout_seconds=30.0,
    )
    monkeypatch.setattr("app.rag.ocr.openai_vision._render_pdf_pages", lambda file_bytes: [b"page-1"])

    async def fake_post(*args, **kwargs):
        raise httpx.HTTPError("boom")

    monkeypatch.setattr("app.rag.ocr.openai_vision._post_vision_request", fake_post)
    with pytest.raises(OcrProviderError, match="云 OCR 调用失败"):
        await provider.aextract_pdf_text(b"%PDF")
```

```python
async def test_cloud_ocr_reports_empty_result(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.rag.ocr.base import OcrProviderError
    from app.rag.ocr.openai_vision import OpenAiVisionOcrProvider

    provider = OpenAiVisionOcrProvider(
        base_url="https://ocr.example.com/v1",
        api_key="ocr-key",
        model="ocr-model",
        timeout_seconds=30.0,
    )
    monkeypatch.setattr("app.rag.ocr.openai_vision._render_pdf_pages", lambda file_bytes: [b"page-1"])
    monkeypatch.setattr(
        "app.rag.ocr.openai_vision._post_vision_request",
        lambda *args, **kwargs: {"choices": [{"message": {"content": ""}}]},
    )
    with pytest.raises(OcrProviderError, match="OCR 已执行，但未提取到可用文本"):
        await provider.aextract_pdf_text(b"%PDF")
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `pytest tests/test_cloud_ocr.py -k "http_failure or empty_result" -v`

Expected: FAIL，提示异步方法或 HTTP 请求辅助函数尚不存在。

- [ ] **Step 3: 增加最小异步接口定义**

```python
class OpenAiVisionOcrProvider(OcrProvider):
    async def aextract_pdf_text(self, file_bytes: bytes) -> OcrResult:
        raise NotImplementedError

    def extract_pdf_text(self, file_bytes: bytes) -> OcrResult:
        return asyncio.run(self.aextract_pdf_text(file_bytes))
```

- [ ] **Step 4: 再次运行测试，确认失败收缩到真实 HTTP 行为**

Run: `pytest tests/test_cloud_ocr.py -k "http_failure or empty_result" -v`

Expected: FAIL，但失败原因已经集中到响应解析或错误映射。

- [ ] **Step 5: Commit**

```bash
git add tests/test_cloud_ocr.py app/rag/ocr/openai_vision.py
git commit -m "test(rag): add failing cloud OCR HTTP tests"
```

### Task 4: 实现逐页图片调用 OpenAI 兼容视觉接口

**Files:**
- Modify: `app/rag/ocr/openai_vision.py`
- Modify: `tests/test_cloud_ocr.py`
- Test: `tests/test_cloud_ocr.py`

- [ ] **Step 1: 先补成功路径测试**

```python
async def test_cloud_ocr_returns_text(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.rag.ocr.openai_vision import OpenAiVisionOcrProvider

    provider = OpenAiVisionOcrProvider(
        base_url="https://ocr.example.com/v1",
        api_key="ocr-key",
        model="ocr-model",
        timeout_seconds=30.0,
    )
    monkeypatch.setattr("app.rag.ocr.openai_vision._render_pdf_pages", lambda file_bytes: [b"page-1"])
    monkeypatch.setattr(
        "app.rag.ocr.openai_vision._post_vision_request",
        lambda *args, **kwargs: {"choices": [{"message": {"content": "云 OCR 文本"}}]},
    )
    result = await provider.aextract_pdf_text(b"%PDF")
    assert result.text == "云 OCR 文本"
    assert result.provider == "cloud_openai_vision"
```

- [ ] **Step 2: 运行云 OCR 测试，确认失败**

Run: `pytest tests/test_cloud_ocr.py -v`

Expected: FAIL，说明 HTTP 组包、base64 编码或响应解析尚未实现。

- [ ] **Step 3: 实现最小 HTTP 请求与响应解析**

```python
# app/rag/ocr/openai_vision.py
import asyncio
import base64
import httpx

async def _post_vision_request(*, base_url: str, api_key: str, model: str, image_bytes: bytes, timeout_seconds: float) -> dict:
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "请提取这页图片中的全部可读文本，不要总结，只输出文本内容。"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                ],
            }
        ],
    }
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
    response.raise_for_status()
    return response.json()

async def aextract_pdf_text(self, file_bytes: bytes) -> OcrResult:
    texts = []
    try:
        for image_bytes in _render_pdf_pages(file_bytes):
            data = await _post_vision_request(
                base_url=self.base_url,
                api_key=self.api_key,
                model=self.model,
                image_bytes=image_bytes,
                timeout_seconds=self.timeout_seconds,
            )
            content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            if content:
                texts.append(content)
    except httpx.HTTPError as exc:
        raise OcrProviderError("云 OCR 调用失败，请检查网络、鉴权或服务配置") from exc
    text = "\n".join(texts).strip()
    if not text:
        raise OcrProviderError("OCR 已执行，但未提取到可用文本")
    return OcrResult(text=text, provider="cloud_openai_vision", used_ocr=True)
```

- [ ] **Step 4: 运行云 OCR 测试**

Run: `pytest tests/test_cloud_ocr.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/rag/ocr/openai_vision.py tests/test_cloud_ocr.py
git commit -m "feat(rag): add OpenAI-compatible cloud OCR provider"
```

### Task 5: 将云 OCR 接入 PDF 解析器和导入任务回归

**Files:**
- Modify: `tests/test_document_parsers.py`
- Modify: `tests/test_rag_import_jobs.py`
- Modify: `app/rag/document_parsers/pdf.py`
- Test: `tests/test_document_parsers.py`
- Test: `tests/test_rag_import_jobs.py`

- [ ] **Step 1: 先写 parser 与任务平台的云 OCR 回归测试**

```python
def test_pdf_parser_uses_cloud_ocr_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.rag.document_parsers.pdf import PdfDocumentParser
    from app.rag.ocr.base import OcrResult

    class FakePage:
        def extract_text(self) -> str:
            return ""

    class FakeReader:
        is_encrypted = False
        pages = [FakePage()]

    class CloudProvider:
        def extract_pdf_text(self, file_bytes: bytes) -> OcrResult:
            return OcrResult(text="云 OCR 文本", provider="cloud_openai_vision", used_ocr=True)

    monkeypatch.setattr("app.rag.document_parsers.pdf.PdfReader", lambda stream: FakeReader())
    parsed = PdfDocumentParser(ocr_provider=CloudProvider()).extract(b"%PDF", "scan.pdf", "application/pdf")
    assert parsed.text == "云 OCR 文本"
    assert parsed.metadata["ocr_provider"] == "cloud_openai_vision"
```

```python
def test_pdf_import_job_fails_when_cloud_ocr_http_error(
    client: tuple[TestClient, User],
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client, _ = client
    _patch_storage_root(monkeypatch, tmp_path)

    from app.rag.document_parsers.base import DocumentParseError

    def fake_parse(file_bytes: bytes, filename: str, content_type: str | None):
        raise DocumentParseError("云 OCR 调用失败，请检查网络、鉴权或服务配置")

    monkeypatch.setattr("app.rag.import_jobs.parse_uploaded_document", fake_parse)
    resp = client.post(
        "/api/rag/import-jobs/upload",
        files=[("files", ("scan.pdf", io.BytesIO(b"%PDF"), "application/pdf"))],
    )
    assert resp.status_code == 202
```

- [ ] **Step 2: 运行 parser 和任务回归测试，确认失败**

Run: `pytest tests/test_document_parsers.py tests/test_rag_import_jobs.py -k "cloud_ocr" -v`

Expected: FAIL，提示 metadata 或失败语义尚未覆盖。

- [ ] **Step 3: 保持 `pdf.py` 不感知具体 provider，只验证 metadata 一致**

```python
# app/rag/document_parsers/pdf.py
metadata={
    "parser_name": "pdf_ocr",
    "used_ocr": True,
    "ocr_provider": result.provider,
}
```

```python
# app/rag/import_jobs.py
job.parser_name = str(parsed.metadata.get("parser_name") or "")
```

- [ ] **Step 4: 运行 parser 和任务回归测试**

Run: `pytest tests/test_document_parsers.py tests/test_rag_import_jobs.py -k "cloud_ocr" -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/rag/document_parsers/pdf.py app/rag/import_jobs.py tests/test_document_parsers.py tests/test_rag_import_jobs.py
git commit -m "test(rag): cover cloud OCR parser and job flow"
```

### Task 6: 更新 README 并做全量回归

**Files:**
- Modify: `README.md`
- Modify: `tests/test_ocr.py`
- Modify: `tests/test_cloud_ocr.py`

- [ ] **Step 1: 更新 README 的 OCR 配置说明**

```md
云 OCR（OpenAI 兼容视觉）支持以下配置优先级：

1. `RAG_OCR_BASE_URL` / `RAG_OCR_API_KEY` / `RAG_OCR_MODEL`
2. 若以上未配置，则回退到 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_DEFAULT_MODEL`

示例：

```env
RAG_OCR_ENABLED=true
RAG_OCR_PROVIDER=cloud
RAG_OCR_BASE_URL=https://your-openai-compatible-endpoint/v1
RAG_OCR_API_KEY=your-key
RAG_OCR_MODEL=gpt-4.1-mini
```
```
```

- [ ] **Step 2: 运行云 OCR 和既有 OCR 回归**

Run: `pytest tests/test_ocr.py tests/test_cloud_ocr.py tests/test_document_parsers.py tests/test_rag_import_jobs.py -v`

Expected: PASS

- [ ] **Step 3: 运行现有 RAG 回归**

Run: `pytest tests/test_rag.py tests/test_rag_backend.py tests/test_document_storage.py -v`

Expected: PASS

- [ ] **Step 4: 运行静态检查**

Run: `ruff check app tests`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add README.md app tests
git commit -m "feat(rag): add cloud OCR provider"
```
