"""RAG-033「多格式解析与 OCR 质量基线」测试。

覆盖三类证据：

1. **真实依赖样本报告**：单栏/双栏/表格 PDF、DOCX、XLSX、PPTX、Markdown/TXT
   的覆盖率与阅读顺序（样本由 ``scripts/generate_parse_corpus.py`` 真实生成）。
2. **依赖失败行为**：OCR 依赖缺失时的错误类型与文案、是否影响非 OCR 文档、
   空文件/损坏文件/不支持扩展名的失败行为。
3. **报告结构**：覆盖率、顺序、失败清单字段齐全，建议门槛明确标注未锁定。

本机事实：未安装 ``pytesseract`` / ``pdfplumber``，也没有 tesseract 二进制，
因此**中文 OCR 质量在本机无法用真实依赖验证**，相关断言只验证「依赖缺失时的
失败行为被正确标记」，不假装 OCR 已通过。
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"


def _load_script(module_name: str):
    """按文件路径加载 scripts/ 下的脚本模块（scripts 不是包）。"""
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    spec = importlib.util.spec_from_file_location(
        f"_rag033_{module_name}", _SCRIPTS_DIR / f"{module_name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_corpus_mod = _load_script("generate_parse_corpus")
_report_mod = _load_script("parse_quality_report")

CORPUS_DIR = _corpus_mod.CORPUS_DIR


@pytest.fixture(scope="session")
def corpus() -> Path:
    """保证样本集存在；缺失时用真实依赖重新生成。"""
    if not (CORPUS_DIR / "manifest.json").exists():
        _corpus_mod.generate()
    return CORPUS_DIR


@pytest.fixture(scope="session")
def report(corpus: Path) -> dict:
    """跑一遍完整样本集，返回报告字典。"""
    return _report_mod.build_report(ocr_provider="tesseract")


def _entry(report: dict, name: str) -> dict:
    """按样本名取报告条目。"""
    for item in report["samples"]:
        if item["name"] == name:
            return item
    raise AssertionError(f"报告中缺少样本条目：{name}")


def _sample_bytes(corpus: Path, name: str) -> bytes:
    """读取样本字节。"""
    return (corpus / name).read_bytes()


# ---------------------------------------------------------------------------
# OCR 依赖缺失行为
# ---------------------------------------------------------------------------


def test_ocr_sample_marked_dependency_missing_not_pass(report: dict) -> None:
    """扫描件样本必须标记为 dependency_missing，绝不能写成 pass。"""
    entry = _entry(report, "pdf_scanned.pdf")
    assert entry["status"] == "dependency_missing"
    assert entry["status"] != "pass"
    assert entry["parsed"] is False
    assert entry["error_code"] == "ocr_tesseract_missing"
    assert "依赖缺失" in (entry["error_message"] or "")
    assert entry["name"] in [item["name"] for item in report["dependency_missing_list"]]
    assert entry["name"] not in [item["name"] for item in report["failures"]]


def test_ocr_report_never_claims_ocr_verified(report: dict) -> None:
    """报告需明示未使用 Mock，且 OCR 样本未产生可用文本。"""
    entry = _entry(report, "pdf_scanned.pdf")
    assert report["ocr_probe"]["used_mock"] is False
    assert report["ocr_probe"]["provider"] == "tesseract"
    assert entry["coverage"] is None
    assert entry["text_length"] == 0
    if shutil.which("tesseract") is None:
        assert report["totals"]["dependency_missing"] >= 1
        assert entry["name"] in [item["name"] for item in report["dependency_missing_list"]]


@pytest.mark.skipif(shutil.which("tesseract") is not None, reason="本机已有 tesseract 二进制")
def test_tesseract_provider_raises_dependency_missing(corpus: Path) -> None:
    """真实 tesseract provider 在二进制缺失时应报 ocr_tesseract_missing。"""
    from app.rag.ocr.base import OcrProviderError
    from app.rag.ocr.tesseract import TesseractOcrProvider

    provider = TesseractOcrProvider(languages="chi_sim+eng", timeout_seconds=30.0)
    with pytest.raises(OcrProviderError) as excinfo:
        provider.extract_pdf_text(_sample_bytes(corpus, "pdf_scanned.pdf"))
    assert excinfo.value.error_code == "ocr_tesseract_missing"
    assert "依赖缺失" in str(excinfo.value)


def test_ocr_enabled_but_dependency_missing_raises_parse_error(
    corpus: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OCR 已启用但依赖缺失：抛 DocumentParseError 并携带依赖缺失错误码。"""
    from app.core.config import settings
    from app.rag.document_parsers.base import DocumentParseError

    monkeypatch.setattr(settings, "RAG_OCR_ENABLED", True)
    monkeypatch.setattr(settings, "RAG_OCR_PROVIDER", "tesseract")

    from app.rag.document_parsers.service import parse_uploaded_document

    with pytest.raises(DocumentParseError) as excinfo:
        parse_uploaded_document(
            _sample_bytes(corpus, "pdf_scanned.pdf"), "pdf_scanned.pdf", "application/pdf"
        )
    assert getattr(excinfo.value, "error_code", None) == "ocr_tesseract_missing"
    assert "依赖缺失" in str(excinfo.value)


def test_ocr_disabled_raises_ocr_required(
    corpus: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OCR 未启用：抛 DocumentOcrRequiredError，文案说明需要 OCR。"""
    from app.core.config import settings
    from app.rag.document_parsers.base import DocumentOcrRequiredError

    monkeypatch.setattr(settings, "RAG_OCR_ENABLED", False)

    from app.rag.document_parsers.service import parse_uploaded_document

    with pytest.raises(DocumentOcrRequiredError, match="未启用 OCR"):
        parse_uploaded_document(
            _sample_bytes(corpus, "pdf_scanned.pdf"), "pdf_scanned.pdf", "application/pdf"
        )


def test_ocr_unavailable_does_not_break_normal_pdf(
    corpus: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OCR 不可用不得影响普通 PDF 解析：开启与未开启两种配置都要解析成功。"""
    from app.core.config import settings
    from app.rag.document_parsers.service import parse_uploaded_document

    file_bytes = _sample_bytes(corpus, "pdf_single_column.pdf")
    for enabled in (True, False):
        monkeypatch.setattr(settings, "RAG_OCR_ENABLED", enabled)
        monkeypatch.setattr(settings, "RAG_OCR_PROVIDER", "tesseract")
        parsed = parse_uploaded_document(
            file_bytes, "pdf_single_column.pdf", "application/pdf"
        )
        assert parsed.metadata.get("used_ocr") is False
        assert "第一章 索引构建" in parsed.text
        assert "第三章 故障排查" in parsed.text


# ---------------------------------------------------------------------------
# 损坏 / 空 / 不支持扩展名
# ---------------------------------------------------------------------------


def test_empty_pdf_raises_document_parse_error() -> None:
    """空文件必须明确报错，不能静默返回空知识。"""
    from app.rag.document_parsers.base import DocumentParseError
    from app.rag.document_parsers.service import parse_uploaded_document

    with pytest.raises(DocumentParseError, match="文件解析失败"):
        parse_uploaded_document(b"", "broken_empty.pdf", "application/pdf")


def test_truncated_pdf_raises_document_parse_error(corpus: Path) -> None:
    """结构损坏的 PDF 必须明确报错。"""
    from app.rag.document_parsers.base import DocumentParseError
    from app.rag.document_parsers.service import parse_uploaded_document

    with pytest.raises(DocumentParseError, match="文件解析失败"):
        parse_uploaded_document(
            _sample_bytes(corpus, "broken_truncated.pdf"),
            "broken_truncated.pdf",
            "application/pdf",
        )


def test_unsupported_extension_raises_unsupported_type(corpus: Path) -> None:
    """不支持的扩展名必须抛 UnsupportedDocumentTypeError 并给出支持列表。"""
    from app.rag.document_parsers.base import UnsupportedDocumentTypeError
    from app.rag.document_parsers.service import parse_uploaded_document

    with pytest.raises(UnsupportedDocumentTypeError, match="当前仅支持"):
        parse_uploaded_document(
            _sample_bytes(corpus, "unsupported.bin"),
            "unsupported.bin",
            "application/octet-stream",
        )


def test_negative_samples_are_expected_failures_in_report(report: dict) -> None:
    """三个负样本在报告里应记为 expected_failure，且不进入失败清单。"""
    names = [item["name"] for item in report["samples"] if item["status"] == "expected_failure"]
    assert set(names) == {"broken_empty.pdf", "broken_truncated.pdf", "unsupported.bin"}
    assert report["failures"] == []


# ---------------------------------------------------------------------------
# 双栏阅读顺序
# ---------------------------------------------------------------------------


def test_two_column_pdf_reading_order_is_correct(report: dict) -> None:
    """双栏样本：左栏全部行必须整体先于右栏全部行。"""
    entry = _entry(report, "pdf_twocolumn.pdf")
    assert entry["status"] == "pass"
    assert entry["reading_order_correct"] is True
    assert entry["order_score"] == 1.0
    positions = entry["order_positions"]
    # positions[0] 是标题，其后 3 个左栏行、再 3 个右栏行。
    left, right = positions[1:4], positions[4:7]
    assert max(left) < min(right), f"左栏位置 {left} 未整体先于右栏 {right}"


def test_two_column_interleaved_is_quantified_as_limitation(report: dict) -> None:
    """逐行交替写入的双栏 PDF：顺序错乱必须被量化并记为已知缺口。"""
    entry = _entry(report, "pdf_twocolumn_interleaved.pdf")
    assert entry["status"] == "known_limitation"
    assert entry["reading_order_correct"] is False
    assert entry["order_score"] is not None and entry["order_score"] < 1.0
    assert entry["name"] in [item["name"] for item in report["known_limitations"]]
    assert "列聚类" in (entry["known_gap"] or "")


def test_order_metric_detects_shuffled_block_sequence() -> None:
    """顺序判定函数本身必须能识别错乱序列（防止判定恒真的退化）。"""
    from app.rag.document_parsers.base import ParsedBlock

    expected = ["标题", "左栏第一行", "左栏第二行", "右栏第一行", "右栏第二行"]

    def blocks_of(texts: list[str]) -> list[ParsedBlock]:
        return [ParsedBlock(type="text_block", text=text, order=index) for index, text in enumerate(texts)]

    correct = _report_mod.evaluate_order(blocks_of(expected), expected)
    assert correct["reading_order_correct"] is True
    assert correct["order_score"] == 1.0

    shuffled = _report_mod.evaluate_order(
        blocks_of(["标题", "左栏第一行", "右栏第一行", "左栏第二行", "右栏第二行"]), expected
    )
    assert shuffled["reading_order_correct"] is False
    assert shuffled["order_score"] is not None and shuffled["order_score"] < 1.0


def test_single_column_multipage_reading_order_is_correct(report: dict) -> None:
    """单栏多页样本：跨页顺序必须保持。"""
    entry = _entry(report, "pdf_single_column.pdf")
    assert entry["reading_order_correct"] is True
    assert entry["order_score"] == 1.0


# ---------------------------------------------------------------------------
# 表格 / 各格式覆盖
# ---------------------------------------------------------------------------


def test_coverage_metric_detects_missing_fragment() -> None:
    """覆盖率函数本身必须能算出未命中比例（防止覆盖率恒 1.0 的退化）。"""
    result = _report_mod.measure_coverage("标题与正文内容", ["标题", "正文", "表格单元格"])
    assert result["coverage"] == round(2 / 3, 4)
    assert result["missing_fragments"] == ["表格单元格"]

    complete = _report_mod.measure_coverage("标题与正文内容", ["标题", "正文"])
    assert complete["coverage"] == 1.0
    assert complete["missing_fragments"] == []


def test_table_pdf_coverage_is_complete(report: dict) -> None:
    """含表格 PDF：全部单元格取值都要命中。"""
    entry = _entry(report, "pdf_table.pdf")
    assert entry["status"] == "pass"
    assert entry["coverage"] == 1.0
    assert entry["missing_fragments"] == []


def test_docx_table_gap_is_recorded_not_hidden(report: dict) -> None:
    """DOCX 表格缺口必须被记录：标题/正文命中，表格单元格缺失。"""
    entry = _entry(report, "docx_headings_table.docx")
    assert entry["status"] == "known_limitation"
    assert entry["coverage"] is not None and entry["coverage"] < 1.0
    assert "角色" in entry["missing_fragments"]
    assert "五个工作日" in entry["missing_fragments"]
    assert "客服系统接入说明" not in entry["missing_fragments"]
    assert entry["name"] in [item["name"] for item in report["known_limitations"]]


@pytest.mark.parametrize(
    "name",
    [
        "xlsx_multi_sheet.xlsx",
        "pptx_notes.pptx",
        "md_code_fence.md",
        "txt_plain.txt",
        "pdf_single_column.pdf",
        "pdf_twocolumn.pdf",
        "pdf_table.pdf",
    ],
)
def test_positive_samples_reach_full_coverage(report: dict, name: str) -> None:
    """各格式正样本覆盖率需达到建议门槛（1.0）。"""
    entry = _entry(report, name)
    assert entry["status"] == "pass", f"{name} 状态为 {entry['status']}：{entry['failure_reason']}"
    assert entry["coverage"] == 1.0, f"{name} 缺失片段：{entry['missing_fragments']}"


def test_ascii_box_diagram_survives_text_parsing(corpus: Path) -> None:
    """ASCII 盒图在 md/txt 解析后必须完整保留（呼应 RAG-042 展示诉求）。"""
    from app.rag.document_parsers.service import parse_uploaded_document

    for name, content_type in (("md_code_fence.md", "text/markdown"), ("txt_plain.txt", "text/plain")):
        parsed = parse_uploaded_document(_sample_bytes(corpus, name), name, content_type)
        assert "+-------------+" in parsed.text
        assert "|  解析阶段   | -----> |  切分阶段   |" in parsed.text
        assert "检索与重排阶段" in parsed.text


# ---------------------------------------------------------------------------
# 报告结构与质量门禁
# ---------------------------------------------------------------------------

_REQUIRED_SAMPLE_FIELDS = (
    "name",
    "format",
    "kind",
    "expectation_mode",
    "status",
    "parsed",
    "error_type",
    "error_message",
    "error_code",
    "failure_reason",
    "coverage",
    "coverage_hit",
    "coverage_total",
    "missing_fragments",
    "order_expected",
    "order_matched",
    "order_score",
    "reading_order_correct",
    "order_positions",
    "block_count",
    "text_length",
    "used_ocr",
)


def test_report_structure_has_all_required_fields(report: dict) -> None:
    """报告每个样本条目都要带齐覆盖率/顺序/失败原因字段。"""
    for entry in report["samples"]:
        for field in _REQUIRED_SAMPLE_FIELDS:
            assert field in entry, f"{entry['name']} 缺少字段 {field}"
    assert report["samples"], "报告不应为空"
    for key in ("failures", "dependency_missing_list", "known_limitations", "totals", "gate"):
        assert key in report


def test_report_totals_are_consistent(report: dict) -> None:
    """合计数量需与样本条目一致。"""
    totals = report["totals"]
    assert totals["samples"] == len(report["samples"])
    assert (
        totals["passed"]
        + totals["failed"]
        + totals["dependency_missing"]
        + totals["known_limitation"]
        + totals["expected_failure"]
        == totals["samples"]
    )
    assert totals["failed"] == len(report["failures"])
    assert totals["dependency_missing"] == len(report["dependency_missing_list"])


def test_dependency_inventory_records_missing_items(report: dict) -> None:
    """依赖清单要如实记录缺失项；本机应缺失 tesseract / pytesseract / pdfplumber。"""
    dependencies = report["dependencies"]
    assert set(dependencies["modules"]) >= {
        "pymupdf",
        "python_docx",
        "openpyxl",
        "python_pptx",
        "pypdf",
        "pytesseract",
        "pdfplumber",
    }
    assert isinstance(dependencies["binaries"]["tesseract"], bool)
    assert dependencies["modules"]["pymupdf"] is True
    if dependencies["binaries"]["tesseract"] is False:
        assert "binary:tesseract" in dependencies["missing"]
        assert "pdfplumber" in dependencies["missing"]
        assert "pytesseract" in dependencies["missing"]


def test_quality_gate_is_proposed_not_locked(report: dict) -> None:
    """质量门槛必须标注「尚未经人工评审锁定」。"""
    gate = report["gate"]
    assert gate["status"] == "proposed_not_locked"
    assert "coverage_min" in gate and "order_correct_required" in gate
    assert "failure_rate_max" in gate
    assert "尚未经人工评审锁定" in gate["note"]


def test_gate_not_met_while_dependency_missing(report: dict) -> None:
    """存在未验证（依赖缺失）样本时，不得宣称满足门槛。"""
    if report["dependency_missing_list"]:
        assert report["gate"]["meets_gate"] is False


def test_json_report_can_be_written(tmp_path: Path, report: dict) -> None:
    """JSON 报告可落盘，且拒绝覆盖冻结基线报告名。"""
    target = tmp_path / "parse-quality-test.json"
    written = _report_mod.write_json(report, target)
    assert written.exists()
    assert '"card"' in written.read_text(encoding="utf-8")

    frozen = tmp_path / _report_mod.FROZEN_BASELINE_NAME
    with pytest.raises(SystemExit):
        _report_mod.write_json(report, frozen)
