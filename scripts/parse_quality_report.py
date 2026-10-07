"""RAG-033「多格式解析与 OCR 质量基线」样本评估报告。

对 ``evals/corpus/parsing/`` 中的**真实样本**逐个跑真实解析器
（``app/rag/document_parsers/``），产出覆盖率、阅读顺序、块数与失败原因。

设计要点
--------
1. **不用 Mock**：所有样本与解析过程都走真实依赖；OCR 样本强制走
   ``app/rag/ocr/factory.py`` 的真实 provider 路径。
2. **依赖缺失不等于通过**：provider 因缺少 tesseract 二进制/语言包而失败时，
   状态标记为 ``dependency_missing``，绝不写成 ``pass``。
3. **顺序判定规则**（见 :func:`evaluate_order`）：把解析块按解析器输出的
   ``order`` 升序排列，取每个期望片段首次命中的块下标；期望序列下标严格递增
   视为阅读顺序正确，逆序对比例用于量化跨栏错乱程度。

用法::

    python scripts/parse_quality_report.py                 # 打印报告
    python scripts/parse_quality_report.py --json          # 落 JSON 报告
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CORPUS_DIR = REPO_ROOT / "evals" / "corpus" / "parsing"
REPORT_DIR = REPO_ROOT / "evals" / "reports"
FROZEN_BASELINE_NAME = "rag-v0.1-baseline-20260919.json"

# 判定为「依赖缺失」的 OCR 错误码；命中后状态一律记为 dependency_missing。
DEPENDENCY_MISSING_ERROR_CODES = frozenset(
    {
        "ocr_tesseract_missing",
        "ocr_tesseract_language_missing",
        "ocr_pdf_renderer_missing",
    }
)
DEPENDENCY_MISSING_MESSAGE_MARK = "依赖缺失"

# 建议门槛（**尚未经人工评审锁定**，仅作为评审输入）。
PROPOSED_GATE = {
    "coverage_min": 0.95,
    "order_correct_required": True,
    "failure_rate_max": 0.0,
    "status": "proposed_not_locked",
    "note": (
        "门槛为建议值，尚未经人工评审锁定（proposed，not locked）；"
        "本报告中不作为判定通过与否的依据，需评审结论后再锁定。"
    ),
}

CONTENT_TYPE_BY_FORMAT = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "md": "text/markdown",
    "txt": "text/plain",
    "bin": "application/octet-stream",
}

# 依赖清单探测：模块名 + 可选的二进制名。
DEPENDENCY_MODULES = {
    "pymupdf": "fitz",
    "python_docx": "docx",
    "openpyxl": "openpyxl",
    "python_pptx": "pptx",
    "pypdf": "pypdf",
    "pytesseract": "pytesseract",
    "pdfplumber": "pdfplumber",
}
DEPENDENCY_BINARIES = {"tesseract": "tesseract"}


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def normalize(text: str) -> str:
    """归一化文本：去掉全部空白，便于跨行/跨块片段匹配。"""
    return re.sub(r"\s+", "", text or "")


def load_manifest() -> dict:
    """读取样本集期望标注。"""
    manifest_path = CORPUS_DIR / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(
            f"样本集缺失：{manifest_path}。请先运行 python scripts/generate_parse_corpus.py"
        )
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def probe_dependencies() -> dict:
    """探测真实依赖的可用情况（模块 + 二进制），如实记录缺失项。"""
    modules: dict[str, bool] = {}
    for name, module in DEPENDENCY_MODULES.items():
        try:
            importlib.import_module(module)
            modules[name] = True
        except Exception:  # noqa: BLE001 - 探测用途，任何导入失败都记为缺失
            modules[name] = False
    binaries = {name: shutil.which(binary) is not None for name, binary in DEPENDENCY_BINARIES.items()}
    return {
        "modules": modules,
        "binaries": binaries,
        "missing": sorted(
            [name for name, ok in modules.items() if not ok]
            + [f"binary:{name}" for name, ok in binaries.items() if not ok]
        ),
    }


def measure_coverage(parsed_text: str, fragments: list[str]) -> dict:
    """计算期望文本片段命中比例。"""
    haystack = normalize(parsed_text)
    if not fragments:
        return {
            "coverage": None,
            "coverage_hit": 0,
            "coverage_total": 0,
            "missing_fragments": [],
        }
    missing = [frag for frag in fragments if normalize(frag) not in haystack]
    hit = len(fragments) - len(missing)
    return {
        "coverage": round(hit / len(fragments), 4),
        "coverage_hit": hit,
        "coverage_total": len(fragments),
        "missing_fragments": missing,
    }


def evaluate_order(blocks: list, expected_order: list[str]) -> dict:
    """按块序列判定阅读顺序。

    规则：
    1. 块按解析器输出的 ``order`` 升序排列，得到实际阅读序列；
    2. 每个期望片段取首次命中的块下标，未命中记为 ``None``；
    3. 所有片段都命中且下标序列严格递增 ⇒ ``reading_order_correct = True``；
    4. ``order_score = 1 - 逆序对数 / C(命中数, 2)``，用于量化错乱程度。

    对双栏样本而言，「左栏全部行先于右栏全部行」即期望序列，因此任何跨栏
    （左右交替）排列都会产生逆序对，从而被量化出来。
    """
    if not expected_order:
        return {
            "order_expected": 0,
            "order_matched": 0,
            "order_score": None,
            "reading_order_correct": None,
            "order_positions": [],
        }
    ordered_blocks = sorted(blocks, key=lambda block: block.order)
    positions: list[int | None] = []
    for fragment in expected_order:
        needle = normalize(fragment)
        position: int | None = None
        for index, block in enumerate(ordered_blocks):
            if needle and needle in normalize(block.text):
                position = index
                break
        positions.append(position)

    matched = [pos for pos in positions if pos is not None]
    inversions = sum(
        1
        for i, left in enumerate(matched)
        for right in matched[i + 1 :]
        if left > right
    )
    pairs = len(matched) * (len(matched) - 1) // 2
    return {
        "order_expected": len(expected_order),
        "order_matched": len(matched),
        "order_score": round(1 - inversions / pairs, 4) if pairs else None,
        "reading_order_correct": (
            len(matched) == len(expected_order) and inversions == 0
        ),
        "order_positions": positions,
    }


def classify_error(exc: BaseException) -> tuple[str, str, str | None, str]:
    """把解析异常归类为状态、失败原因、错误码与错误类型。"""
    error_type = type(exc).__name__
    message = str(exc)
    error_code = getattr(exc, "error_code", None)
    if error_code in DEPENDENCY_MISSING_ERROR_CODES or DEPENDENCY_MISSING_MESSAGE_MARK in message:
        return (
            "dependency_missing",
            f"真实依赖缺失，无法在本机验证该路径：{message}",
            error_code,
            error_type,
        )
    return ("fail", message, error_code, error_type)


# ---------------------------------------------------------------------------
# 单样本评估
# ---------------------------------------------------------------------------


def evaluate_sample(sample: dict, *, ocr_provider: str) -> dict:
    """评估单个样本，返回结构化结果。"""
    from app.core.config import settings
    from app.rag.document_parsers.service import parse_uploaded_document

    name = sample["name"]
    path = CORPUS_DIR / name
    result: dict = {
        "name": name,
        "format": sample["format"],
        "kind": sample["kind"],
        "expectation_mode": sample["expectation_mode"],
        "expected_error": sample["expected_error"],
        "known_gap": sample.get("known_gap"),
        "parsed": False,
        "status": None,
        "error_type": None,
        "error_message": None,
        "error_code": None,
        "failure_reason": None,
        "coverage": None,
        "coverage_hit": 0,
        "coverage_total": 0,
        "missing_fragments": [],
        "order_expected": 0,
        "order_matched": 0,
        "order_score": None,
        "reading_order_correct": None,
        "order_positions": [],
        "block_count": 0,
        "text_length": 0,
        "used_ocr": False,
        "ocr_provider": None,
        "ocr_probe_mode": None,
    }

    file_bytes = path.read_bytes() if path.exists() else b""
    content_type = CONTENT_TYPE_BY_FORMAT.get(sample["format"])

    # OCR 样本：临时打开 OCR 开关，强制走真实 provider 路径（不是 Mock）。
    original = (settings.RAG_OCR_ENABLED, settings.RAG_OCR_PROVIDER)
    if sample["kind"] == "ocr":
        settings.RAG_OCR_ENABLED = True
        settings.RAG_OCR_PROVIDER = ocr_provider
        result["ocr_probe_mode"] = f"enabled:{ocr_provider}"
    try:
        parsed = parse_uploaded_document(file_bytes, name, content_type)
    except Exception as exc:  # noqa: BLE001 - 需要记录所有失败形态
        status, reason, error_code, error_type = classify_error(exc)
        result.update(
            parsed=False,
            status=status,
            error_type=error_type,
            error_message=str(exc),
            error_code=error_code,
            failure_reason=reason,
        )
        if (
            sample["expectation_mode"] == "must_fail"
            and sample["expected_error"] == error_type
        ):
            result["status"] = "expected_failure"
            result["failure_reason"] = f"按预期失败：{error_type}"
        elif sample["expectation_mode"] == "must_fail":
            result["failure_reason"] = (
                f"负样本未按预期错误类型失败：期望 {sample['expected_error']}，实际 {error_type}"
            )
        return result
    finally:
        settings.RAG_OCR_ENABLED, settings.RAG_OCR_PROVIDER = original

    result["parsed"] = True
    result["text_length"] = len(parsed.text)
    result["block_count"] = len(parsed.blocks)
    result["used_ocr"] = bool(parsed.metadata.get("used_ocr"))
    result["ocr_provider"] = parsed.metadata.get("ocr_provider")
    result.update(measure_coverage(parsed.text, sample["expected_fragments"]))
    result.update(evaluate_order(parsed.blocks, sample["expected_order"]))

    if sample["expectation_mode"] == "known_limitation":
        result["status"] = "known_limitation"
        result["failure_reason"] = sample.get("known_gap") or "已知解析缺口，指标仅作记录"
        return result

    problems: list[str] = []
    coverage = result["coverage"]
    if coverage is not None and coverage < PROPOSED_GATE["coverage_min"]:
        problems.append(
            f"覆盖率 {coverage} 低于建议门槛 {PROPOSED_GATE['coverage_min']}"
            f"（缺失片段：{result['missing_fragments']}）"
        )
    if sample["expected_order"] and not result["reading_order_correct"]:
        problems.append(
            f"阅读顺序不正确（命中 {result['order_matched']}/{result['order_expected']}，"
            f"顺序得分 {result['order_score']}）"
        )
    if sample["expectation_mode"] == "must_fail":
        result["status"] = "fail"
        result["failure_reason"] = "负样本未报错，属于静默通过"
        return result

    if problems:
        result["status"] = "fail"
        result["failure_reason"] = "；".join(problems)
    else:
        result["status"] = "pass"
    return result


# ---------------------------------------------------------------------------
# 报告组装
# ---------------------------------------------------------------------------


def build_report(*, ocr_provider: str) -> dict:
    """跑完整样本集并组装报告。"""
    manifest = load_manifest()
    results = [evaluate_sample(sample, ocr_provider=ocr_provider) for sample in manifest["samples"]]

    failures = [item for item in results if item["status"] == "fail"]
    dependency_missing = [item for item in results if item["status"] == "dependency_missing"]
    known_limitations = [item for item in results if item["status"] == "known_limitation"]
    expected_failures = [item for item in results if item["status"] == "expected_failure"]
    passed = [item for item in results if item["status"] == "pass"]

    scored = [item for item in results if item["status"] == "pass"]
    failure_rate = (
        round(len(failures) / len(scored + failures), 4) if (scored or failures) else 0.0
    )
    meets_gate = (
        not failures
        and not dependency_missing
        and failure_rate <= PROPOSED_GATE["failure_rate_max"]
    )
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "card": "RAG-033 多格式解析与 OCR 质量基线",
        "corpus_dir": str(CORPUS_DIR.relative_to(REPO_ROOT)),
        "corpus_version": manifest.get("version"),
        "cjk_font": manifest.get("cjk_font"),
        "cjk_font_is_system_font": manifest.get("cjk_font_is_system_font"),
        "cjk_font_note": manifest.get("cjk_font_note"),
        "ocr_probe": {
            "enabled_for_ocr_samples": True,
            "provider": ocr_provider,
            "path": "app.rag.ocr.factory.get_ocr_provider -> 真实 provider",
            "used_mock": False,
        },
        "dependencies": probe_dependencies(),
        "samples": results,
        "totals": {
            "samples": len(results),
            "passed": len(passed),
            "failed": len(failures),
            "dependency_missing": len(dependency_missing),
            "known_limitation": len(known_limitations),
            "expected_failure": len(expected_failures),
        },
        "failures": failures,
        "dependency_missing_list": dependency_missing,
        "known_limitations": known_limitations,
        "gate": {
            **PROPOSED_GATE,
            "measured_failure_rate": failure_rate,
            "meets_gate": meets_gate,
        },
    }


def render_report(report: dict) -> str:
    """渲染人类可读的报告文本。"""
    lines: list[str] = []
    lines.append(f"RAG-033 解析质量报告  生成于 {report['generated_at']}")
    lines.append(f"样本集：{report['corpus_dir']} (版本 {report['corpus_version']})")
    lines.append(f"CJK 字体：{report['cjk_font']}（系统字体={report['cjk_font_is_system_font']}）")
    missing = report["dependencies"]["missing"]
    lines.append(f"缺失依赖：{missing if missing else '无'}")
    lines.append("")
    header = f"{'样本':<34}{'格式':<7}{'状态':<20}{'覆盖':>8}{'顺序':>8}{'块数':>7}"
    lines.append(header)
    lines.append("-" * len(header))
    for item in report["samples"]:
        coverage = "-" if item["coverage"] is None else f"{item['coverage']:.2f}"
        order = "-" if item["order_score"] is None else f"{item['order_score']:.2f}"
        lines.append(
            f"{item['name']:<34}{item['format']:<7}{item['status']:<20}"
            f"{coverage:>8}{order:>8}{item['block_count']:>7}"
        )
    lines.append("")
    lines.append(
        "合计："
        + "  ".join(f"{key}={value}" for key, value in report["totals"].items())
    )
    lines.append("")
    lines.append("失败清单：")
    if report["failures"]:
        for item in report["failures"]:
            lines.append(f"  - {item['name']}: {item['failure_reason']}")
    else:
        lines.append("  （无）")
    lines.append("")
    lines.append("依赖缺失清单（未验证，不等于通过）：")
    if report["dependency_missing_list"]:
        for item in report["dependency_missing_list"]:
            lines.append(
                f"  - {item['name']}: error_code={item['error_code']} "
                f"error_type={item['error_type']} reason={item['failure_reason']}"
            )
    else:
        lines.append("  （无）")
    lines.append("")
    lines.append("已知缺口（指标仅记录，不计入失败）：")
    for item in report["known_limitations"]:
        lines.append(
            f"  - {item['name']}: 覆盖={item['coverage']} 顺序={item['order_score']} "
            f"原因={item['failure_reason']}"
        )
    lines.append("")
    gate = report["gate"]
    lines.append(
        f"建议门槛（状态={gate['status']}，未锁定）：覆盖率≥{gate['coverage_min']}、"
        f"顺序必须正确、失败率≤{gate['failure_rate_max']}；实测失败率={gate['measured_failure_rate']}、"
        f"是否满足={gate['meets_gate']}"
    )
    return "\n".join(lines)


def write_json(report: dict, target: Path) -> Path:
    """写入 JSON 报告；禁止覆盖冻结基线报告。"""
    if target.name == FROZEN_BASELINE_NAME:
        raise SystemExit(f"拒绝写入冻结基线报告：{target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="RAG-033 多格式解析与 OCR 质量基线报告")
    parser.add_argument("--json", action="store_true", help="同时落一份 JSON 报告到 evals/reports/")
    parser.add_argument("--json-path", default="", help="自定义 JSON 报告路径")
    parser.add_argument(
        "--ocr-provider",
        default="tesseract",
        help="OCR 样本强制使用的 provider（默认 tesseract，走本地真实 provider 路径）",
    )
    args = parser.parse_args(argv)

    report = build_report(ocr_provider=args.ocr_provider)
    print(render_report(report))

    if args.json or args.json_path:
        target = (
            Path(args.json_path)
            if args.json_path
            else REPORT_DIR / f"parse-quality-{datetime.now().strftime('%Y%m%d')}.json"
        )
        written = write_json(report, target)
        print(f"\nJSON 报告已写入：{written}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
