"""RAG-033「多格式解析与 OCR 质量基线」测试。

覆盖三类证据：

1. **真实依赖样本报告**：单栏/双栏/表格 PDF、DOCX、XLSX、PPTX、Markdown/TXT
   的覆盖率与阅读顺序（样本由 ``scripts/generate_parse_corpus.py`` 真实生成）。
2. **依赖失败行为**：OCR 依赖缺失时的错误类型与文案、是否影响非 OCR 文档、
   空文件/损坏文件/不支持扩展名的失败行为。
3. **报告结构**：覆盖率、顺序、失败清单字段齐全，门禁按 gate.v1 已锁定。

本机事实：未安装 ``pytesseract`` / ``pdfplumber``，也没有 tesseract 二进制，
因此**中文 OCR 质量在本机无法用真实依赖验证**，相关断言只验证「依赖缺失时的
失败行为被正确标记」，不假装 OCR 已通过。
"""

from __future__ import annotations

import importlib.util
import json
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


def _manifest_sample(manifest: dict, name: str) -> dict:
    """按样本名取 manifest 条目。"""
    for sample in manifest["samples"]:
        if sample["name"] == name:
            return sample
    raise AssertionError(f"manifest 中缺少样本：{name}")


@pytest.fixture
def corpus_copy(tmp_path: Path, corpus: Path, monkeypatch: pytest.MonkeyPatch):
    """返回一个「复制语料到临时目录并按需改 manifest」的工厂。

    用于构造**门禁真的会失败**的输入（H-01/H-02 的构造性反例）：
    不污染仓库语料，只把补丁后的目录挂到脚本的 ``CORPUS_DIR`` 上。
    """

    def _make(mutate=None) -> Path:
        dest = tmp_path / "corpus-copy"
        dest.mkdir(parents=True, exist_ok=True)
        for path in corpus.iterdir():
            shutil.copy2(path, dest / path.name)
        manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
        if mutate is not None:
            mutate(manifest, dest)
        (dest / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        monkeypatch.setattr(_report_mod, "CORPUS_DIR", dest)
        return dest

    return _make


def _build(corpus_copy, mutate) -> dict:
    """用补丁后的语料重建报告。"""
    corpus_copy(mutate)
    return _report_mod.build_report(ocr_provider="tesseract")


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
    """各格式正样本覆盖率需达到门禁 G2 门槛。"""
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
        + totals["gap_not_reproduced"]
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


def test_quality_gate_is_locked(report: dict) -> None:
    """门禁必须为已锁定的 gate.v2，并带齐锁定人/日期/六条规则。"""
    gate = report["gate"]
    assert gate["status"] == "locked"
    assert gate["version"] == "gate.v2"
    assert gate["locked_by"] == "批次评审（Agent 代执行人工评审）"
    assert gate["locked_at"] == "2026-10-07"
    assert set(gate["rules"]) == {"G1", "G2", "G3", "G4", "G5", "G6"}
    assert gate["coverage_min"] == 0.95
    assert gate["order_score_min"] == 0.95
    assert gate["failure_rate_max"] == 0.0


# ---------------------------------------------------------------------------
# H-01：G1 与「未登记 gap 即判 fail」的构造性反例
# ---------------------------------------------------------------------------


def test_unregistered_coverage_break_blocks_gate(corpus_copy) -> None:
    """删掉 docx 的 known_gap：阈值突破不再豁免 → fail，G1/G2 必须亮灯。"""
    def mutate(manifest: dict, dest: Path) -> None:
        _manifest_sample(manifest, "docx_headings_table.docx")["known_gap"] = None

    report = _build(corpus_copy, mutate)
    entry = _entry(report, "docx_headings_table.docx")
    assert entry["status"] == "fail"
    assert entry["status"] != "known_limitation"
    assert report["gate"]["checks"]["G1"]["passed"] is False
    assert "docx_headings_table.docx" in report["gate"]["checks"]["G1"]["violations"]
    assert report["gate"]["checks"]["G2"]["passed"] is False
    assert report["gate"]["meets_gate"] is False


def test_unregistered_order_break_blocks_gate(corpus_copy) -> None:
    """删掉双栏交错样本的 known_gap：顺序突破不再豁免 → fail，G1/G3 亮灯。"""
    def mutate(manifest: dict, dest: Path) -> None:
        _manifest_sample(manifest, "pdf_twocolumn_interleaved.pdf")["known_gap"] = None

    report = _build(corpus_copy, mutate)
    entry = _entry(report, "pdf_twocolumn_interleaved.pdf")
    assert entry["status"] == "fail"
    assert report["gate"]["checks"]["G1"]["passed"] is False
    assert report["gate"]["checks"]["G3"]["passed"] is False
    assert "pdf_twocolumn_interleaved.pdf" in report["gate"]["checks"]["G3"]["violations"]
    assert report["gate"]["meets_gate"] is False


def test_injected_unreachable_fragment_blocks_gate(corpus_copy) -> None:
    """给表格 PDF 加一个不可能命中的期望片段 → 未登记覆盖失败。"""
    def mutate(manifest: dict, dest: Path) -> None:
        _manifest_sample(manifest, "pdf_table.pdf")["expected_fragments"].append(
            "这个片段在样本里根本不存在"
        )

    report = _build(corpus_copy, mutate)
    entry = _entry(report, "pdf_table.pdf")
    assert entry["status"] == "fail"
    assert entry["coverage"] is not None and entry["coverage"] < 0.95
    assert "pdf_table.pdf" in report["gate"]["checks"]["G1"]["violations"]
    assert report["gate"]["checks"]["G2"]["passed"] is False


def test_reversed_expected_order_blocks_gate(corpus_copy) -> None:
    """反转单栏样本的期望顺序 → 未登记顺序失败，G1/G3 拦。"""
    def mutate(manifest: dict, dest: Path) -> None:
        sample = _manifest_sample(manifest, "pdf_single_column.pdf")
        sample["expected_order"] = list(reversed(sample["expected_order"]))

    report = _build(corpus_copy, mutate)
    entry = _entry(report, "pdf_single_column.pdf")
    assert entry["status"] == "fail"
    assert entry["order_score"] == 0.0
    assert report["gate"]["checks"]["G3"]["passed"] is False
    assert report["gate"]["meets_gate"] is False


def test_negative_sample_silent_pass_is_failure(corpus_copy) -> None:
    """把空文件负样本换成合法 PDF → 必须判 fail「静默通过」。"""
    def mutate(manifest: dict, dest: Path) -> None:
        shutil.copy2(dest / "pdf_table.pdf", dest / "broken_empty.pdf")

    report = _build(corpus_copy, mutate)
    entry = _entry(report, "broken_empty.pdf")
    assert entry["status"] == "fail"
    assert entry["status"] != "expected_failure"
    assert "静默通过" in (entry["failure_reason"] or "")
    assert "broken_empty.pdf" in report["gate"]["checks"]["G1"]["violations"]


def test_negative_sample_wrong_error_type_is_failure(corpus_copy) -> None:
    """负样本声明的错误类型与实际不符 → 判 fail 而非 expected_failure。"""
    def mutate(manifest: dict, dest: Path) -> None:
        _manifest_sample(manifest, "unsupported.bin")["expected_error"] = "DocumentParseError"

    report = _build(corpus_copy, mutate)
    entry = _entry(report, "unsupported.bin")
    assert entry["status"] == "fail"
    assert entry["status"] != "expected_failure"
    assert "未按预期错误类型失败" in (entry["failure_reason"] or "")
    assert "unsupported.bin" in report["gate"]["checks"]["G1"]["violations"]


# ---------------------------------------------------------------------------
# H-02：削弱 manifest 期望不得让已登记缺口静默变 pass
# ---------------------------------------------------------------------------


def test_weakened_expectation_does_not_silently_pass(corpus_copy) -> None:
    """只删期望片段、保留 known_gap：不得 pass，必须判为需人工注销的阻断项。"""
    table_cells = {"角色", "职责", "时限", "实施", "联调", "五个工作日"}

    def mutate(manifest: dict, dest: Path) -> None:
        sample = _manifest_sample(manifest, "docx_headings_table.docx")
        sample["expected_fragments"] = [
            frag for frag in sample["expected_fragments"] if frag not in table_cells
        ]

    report = _build(corpus_copy, mutate)
    entry = _entry(report, "docx_headings_table.docx")

    # 削弱期望后覆盖率确实回到 1.0，但门禁不能因此放行。
    assert entry["coverage"] == 1.0
    assert entry["status"] == "gap_not_reproduced"
    assert entry["status"] != "pass"
    assert entry["status"] != "known_limitation"
    assert entry["known_gap"], "known_gap 仍在 manifest 里，必须人工注销"
    assert "docx_headings_table.docx" in report["gate"]["checks"]["G1"]["violations"]
    assert report["gate"]["checks"]["G1"]["passed"] is False
    assert report["gate"]["meets_gate"] is False
    assert entry["name"] not in [item["name"] for item in report["samples"] if item["status"] == "pass"]


def test_gap_not_reproduced_is_listed_separately(report: dict) -> None:
    """当前语料不应产生 gap_not_reproduced（两条缺口都还在复现）。"""
    assert report["gate"]["checks"]["G1"]["passed"] is True
    assert report["gap_not_reproduced_list"] == []
    assert report["totals"]["gap_not_reproduced"] == 0


def test_gate_g1_no_unregistered_failure(report: dict) -> None:
    """G1：未登记的失败必须为 0。"""
    check = report["gate"]["checks"]["G1"]
    assert check["passed"] is True
    assert check["violations"] == []
    assert report["failures"] == []


def test_gate_g2_g3_exemptions_must_be_registered(report: dict) -> None:
    """G2/G3：低于门槛的样本必须登记 known_gap，且豁免项不得计入 passed。"""
    gate = report["gate"]
    for rule in ("G2", "G3"):
        assert gate["checks"][rule]["passed"] is True, f"{rule} 命中：{gate['checks'][rule]['violations']}"
    for item in report["known_limitations"]:
        assert item["known_gap"], f"{item['name']} 豁免但 manifest 未登记 known_gap"
        assert item["name"] not in [entry["name"] for entry in report["samples"] if entry["status"] == "pass"]
    # 当前两条豁免项：双栏交错（顺序 0.8571）与 DOCX 表格（覆盖 0.40）
    exempt_names = {item["name"] for item in report["known_limitations"]}
    assert "pdf_twocolumn_interleaved.pdf" in exempt_names
    assert "docx_headings_table.docx" in exempt_names


def test_gate_g4_blocks_while_dependency_missing(report: dict) -> None:
    """G4：存在 dependency_missing 样本时整份报告不通过，阻断原因必须指向 G4。"""
    if report["dependency_missing_list"]:
        gate = report["gate"]
        assert gate["checks"]["G4"]["passed"] is False
        assert "G4" in gate["blocking_rules"]
        assert gate["meets_gate"] is False
        assert "pdf_scanned.pdf" in gate["checks"]["G4"]["violations"]


def test_gate_g5_no_verification_claims(report: dict) -> None:
    """G5：报告不得出现「OCR 质量已验证」类表述；依赖缺失样本不得计入覆盖/顺序统计。"""
    gate = report["gate"]
    assert gate["checks"]["G5"]["passed"] is True
    assert _report_mod.scan_forbidden_phrases(report) == []
    for item in report["dependency_missing_list"]:
        assert item["coverage"] is None
        assert item["order_score"] is None


def test_gate_g5_blocks_when_report_claims_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    """M-01：报告里出现禁用表述时，必须走到门禁并阻断（端到端，不只测扫描函数）。"""
    monkeypatch.setitem(_report_mod.LOCKED_GATE, "note", "OCR 质量已验证")
    polluted = _report_mod.build_report(ocr_provider="tesseract")
    assert polluted["gate"]["checks"]["G5"]["passed"] is False
    assert "G5" in polluted["gate"]["blocking_rules"]
    assert polluted["gate"]["meets_gate"] is False


def test_gate_g5_unit_flags_dependency_missing_with_stats() -> None:
    """M-04：门禁层直接判定「dependency_missing 却带覆盖率/顺序」为违规。"""
    results = [
        {
            "name": "pdf_scanned.pdf",
            "status": "dependency_missing",
            "coverage": 1.0,
            "order_score": None,
            "kind": "ocr",
            "ocr_provider": None,
        }
    ]
    gate = _report_mod.evaluate_gate(results, failure_rate=0.0)
    assert gate["checks"]["G5"]["passed"] is False
    assert gate["checks"]["G5"]["violations"] == ["pdf_scanned.pdf"]
    assert "G5" in gate["blocking_rules"]


def test_gate_g6_uses_measured_failure_rate() -> None:
    """M-03：failure_rate_max 必须被规则真正引用。"""
    clean = _report_mod.evaluate_gate([], failure_rate=0.0)
    assert clean["checks"]["G6"]["passed"] is True

    dirty = _report_mod.evaluate_gate([], failure_rate=0.5)
    assert dirty["checks"]["G6"]["passed"] is False
    assert "G6" in dirty["blocking_rules"]
    assert dirty["meets_gate"] is False


def test_gate_g6_passes_on_current_corpus(report: dict) -> None:
    """当前语料失败率为 0，G6 通过。"""
    assert report["gate"]["measured_failure_rate"] == 0.0
    assert report["gate"]["checks"]["G6"]["passed"] is True


# ---------------------------------------------------------------------------
# M-02：ocr_probe 的 used_mock / path 必须从实际生效的 provider 派生
# ---------------------------------------------------------------------------


def test_ocr_probe_is_derived_from_real_provider(report: dict) -> None:
    """真实运行下 provider_class 必须落在 app.rag.ocr.* 白名单内。"""
    probe = report["ocr_probe"]
    assert probe["used_mock"] is False
    assert probe["provider_class"] == "app.rag.ocr.tesseract.TesseractOcrProvider"
    assert probe["provider_class"].startswith(probe["real_provider_module_prefix"])
    assert probe["real_provider_module_prefix"] == "app.rag.ocr."
    assert probe["observed_provider"] is None  # 依赖缺失，未产出任何 OCR 文本
    assert "app.rag.ocr.tesseract" in probe["path"]


def test_used_mock_is_true_when_factory_provider_is_faked(monkeypatch: pytest.MonkeyPatch) -> None:
    """工厂被换成假 provider 时，不得再声明走了真实路径。"""
    class _FakeProvider:
        def extract_pdf_text(self, file_bytes: bytes):  # pragma: no cover - 不会被调用
            raise AssertionError("不应调用假 provider 的 OCR")

    monkeypatch.setattr("app.rag.ocr.factory.get_ocr_provider", lambda: _FakeProvider())
    probe = _report_mod.build_ocr_probe([], ocr_provider="tesseract")
    assert probe["used_mock"] is True
    assert probe["provider_class"].startswith("app.rag.ocr.") is False


def test_used_mock_is_true_when_observed_provider_is_faked() -> None:
    """反例 CE-11b：解析器实际用了假 provider（自报名不在白名单）也要识别出来。"""
    results = [{"kind": "ocr", "ocr_provider": "fake", "name": "pdf_scanned.pdf"}]
    probe = _report_mod.build_ocr_probe(results, ocr_provider="tesseract")
    assert probe["observed_provider"] == "fake"
    assert probe["used_mock"] is True


def test_used_mock_true_blocks_gate_via_g5() -> None:
    """used_mock=True 必须落到门禁（G5），不能只是个字段。"""
    report = {
        "ocr_probe": {"used_mock": True, "provider_class": "fake.FakeProvider"},
        "gate": _report_mod.evaluate_gate([], failure_rate=0.0),
    }
    _report_mod.apply_report_level_checks(report)
    assert report["gate"]["checks"]["G5"]["passed"] is False
    assert "G5" in report["gate"]["blocking_rules"]


# ---------------------------------------------------------------------------
# L-02：报告不静默覆盖 + 与脚本输出一致
# ---------------------------------------------------------------------------


def test_write_json_refuses_silent_overwrite(tmp_path: Path, report: dict) -> None:
    """同名报告默认拒绝覆盖；显式 --force 才允许。"""
    target = tmp_path / "parse-quality-test.json"
    _report_mod.write_json(report, target)
    with pytest.raises(SystemExit, match="拒绝静默覆盖"):
        _report_mod.write_json(report, target)
    assert _report_mod.write_json(report, target, force=True).exists()


def test_committed_report_matches_script_output(report: dict) -> None:
    """落盘报告必须与脚本输出逐字段一致（除 generated_at），防止评审看到过期数据。"""
    candidates = sorted((_REPO_ROOT / "evals" / "reports").glob("parse-quality-*.json"))
    if not candidates:  # pragma: no cover - 首次提交前无报告
        pytest.skip("尚无落盘报告")
    committed = json.loads(candidates[-1].read_text(encoding="utf-8"))
    committed.pop("generated_at", None)
    fresh = dict(report)
    fresh.pop("generated_at", None)
    assert committed == fresh


def test_manifest_known_limitation_requires_known_gap(corpus: Path) -> None:
    """L-03：expectation_mode=known_limitation 与 known_gap 必须同真同假，避免死标注。"""
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    for sample in manifest["samples"]:
        declared = sample.get("expectation_mode") == "known_limitation"
        assert declared == bool(sample.get("known_gap")), f"{sample['name']} 标注不一致"


def test_g5_scan_detects_forbidden_claim(report: dict) -> None:
    """G5 扫描函数本身必须能识别禁用表述（防止扫描恒空）。"""
    polluted = json.loads(json.dumps(report))
    polluted["samples"][0]["failure_reason"] = "OCR 质量已验证"
    assert _report_mod.scan_forbidden_phrases(polluted) == ["OCR 质量已验证"]


def test_json_report_can_be_written(tmp_path: Path, report: dict) -> None:
    """JSON 报告可落盘，且拒绝覆盖冻结基线报告名。"""
    target = tmp_path / "parse-quality-test.json"
    written = _report_mod.write_json(report, target)
    assert written.exists()
    assert '"card"' in written.read_text(encoding="utf-8")

    frozen = tmp_path / _report_mod.FROZEN_BASELINE_NAME
    with pytest.raises(SystemExit):
        _report_mod.write_json(report, frozen)
