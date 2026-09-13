"""Office 文档解析器（docx / xlsx / pptx）。

分别基于 python-docx / openpyxl / python-pptx 按需导入并提取文本；
依赖缺失或文件异常统一转换为 ``DocumentParseError``，避免内部错误外泄。
"""

from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree as ET

from app.rag.document_parsers.base import (
    DocumentOcrRequiredError,
    DocumentParseError,
    DocumentParser,
    ParsedBlock,
    ParsedDocument,
)
from app.rag.document_parsers.libreoffice import convert_office_bytes_with_libreoffice
from app.rag.document_parsers.text import file_extension, title_from_filename
from app.rag.import_trace import copy_import_trace
from app.rag.ocr.base import OcrProvider, OcrProviderError
from app.rag.ocr.factory import get_ocr_provider

try:
    from pptx import Presentation
except ImportError:  # pragma: no cover - 依赖缺失时由运行时错误提示
    Presentation = None

# 供测试和调用方稳定 patch 的模块级别别名。
_convert_office_bytes_with_libreoffice = convert_office_bytes_with_libreoffice


def _make_block(
    block_type: str,
    text: str,
    order: int,
    *,
    page: int | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    section_path: list[str] | None = None,
    metadata: dict[str, str | int | float | bool | None] | None = None,
) -> ParsedBlock:
    """构造解析块，统一清洗空白与默认值。"""
    return ParsedBlock(
        type=block_type,
        text=text.strip(),
        order=order,
        page=page,
        bbox=bbox,
        section_path=list(section_path or []),
        metadata=dict(metadata or {}),
    )


def _append_unique(lines: list[str], seen: set[str], value: str) -> None:
    """按出现顺序去重追加文本行。"""
    text = value.strip()
    if not text or text in seen:
        return
    seen.add(text)
    lines.append(text)


def _extract_text_frame_lines(text_frame) -> list[str]:
    """从 text frame 的段落/run 中提取非空文本。"""
    lines: list[str] = []
    for paragraph in getattr(text_frame, "paragraphs", []):
        run_texts = [str(getattr(run, "text", "")).strip() for run in getattr(paragraph, "runs", [])]
        merged = "".join(part for part in run_texts if part)
        if merged:
            lines.append(merged)
    return lines


def _extract_table_lines(table) -> list[str]:
    """从表格中逐单元格提取文本。"""
    lines: list[str] = []
    for row in getattr(table, "rows", []):
        for cell in getattr(row, "cells", []):
            for line in _extract_text_frame_lines(getattr(cell, "text_frame", None)):
                if line:
                    lines.append(line)
    return lines


def _extract_chart_lines(chart) -> list[str]:
    """提取图表标题、坐标轴、系列名、分类名、图例和数据标签。"""
    lines: list[str] = []
    if getattr(chart, "has_title", False):
        chart_title = getattr(chart, "chart_title", None)
        if getattr(chart_title, "has_text_frame", False):
            lines.extend(_extract_text_frame_lines(getattr(chart_title, "text_frame", None)))

    for axis_name in ("category_axis", "value_axis"):
        axis = getattr(chart, axis_name, None)
        if axis is not None and getattr(axis, "has_title", False):
            axis_title = getattr(axis, "axis_title", None)
            if getattr(axis_title, "has_text_frame", False):
                lines.extend(_extract_text_frame_lines(getattr(axis_title, "text_frame", None)))

    for plot in getattr(chart, "plots", []):
        for series in getattr(plot, "series", []):
            name = str(getattr(series, "name", "")).strip()
            if name:
                lines.append(name)
            for point in getattr(series, "points", []):
                data_label = getattr(point, "data_label", None)
                if getattr(data_label, "has_text_frame", False):
                    lines.extend(_extract_text_frame_lines(getattr(data_label, "text_frame", None)))
        for category in getattr(plot, "categories", []):
            label = str(getattr(category, "label", "")).strip()
            if label:
                lines.append(label)

    if getattr(chart, "has_legend", False):
        legend = getattr(chart, "legend", None)
        if legend is not None:
            if getattr(legend, "has_text_frame", False):
                lines.extend(_extract_text_frame_lines(getattr(legend, "text_frame", None)))
            for entry in getattr(legend, "entries", []):
                text_frame = getattr(entry, "text_frame", None)
                if text_frame is not None:
                    lines.extend(_extract_text_frame_lines(text_frame))
                else:
                    text = str(getattr(entry, "text", "")).strip()
                    if text:
                        lines.append(text)
    return lines


def _shape_bbox(shape) -> tuple[float, float, float, float] | None:
    """从 shape 提取位置框。"""
    left = getattr(shape, "left", None)
    top = getattr(shape, "top", None)
    width = getattr(shape, "width", None)
    height = getattr(shape, "height", None)
    try:
        if None in (left, top, width, height):
            return None
        x0 = float(left)
        y0 = float(top)
        w = float(width)
        h = float(height)
    except (TypeError, ValueError):
        return None
    return (x0, y0, x0 + w, y0 + h)


def _shape_layout_role(shape, *, notes: bool = False) -> str:
    """根据 shape 类型和占位符信息推断版式角色。"""
    if notes:
        return "note"
    if getattr(shape, "has_table", False):
        return "table"
    if getattr(shape, "has_chart", False):
        return "chart"
    placeholder_format = getattr(shape, "placeholder_format", None)
    placeholder_type = str(getattr(placeholder_format, "type", "")).lower()
    name = str(getattr(shape, "name", "")).lower()
    if "subtitle" in placeholder_type or "subtitle" in name:
        return "subtitle"
    if "title" in placeholder_type or "title" in name:
        return "title"
    if "footer" in placeholder_type or "footer" in name:
        return "footer"
    return "body"


def _extract_table_entries(table) -> list[tuple[str, str, str]]:
    """从表格中提取带角色的文本条目。"""
    entries: list[tuple[str, str, str]] = []
    for row_index, row in enumerate(getattr(table, "rows", [])):
        role = "table_header" if row_index == 0 else "table_cell"
        for cell in getattr(row, "cells", []):
            for line in _extract_text_frame_lines(getattr(cell, "text_frame", None)):
                if line:
                    entries.append((line, "table_text", role))
    return entries


def _extract_chart_entries(chart) -> list[tuple[str, str, str]]:
    """提取带细分角色的图表文本条目。"""
    entries: list[tuple[str, str, str]] = []
    if getattr(chart, "has_title", False):
        chart_title = getattr(chart, "chart_title", None)
        if getattr(chart_title, "has_text_frame", False):
            for line in _extract_text_frame_lines(getattr(chart_title, "text_frame", None)):
                entries.append((line, "chart_text", "chart_title"))

    for axis_name in ("category_axis", "value_axis"):
        axis = getattr(chart, axis_name, None)
        if axis is not None and getattr(axis, "has_title", False):
            axis_title = getattr(axis, "axis_title", None)
            if getattr(axis_title, "has_text_frame", False):
                for line in _extract_text_frame_lines(getattr(axis_title, "text_frame", None)):
                    entries.append((line, "chart_text", "chart_axis"))

    for plot in getattr(chart, "plots", []):
        for series in getattr(plot, "series", []):
            name = str(getattr(series, "name", "")).strip()
            if name:
                entries.append((name, "chart_text", "chart_series"))
            for point in getattr(series, "points", []):
                data_label = getattr(point, "data_label", None)
                if getattr(data_label, "has_text_frame", False):
                    for line in _extract_text_frame_lines(getattr(data_label, "text_frame", None)):
                        entries.append((line, "chart_text", "chart_data_label"))
        for category in getattr(plot, "categories", []):
            label = str(getattr(category, "label", "")).strip()
            if label:
                entries.append((label, "chart_text", "chart_category"))

    if getattr(chart, "has_legend", False):
        legend = getattr(chart, "legend", None)
        if legend is not None:
            if getattr(legend, "has_text_frame", False):
                for line in _extract_text_frame_lines(getattr(legend, "text_frame", None)):
                    entries.append((line, "chart_text", "chart_legend"))
            for entry in getattr(legend, "entries", []):
                text_frame = getattr(entry, "text_frame", None)
                if text_frame is not None:
                    for line in _extract_text_frame_lines(text_frame):
                        entries.append((line, "chart_text", "chart_legend"))
                else:
                    text = str(getattr(entry, "text", "")).strip()
                    if text:
                        entries.append((text, "chart_text", "chart_legend"))
    return entries


def _append_shape_blocks(
    blocks: list[ParsedBlock],
    *,
    page: int,
    order_ref: list[int],
    parser_name: str,
    block_type: str,
    layout_role: str,
    texts: list[str],
    bbox: tuple[float, float, float, float] | None,
) -> None:
    """将一组同 shape 文本写入块列表。"""
    for text in texts:
        value = text.strip()
        if not value:
            continue
        blocks.append(
            _make_block(
                block_type,
                value,
                order_ref[0],
                page=page,
                bbox=bbox,
                metadata={
                    "parser_name": parser_name,
                    "reading_order": order_ref[0] + 1,
                    "layout_role": layout_role,
                },
            )
        )
        order_ref[0] += 1


def _extract_shape_blocks(
    shape,
    *,
    page: int,
    order_ref: list[int],
    parser_name: str,
    notes: bool = False,
) -> list[ParsedBlock]:
    """递归提取 shape 级结构块，尽量保留真实 bbox 与 layout_role。"""
    blocks: list[ParsedBlock] = []
    bbox = _shape_bbox(shape)
    base_role = _shape_layout_role(shape, notes=notes)

    if getattr(shape, "has_text_frame", False):
        _append_shape_blocks(
            blocks,
            page=page,
            order_ref=order_ref,
            parser_name=parser_name,
            block_type="shape_text",
            layout_role=base_role,
            texts=_extract_text_frame_lines(getattr(shape, "text_frame", None)),
            bbox=bbox,
        )
    elif getattr(shape, "text", "").strip():
        _append_shape_blocks(
            blocks,
            page=page,
            order_ref=order_ref,
            parser_name=parser_name,
            block_type="shape_text",
            layout_role=base_role,
            texts=[str(getattr(shape, "text", "")).strip()],
            bbox=bbox,
        )

    if getattr(shape, "has_table", False):
        for text, block_type, layout_role in _extract_table_entries(getattr(shape, "table", None)):
            _append_shape_blocks(
                blocks,
                page=page,
                order_ref=order_ref,
                parser_name=parser_name,
                block_type=block_type,
                layout_role=layout_role,
                texts=[text],
                bbox=bbox,
            )

    if getattr(shape, "has_chart", False):
        for text, block_type, layout_role in _extract_chart_entries(getattr(shape, "chart", None)):
            _append_shape_blocks(
                blocks,
                page=page,
                order_ref=order_ref,
                parser_name=parser_name,
                block_type=block_type,
                layout_role=layout_role,
                texts=[text],
                bbox=bbox,
            )

    child_shapes = getattr(shape, "shapes", None)
    if child_shapes:
        for child in child_shapes:
            blocks.extend(
                _extract_shape_blocks(
                    child,
                    page=page,
                    order_ref=order_ref,
                    parser_name=parser_name,
                    notes=notes,
                )
            )
    return blocks


def _extract_pptx_structured_blocks(presentation, *, parser_name: str) -> list[ParsedBlock]:
    """基于 python-pptx 提取带版式信息的结构块。"""
    blocks: list[ParsedBlock] = []
    order_ref = [0]
    for page, slide in enumerate(getattr(presentation, "slides", []), start=1):
        blocks.append(
            _make_block(
                "slide_marker",
                f"# Slide {page}",
                order_ref[0],
                page=page,
                metadata={
                    "parser_name": parser_name,
                    "reading_order": order_ref[0] + 1,
                    "layout_role": "page_marker",
                },
            )
        )
        order_ref[0] += 1
        for shape in getattr(slide, "shapes", []):
            blocks.extend(
                _extract_shape_blocks(
                    shape,
                    page=page,
                    order_ref=order_ref,
                    parser_name=parser_name,
                )
            )
        if getattr(slide, "has_notes_slide", False):
            notes_slide = getattr(slide, "notes_slide", None)
            if notes_slide is not None:
                for shape in getattr(notes_slide, "shapes", []):
                    blocks.extend(
                        _extract_shape_blocks(
                            shape,
                            page=page,
                            order_ref=order_ref,
                            parser_name=parser_name,
                            notes=True,
                        )
                    )
    return blocks


def _presentation_trace_stage(filename: str, phase: str) -> str:
    """为 PPT/PPTX OCR 兜底生成明确的追踪阶段名。"""
    return f"{file_extension(filename)}_{phase}"


def _extract_presentation_pdf_ocr_text(
    file_bytes: bytes,
    filename: str,
    ocr_provider: OcrProvider | None,
) -> tuple[str, dict[str, str | int | bool | None], list[ParsedBlock]]:
    """在 PPT/PPTX 文本为空时，转换为 PDF 后调用 OCR 提取文本。"""
    source_ext = file_extension(filename)
    try:
        pdf_bytes = _convert_office_bytes_with_libreoffice(file_bytes, source_ext, "pdf")
    except DocumentParseError as exc:
        setattr(exc, "stage", _presentation_trace_stage(filename, "pdf_convert"))
        raise
    try:
        provider = ocr_provider or get_ocr_provider()
        result = provider.extract_pdf_text(pdf_bytes)
    except OcrProviderError as exc:
        message = str(exc)
        if "未启用 OCR" in message:
            raise copy_import_trace(
                DocumentOcrRequiredError(message),
                exc,
                stage=_presentation_trace_stage(filename, "pdf_ocr"),
            ) from exc
        raise copy_import_trace(
            DocumentParseError(message),
            exc,
            stage=_presentation_trace_stage(filename, "pdf_ocr"),
        ) from exc
    metadata = {
        "parser_name": f"{source_ext}_ocr",
        "used_ocr": True,
        "ocr_provider": result.provider,
    }
    blocks = [
        _make_block(
            "ocr_slide",
            block.text,
            index,
            page=block.page,
            bbox=block.bbox,
            metadata={
                **metadata,
                "reading_order": block.reading_order,
                "layout_role": block.layout_role,
            },
        )
        for index, block in enumerate(result.blocks)
        if block.text.strip()
    ]
    if not blocks:
        blocks = [
            _make_block(
                "ocr_slide",
                result.text,
                0,
                page=1,
                metadata={
                    **metadata,
                    "reading_order": 1,
                    "layout_role": "body",
                },
            )
        ]
    return result.text, metadata, blocks


def _extract_shape_lines(shape) -> list[str]:
    """递归提取 shape、分组 shape、表格中的文本。"""
    lines: list[str] = []
    if getattr(shape, "has_text_frame", False):
        lines.extend(_extract_text_frame_lines(getattr(shape, "text_frame", None)))
    elif getattr(shape, "text", "").strip():
        lines.append(str(getattr(shape, "text", "")).strip())

    if getattr(shape, "has_table", False):
        lines.extend(_extract_table_lines(getattr(shape, "table", None)))

    if getattr(shape, "has_chart", False):
        lines.extend(_extract_chart_lines(getattr(shape, "chart", None)))

    child_shapes = getattr(shape, "shapes", None)
    if child_shapes:
        for child in child_shapes:
            lines.extend(_extract_shape_lines(child))
    return lines


def _extract_notes_lines(slide) -> list[str]:
    """提取备注页中的文本。"""
    if not getattr(slide, "has_notes_slide", False):
        return []
    notes_slide = getattr(slide, "notes_slide", None)
    if notes_slide is None:
        return []
    lines: list[str] = []
    for shape in getattr(notes_slide, "shapes", []):
        lines.extend(_extract_shape_lines(shape))
    return lines


def _extract_pptx_structured_lines(presentation) -> list[str]:
    """基于 python-pptx 做结构化提取。"""
    lines: list[str] = []
    seen: set[str] = set()
    for index, slide in enumerate(getattr(presentation, "slides", []), start=1):
        _append_unique(lines, seen, f"# Slide {index}")
        for shape in getattr(slide, "shapes", []):
            for line in _extract_shape_lines(shape):
                _append_unique(lines, seen, line)
        for line in _extract_notes_lines(slide):
            _append_unique(lines, seen, line)
    return lines


def _is_slide_marker(line: str) -> bool:
    """判断该行是否只是幻灯片分隔标记。"""
    return line.startswith("# Slide ")


def _pptx_blocks_from_lines(
    lines: list[str],
    *,
    parser_name: str,
) -> list[ParsedBlock]:
    """将 PPT 提取文本行映射为最小结构块列表。"""
    blocks: list[ParsedBlock] = []
    current_page: int | None = None
    order = 0
    for line in lines:
        text = line.strip()
        if not text:
            continue
        if _is_slide_marker(text):
            try:
                current_page = int(text.removeprefix("# Slide ").strip())
            except ValueError:
                current_page = current_page or 1
            blocks.append(
                _make_block(
                    "slide_heading",
                    text,
                    order,
                    page=current_page,
                    metadata={
                        "parser_name": parser_name,
                        "reading_order": order + 1,
                        "layout_role": "page_marker",
                    },
                )
            )
        else:
            blocks.append(
                _make_block(
                    "slide_text",
                    text,
                    order,
                    page=current_page,
                    metadata={
                        "parser_name": parser_name,
                        "reading_order": order + 1,
                        "layout_role": "body",
                    },
                )
            )
        order += 1
    return blocks


def _extract_pptx_ooxml_lines(file_bytes: bytes) -> list[str]:
    """从 PPTX 的底层 OOXML 中兜底提取文本。"""
    lines: list[str] = []
    seen: set[str] = set()
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as archive:
            candidates = sorted(
                name
                for name in archive.namelist()
                if name.startswith(
                    ("ppt/slides/", "ppt/notesSlides/", "ppt/charts/", "ppt/diagrams/")
                )
                and name.endswith(".xml")
            )
            for name in candidates:
                root = ET.fromstring(archive.read(name))
                for node in root.findall(".//{*}t"):
                    _append_unique(lines, seen, node.text or "")
    except Exception:  # noqa: BLE001 - 兜底提取失败时回到调用方统一判空/报错
        return []
    return lines


class DocxDocumentParser(DocumentParser):
    """解析 docx 文档正文段落文本。"""

    def supports(self, filename: str, content_type: str | None) -> bool:
        """扩展名为 docx 时返回 True。"""
        return file_extension(filename) == "docx"

    def extract(
        self, file_bytes: bytes, filename: str, content_type: str | None
    ) -> ParsedDocument:
        """读取 docx 段落并拼接为纯文本。"""
        try:
            from docx import Document

            document = Document(io.BytesIO(file_bytes))
            parts = [paragraph.text.strip() for paragraph in document.paragraphs]
            text = "\n".join(part for part in parts if part)
            blocks = [
                _make_block(
                    "paragraph",
                    part,
                    index,
                    metadata={
                        "parser_name": "docx",
                        "reading_order": index + 1,
                        "layout_role": "body",
                    },
                )
                for index, part in enumerate(parts)
                if part
            ]
        except Exception as exc:  # noqa: BLE001
            raise DocumentParseError("文件解析失败，请确认文件未损坏或未加密") from exc

        return ParsedDocument(
            text=text,
            title=title_from_filename(filename),
            source=filename,
            extension="docx",
            content_type=content_type,
            metadata={"parser_name": "docx"},
            blocks=blocks,
        )


class XlsxDocumentParser(DocumentParser):
    """解析 xlsx 工作簿，汇总各 sheet 的文本单元格。"""

    def supports(self, filename: str, content_type: str | None) -> bool:
        """扩展名为 xlsx 时返回 True。"""
        return file_extension(filename) == "xlsx"

    def extract(
        self, file_bytes: bytes, filename: str, content_type: str | None
    ) -> ParsedDocument:
        """遍历所有工作表，按行聚合非空单元格为文本。"""
        try:
            from openpyxl import load_workbook

            workbook = load_workbook(io.BytesIO(file_bytes), data_only=True)
            rows: list[str] = []
            blocks: list[ParsedBlock] = []
            order = 0
            for sheet in workbook.worksheets:
                rows.append(f"# Sheet: {sheet.title}")
                blocks.append(
                    _make_block(
                        "sheet_heading",
                        f"# Sheet: {sheet.title}",
                        order,
                        section_path=[sheet.title],
                        metadata={
                            "parser_name": "xlsx",
                            "reading_order": order + 1,
                            "layout_role": "title",
                        },
                    )
                )
                order += 1
                for row in sheet.iter_rows(values_only=True):
                    cells = [str(cell).strip() for cell in row if cell is not None and str(cell).strip()]
                    if cells:
                        line = ", ".join(cells)
                        rows.append(line)
                        blocks.append(
                            _make_block(
                                "sheet_row",
                                line,
                                order,
                                section_path=[sheet.title],
                                metadata={
                                    "parser_name": "xlsx",
                                    "reading_order": order + 1,
                                    "layout_role": "table_row",
                                },
                            )
                        )
                        order += 1
            text = "\n".join(rows)
        except Exception as exc:  # noqa: BLE001
            raise DocumentParseError("文件解析失败，请确认文件未损坏或未加密") from exc

        return ParsedDocument(
            text=text,
            title=title_from_filename(filename),
            source=filename,
            extension="xlsx",
            content_type=content_type,
            metadata={"parser_name": "xlsx"},
            blocks=blocks,
        )


class PptxDocumentParser(DocumentParser):
    """解析 pptx 幻灯片，按页汇总形状中的文本。"""

    def __init__(self, ocr_provider: OcrProvider | None = None) -> None:
        self._ocr_provider = ocr_provider

    def supports(self, filename: str, content_type: str | None) -> bool:
        """扩展名为 pptx 时返回 True。"""
        return file_extension(filename) == "pptx"

    def extract(
        self, file_bytes: bytes, filename: str, content_type: str | None
    ) -> ParsedDocument:
        """按“结构化提取 -> OOXML 兜底”的多阶段策略提取文本。"""
        structured_blocks: list[ParsedBlock] = []
        structured_error: Exception | None = None
        if Presentation is not None:
            try:
                presentation = Presentation(io.BytesIO(file_bytes))
                structured_blocks = _extract_pptx_structured_blocks(
                    presentation, parser_name="pptx"
                )
            except Exception as exc:  # noqa: BLE001
                structured_error = exc

        structured_lines = [block.text for block in structured_blocks]
        ooxml_lines = _extract_pptx_ooxml_lines(file_bytes)
        combined_lines = [part for part in [*structured_lines, *ooxml_lines] if part]
        text = "\n".join(combined_lines).strip()
        metadata: dict[str, str | int | bool | None] = {"parser_name": "pptx"}
        blocks = list(structured_blocks)
        existing_texts = {block.text for block in blocks}
        blocks.extend(
            block
            for block in _pptx_blocks_from_lines(ooxml_lines, parser_name="pptx")
            if block.text not in existing_texts
        )
        meaningful_lines = [line for line in combined_lines if not _is_slide_marker(line)]
        if not meaningful_lines:
            text, metadata, blocks = _extract_presentation_pdf_ocr_text(
                file_bytes, filename, self._ocr_provider
            )
        if not text and structured_error is not None:
            raise DocumentParseError("文件解析失败，请确认文件未损坏或未加密") from structured_error

        return ParsedDocument(
            text=text,
            title=title_from_filename(filename),
            source=filename,
            extension="pptx",
            content_type=content_type,
            metadata=metadata,
            blocks=blocks,
        )
