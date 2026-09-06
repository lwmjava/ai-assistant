"""本地 Tesseract OCR provider。"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.rag.ocr.base import OcrPageBlock, OcrProvider, OcrProviderError, OcrResult

try:
    import fitz
except ImportError:  # pragma: no cover - 依赖缺失时由运行时错误提示
    fitz = None


@dataclass(slots=True)
class RenderedPdfPage:
    """渲染后的 PDF 页面。"""

    image_bytes: bytes
    page: int
    bbox: tuple[float, float, float, float] | None = None


def _render_pdf_pages(file_bytes: bytes) -> list[RenderedPdfPage]:
    """将 PDF 每页渲染为 PNG 字节，并保留页级 bbox。"""
    if fitz is None:
        raise OcrProviderError(
            "OCR 依赖缺失：未安装 PDF 渲染依赖 PyMuPDF",
            error_code="ocr_pdf_renderer_missing",
        )
    try:
        document = fitz.open(stream=file_bytes, filetype="pdf")
        pages: list[RenderedPdfPage] = []
        for index, page in enumerate(document, start=1):
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            pages.append(
                RenderedPdfPage(
                    image_bytes=pixmap.tobytes("png"),
                    page=index,
                    bbox=(0.0, 0.0, float(pixmap.width), float(pixmap.height)),
                )
            )
    except Exception as exc:  # noqa: BLE001
        raise OcrProviderError(
            "OCR 执行失败，请检查服务器 OCR 环境或稍后重试",
            error_code="ocr_pdf_render_failed",
        ) from exc
    if not pages:
        raise OcrProviderError("OCR 已执行，但未提取到可用文本", error_code="ocr_empty_result")
    return pages


def _parse_tesseract_tsv(
    tsv_text: str,
) -> list[tuple[str, tuple[float, float, float, float] | None]]:
    """解析 Tesseract TSV 输出，按行聚合成文本块及其边界框。"""
    rows = [line.split("\t") for line in tsv_text.splitlines() if line.strip()]
    if not rows:
        return []
    header = rows[0]
    try:
        index_of = {
            name: header.index(name)
            for name in (
                "level",
                "block_num",
                "par_num",
                "line_num",
                "left",
                "top",
                "width",
                "height",
                "text",
            )
        }
    except ValueError:
        return []

    grouped: dict[tuple[str, str, str], list[list[str]]] = {}
    for cols in rows[1:]:
        if any(index_of[name] >= len(cols) for name in index_of):
            continue
        if cols[index_of["level"]] != "5":
            continue
        text = cols[index_of["text"]].strip()
        if not text:
            continue
        key = (
            cols[index_of["block_num"]],
            cols[index_of["par_num"]],
            cols[index_of["line_num"]],
        )
        grouped.setdefault(key, []).append(cols)

    blocks: list[tuple[str, tuple[float, float, float, float] | None]] = []
    for key in sorted(
        grouped,
        key=lambda k: (int(k[2] or 0), int(k[1] or 0), int(k[0] or 0)),
    ):
        cols_list = grouped[key]
        words = [cols[index_of["text"]] for cols in cols_list]
        bbox: tuple[float, float, float, float] | None = None
        try:
            lefts = [float(cols[index_of["left"]]) for cols in cols_list]
            tops = [float(cols[index_of["top"]]) for cols in cols_list]
            rights = [
                float(cols[index_of["left"]]) + float(cols[index_of["width"]])
                for cols in cols_list
            ]
            bottoms = [
                float(cols[index_of["top"]]) + float(cols[index_of["height"]])
                for cols in cols_list
            ]
            bbox = (min(lefts), min(tops), max(rights), max(bottoms))
        except (ValueError, IndexError):
            bbox = None
        blocks.append((" ".join(words), bbox))
    return blocks


class TesseractOcrProvider(OcrProvider):
    """基于本地 Tesseract 的 OCR 实现。"""

    def __init__(self, *, languages: str, timeout_seconds: float) -> None:
        self.languages = languages
        self.timeout_seconds = timeout_seconds

    def extract_pdf_text(self, file_bytes: bytes) -> OcrResult:
        """调用 Tesseract 对 PDF 渲染页做 OCR。"""
        binary = shutil.which("tesseract")
        if not binary:
            raise OcrProviderError(
                "OCR 依赖缺失：未检测到 tesseract 或 chi_sim/eng 语言包",
                error_code="ocr_tesseract_missing",
            )

        texts: list[str] = []
        blocks: list[OcrPageBlock] = []
        try:
            for index, rendered_page in enumerate(_render_pdf_pages(file_bytes), start=1):
                if isinstance(rendered_page, RenderedPdfPage):
                    image_bytes = rendered_page.image_bytes
                    page_number = rendered_page.page
                    bbox = rendered_page.bbox
                else:  # 兼容测试中直接 patch 为 bytes 的旧写法
                    image_bytes = rendered_page
                    page_number = index
                    bbox = None
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
                    temp_file.write(image_bytes)
                    image_path = Path(temp_file.name)
                try:
                    proc = subprocess.run(
                        [binary, str(image_path), "stdout", "-l", self.languages, "tsv"],
                        capture_output=True,
                        text=True,
                        timeout=self.timeout_seconds,
                        check=False,
                    )
                finally:
                    image_path.unlink(missing_ok=True)
                if proc.returncode != 0:
                    message = proc.stderr.strip()
                    if "Failed loading language" in message:
                        raise OcrProviderError(
                            "OCR 依赖缺失：未检测到 tesseract 或 chi_sim/eng 语言包",
                            error_code="ocr_tesseract_language_missing",
                            command=f"{binary} {image_path} stdout -l {self.languages} tsv",
                            exit_code=proc.returncode,
                            stderr=proc.stderr,
                        )
                    raise OcrProviderError(
                        "OCR 执行失败，请检查服务器 OCR 环境或稍后重试",
                        error_code="ocr_tesseract_non_zero_exit",
                        command=f"{binary} {image_path} stdout -l {self.languages} tsv",
                        exit_code=proc.returncode,
                        stdout=proc.stdout,
                        stderr=proc.stderr,
                    )
                line_blocks = _parse_tesseract_tsv(proc.stdout)
                if line_blocks:
                    page_text = "\n".join(text for text, _bbox in line_blocks).strip()
                    texts.append(page_text)
                    for order, (line_text, line_bbox) in enumerate(line_blocks, start=1):
                        blocks.append(
                            OcrPageBlock(
                                page=page_number,
                                text=line_text,
                                bbox=line_bbox,
                                reading_order=order,
                                layout_role="body",
                            )
                        )
                else:
                    page_text = proc.stdout.strip()
                    texts.append(page_text)
                    if page_text:
                        blocks.append(
                            OcrPageBlock(
                                page=page_number,
                                text=page_text,
                                bbox=bbox,
                                reading_order=1,
                                layout_role="body",
                            )
                        )
        except subprocess.TimeoutExpired as exc:
            raise OcrProviderError(
                "OCR 执行失败，请检查服务器 OCR 环境或稍后重试",
                error_code="ocr_tesseract_timeout",
                command=" ".join(str(part) for part in (exc.cmd or [])),
                stdout=exc.stdout,
                stderr=exc.stderr,
            ) from exc

        text = "\n".join(part for part in texts if part).strip()
        if not text:
            raise OcrProviderError("OCR 已执行，但未提取到可用文本", error_code="ocr_empty_result")
        return OcrResult(text=text, provider="tesseract", used_ocr=True, blocks=blocks)
