"""OCR provider 测试。"""

import shutil
import subprocess
from types import SimpleNamespace

import pytest


def test_tesseract_provider_reports_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.rag.ocr.base import OcrProviderError
    from app.rag.ocr.tesseract import TesseractOcrProvider

    monkeypatch.setattr(shutil, "which", lambda name: None)
    provider = TesseractOcrProvider(languages="chi_sim+eng", timeout_seconds=30.0)
    with pytest.raises(OcrProviderError, match="未检测到 tesseract"):
        provider.extract_pdf_text(b"%PDF")


def test_tesseract_provider_reports_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.rag.ocr.base import OcrProviderError
    from app.rag.ocr.tesseract import TesseractOcrProvider

    monkeypatch.setattr(shutil, "which", lambda name: "C:/tesseract.exe")

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="tesseract", timeout=10)

    monkeypatch.setattr("app.rag.ocr.tesseract.subprocess.run", fake_run)
    monkeypatch.setattr(
        "app.rag.ocr.tesseract._render_pdf_pages", lambda file_bytes: [b"page-1"]
    )
    provider = TesseractOcrProvider(languages="chi_sim+eng", timeout_seconds=10.0)
    with pytest.raises(OcrProviderError, match="OCR 执行失败"):
        provider.extract_pdf_text(b"%PDF")


def test_tesseract_provider_returns_ocr_text(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.rag.ocr.tesseract import TesseractOcrProvider

    monkeypatch.setattr(shutil, "which", lambda name: "C:/tesseract.exe")
    monkeypatch.setattr(
        "app.rag.ocr.tesseract._render_pdf_pages", lambda file_bytes: [b"page-1"]
    )
    monkeypatch.setattr(
        "app.rag.ocr.tesseract.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout="识别文本", stderr=""
        ),
    )
    provider = TesseractOcrProvider(languages="chi_sim+eng", timeout_seconds=10.0)
    result = provider.extract_pdf_text(b"%PDF")
    assert result.text == "识别文本"
    assert result.provider == "tesseract"
    assert result.blocks[0].text == "识别文本"
    assert result.blocks[0].page == 1


def test_tesseract_provider_preserves_rendered_page_bbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.rag.ocr.tesseract import RenderedPdfPage, TesseractOcrProvider

    monkeypatch.setattr(shutil, "which", lambda name: "C:/tesseract.exe")
    monkeypatch.setattr(
        "app.rag.ocr.tesseract._render_pdf_pages",
        lambda file_bytes: [
            RenderedPdfPage(image_bytes=b"page-1", page=2, bbox=(0.0, 0.0, 1440.0, 1920.0))
        ],
    )
    monkeypatch.setattr(
        "app.rag.ocr.tesseract.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout="第 2 页识别文本", stderr=""
        ),
    )
    provider = TesseractOcrProvider(languages="chi_sim+eng", timeout_seconds=10.0)
    result = provider.extract_pdf_text(b"%PDF")
    assert result.blocks[0].page == 2
    assert result.blocks[0].bbox == (0.0, 0.0, 1440.0, 1920.0)


def test_tesseract_provider_parses_tsv_line_bboxes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.rag.ocr.tesseract import RenderedPdfPage, TesseractOcrProvider

    tsv = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        "5\t1\t1\t1\t1\t1\t10\t20\t100\t20\t95\t第一行\n"
        "5\t1\t1\t1\t1\t2\t120\t20\t80\t20\t95\t内容\n"
        "5\t1\t1\t1\t2\t1\t10\t60\t90\t20\t95\t第二行\n"
    )
    monkeypatch.setattr(shutil, "which", lambda name: "C:/tesseract.exe")
    monkeypatch.setattr(
        "app.rag.ocr.tesseract._render_pdf_pages",
        lambda file_bytes: [
            RenderedPdfPage(image_bytes=b"page-1", page=1, bbox=(0.0, 0.0, 200.0, 100.0))
        ],
    )
    monkeypatch.setattr(
        "app.rag.ocr.tesseract.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=tsv, stderr=""),
    )
    provider = TesseractOcrProvider(languages="chi_sim+eng", timeout_seconds=10.0)
    result = provider.extract_pdf_text(b"%PDF")

    assert [block.text for block in result.blocks] == ["第一行 内容", "第二行"]
    assert [block.reading_order for block in result.blocks] == [1, 2]
    assert result.blocks[0].bbox == (10.0, 20.0, 200.0, 40.0)
    assert result.blocks[1].bbox == (10.0, 60.0, 100.0, 80.0)


def test_get_ocr_provider_reports_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings
    from app.rag.ocr.base import OcrProviderError
    from app.rag.ocr.factory import get_ocr_provider

    monkeypatch.setattr(settings, "RAG_OCR_ENABLED", False)
    with pytest.raises(OcrProviderError, match="当前系统未启用 OCR"):
        get_ocr_provider()


def test_get_ocr_provider_returns_cloud_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
