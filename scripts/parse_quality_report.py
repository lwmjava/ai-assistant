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

# 质量门禁（gate.v3，2026-10-07 由批次评审锁定）。
# 版本沿革：v1 初版五条规则；v2 补 G6 与 gap_not_reproduced 并写明确 G1/G5 覆盖范围；
# v3 把原 G5（一条文本对应三处实现）拆成 G5a/G5b/G5c 各自独立判定，**门槛值未改**。
# 修改门槛值或规则集合必须同时改 version 与 locked_at，避免静默改写已锁定门禁。
LOCKED_GATE = {
    "version": "gate.v3",
    "status": "locked",
    "locked_by": "批次评审（Agent 代执行人工评审）",
    "locked_at": "2026-10-07",
    "coverage_min": 0.95,
    "order_score_min": 0.95,
    "failure_rate_max": 0.0,
    "rules": {
        "G1": "未登记的失败与未注销的缺口必须为 0：任何 status=fail（不在 manifest "
        "expected_failure 里）或 status=gap_not_reproduced（manifest 仍登记 known_gap "
        "但本次阈值突破未复现，需人工注销）的样本，整份报告判不通过。",
        "G2": "覆盖率 coverage ≥ 0.95；低于门槛的样本必须登记在 manifest 的 known_gap 并出现在报告 "
        "known_limitations 中，登记后状态为 known_limitation（豁免门槛，但不得计入 passed）。",
        "G3": "阅读顺序 order_score ≥ 0.95，豁免规则同 G2。",
        "G4": "依赖缺失：存在任何 dependency_missing 样本时整份报告 meets_gate=False（即使其它全绿）。",
        "G5a": "不得用数据暗示验证过：dependency_missing 样本不得带 coverage / order_score "
        "数值（不得计入覆盖率或顺序统计）。判定在 evaluate_gate（样本级）。",
        "G5b": "不得用表述声称验证过：报告任何位置不得出现宣称 OCR 质量已经过验证的禁用表述。"
        "判定在 apply_report_level_checks（报告级，scan_forbidden_phrases）。",
        "G5c": "不得声称走了真实路径：ocr_probe.used_mock 必须为 False，未走真实 provider "
        "路径时不得声明走了真实路径。判定在 apply_report_level_checks（报告级）。",
        "G6": "失败率 measured_failure_rate ≤ 0.0（分母为 pass+fail 样本）。",
    },
    "note": (
        "本门禁已锁定（gate.v3）。G4 为阻断项：补齐 tesseract + chi_sim 语言包并重跑后"
        "才能解除，解除前本卡整体不算「质量通过」。"
        "v3 相对 v2 只把 G5 拆成 G5a/G5b/G5c（各自独立判定），门槛值未变。"
        "另：本报告中不含任何宣称 OCR 已被真实依赖验证的表述，"
        "中文 OCR 质量在本机未经验证。"
    ),
}

# 规则展示与派生顺序的唯一来源。blocking_rules / meets_gate 都按此顺序派生，
# 避免「checks 里多一条规则但没人重算结论」这类静默失效。
GATE_RULE_ORDER = ("G1", "G2", "G3", "G4", "G5a", "G5b", "G5c", "G6")

# G5b 禁止出现的表述（在整份报告序列化结果里扫描）。
FORBIDDEN_VERIFICATION_PHRASES = (
    "OCR 质量已验证",
    "已验证 OCR",
    "OCR 已验证",
    "中文 OCR 已通过",
    "OCR 质量通过",
)

# 真实 OCR provider 的识别依据：模块前缀 + provider 自报名称白名单。
# 用来让 ocr_probe.used_mock 从「实际生效的 provider」派生，而不是硬编码常量。
REAL_OCR_PROVIDER_MODULE_PREFIX = "app.rag.ocr."
REAL_OCR_PROVIDER_NAMES = frozenset({"tesseract", "cloud"})

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


def _gap_diagnosis(sample: dict, scenario: str) -> str:
    """生成**可诊断**的失败说明。

    必须包含三要素，否则「样本缺失 / 缺口未复现」这类失败无法定位：
    ① 样本 id（文件名）；② 需要人工退役的 ``known_gap`` 条目；
    ③ 下一步该做什么（补预期 or 退役缺口）。
    """
    name = sample["name"]
    gap = sample.get("known_gap")
    if scenario == "missing_file":
        head = f"样本文件缺失：{name}（manifest 已登记该样本，但 {CORPUS_DIR / name} 不存在）"
    else:
        head = f"样本 {name}：manifest 仍登记 known_gap，但本次阈值突破未复现"
    if gap:
        entry = f"· 需人工退役的 known_gap 条目：{gap}"
        action = (
            "· 下一步：确认是解析器已改进还是 manifest 期望被削弱；"
            "若是前者，从 manifest 删除该 known_gap 后重跑；"
            "若是后者，恢复原有 expected_fragments"
        )
    else:
        entry = "· 该样本未登记 known_gap，属未登记的门禁突破"
        action = "· 下一步：恢复样本文件并补齐 expected_fragments，或在 manifest 中同步删除该样本条目"
    return "\n".join((head, entry, action))


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
        "gap_note": None,
    }

    file_bytes = path.read_bytes() if path.exists() else b""
    content_type = CONTENT_TYPE_BY_FORMAT.get(sample["format"])

    # 样本文件缺失：直接判 fail 并给出可诊断信息（不静默当空文件解析）。
    if not path.exists():
        reason = _gap_diagnosis(sample, "missing_file")
        result.update(
            parsed=False,
            status="fail",
            error_type="CorpusSampleMissing",
            error_message=reason,
            failure_reason=reason,
        )
        return result

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

    problems: list[str] = []
    coverage = result["coverage"]
    if coverage is not None and coverage < LOCKED_GATE["coverage_min"]:
        problems.append(
            f"覆盖率 {coverage} 低于门禁 G2 门槛 {LOCKED_GATE['coverage_min']}"
            f"（缺失片段：{result['missing_fragments']}）"
        )
    order_score = result["order_score"]
    if sample["expected_order"] and order_score is not None and order_score < LOCKED_GATE["order_score_min"]:
        problems.append(
            f"阅读顺序得分 {order_score} 低于门禁 G3 门槛 {LOCKED_GATE['order_score_min']}"
            f"（命中 {result['order_matched']}/{result['order_expected']}）"
        )
    if result["reading_order_correct"] is False and order_score is None:
        problems.append("阅读顺序片段未全部命中，无法计算顺序得分")

    if sample["expectation_mode"] == "must_fail":
        result["status"] = "fail"
        result["failure_reason"] = "负样本未报错，属于静默通过"
        return result

    registered_gap = sample.get("known_gap")
    if problems:
        if registered_gap:
            # G2/G3 豁免：已登记 known_gap 的样本记为 known_limitation，
            # 豁免门槛但不得计入 passed。
            result["status"] = "known_limitation"
            result["failure_reason"] = f"{registered_gap}｜实测：{'；'.join(problems)}"
        else:
            result["status"] = "fail"
            result["failure_reason"] = f"未登记的门禁突破（G1/G2/G3）：{'；'.join(problems)}"
        return result

    if registered_gap:
        # manifest 仍登记 known_gap 但阈值突破未复现：可能是解析器真的修好了，
        # 也可能是把 manifest 期望改弱了（削弱期望与改进解析器在实测值上等价）。
        # 因此不得直接 pass，必须判为需人工注销 known_gap 的阻断项（G1）。
        result["status"] = "gap_not_reproduced"
        result["failure_reason"] = _gap_diagnosis(sample, "gap_not_reproduced")
        return result

    result["status"] = "pass"
    return result


# ---------------------------------------------------------------------------
# 报告组装
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 质量门禁（gate.v1，已锁定）
# ---------------------------------------------------------------------------


def _derive_blocking_rules(checks: dict) -> list[str]:
    """从 checks 派生阻断规则名（顺序固定为 :data:`GATE_RULE_ORDER`）。"""
    return [name for name in GATE_RULE_ORDER if name in checks and not checks[name]["passed"]]


def evaluate_gate(results: list[dict], *, failure_rate: float) -> dict:
    """按 gate.v3 的样本级规则逐条判定，返回门禁结果。

    G1 未登记的失败 + 未注销的缺口必须为 0；G2/G3 覆盖率与顺序门槛（登记
    known_gap 则豁免，但豁免项不得计入 passed）；G4 存在依赖缺失即整份不通过；
    G5a 依赖缺失样本不得计入覆盖/顺序统计；G6 失败率门槛。

    G5b（表述扫描）与 G5c（used_mock）是报告级规则，由
    :func:`apply_report_level_checks` 判定——**最终结论必须经它派生后才可发布**。
    """
    coverage_min = LOCKED_GATE["coverage_min"]
    order_min = LOCKED_GATE["order_score_min"]
    failure_rate_max = LOCKED_GATE["failure_rate_max"]

    def names(items: list[dict]) -> list[str]:
        return [item["name"] for item in items]

    # G1：status=fail 属于未登记失败（已登记负样本状态是 expected_failure）；
    # status=gap_not_reproduced 属于未注销的缺口（需人工注销 known_gap）。
    g1_violations = [
        item for item in results if item["status"] in ("fail", "gap_not_reproduced")
    ]
    g1_passed = not g1_violations

    # G2：覆盖率低于门槛且未登记 known_gap（未落进 known_limitations）的样本。
    g2_violations = [
        item
        for item in results
        if item["coverage"] is not None
        and item["coverage"] < coverage_min
        and item["status"] != "known_limitation"
    ]
    g2_passed = not g2_violations

    # G3：顺序得分低于门槛且未登记的样本。
    g3_violations = [
        item
        for item in results
        if item["order_score"] is not None
        and item["order_score"] < order_min
        and item["status"] != "known_limitation"
    ]
    g3_passed = not g3_violations

    # G4：依赖缺失即为阻断项。
    g4_items = [item for item in results if item["status"] == "dependency_missing"]
    g4_passed = not g4_items

    # G5a：不得用数据暗示验证过——依赖缺失样本不得带覆盖率/顺序数值。
    claimed = [
        item["name"]
        for item in results
        if item["status"] == "dependency_missing"
        and (item["coverage"] is not None or item["order_score"] is not None)
    ]
    g5a_passed = not claimed

    # G6：失败率门槛（分母为 pass + fail）。
    g6_passed = failure_rate <= failure_rate_max
    g6_violations = [] if g6_passed else [f"measured_failure_rate={failure_rate} > {failure_rate_max}"]

    checks = {
        "G1": {"passed": g1_passed, "violations": names(g1_violations)},
        "G2": {"passed": g2_passed, "violations": names(g2_violations)},
        "G3": {"passed": g3_passed, "violations": names(g3_violations)},
        "G4": {"passed": g4_passed, "violations": names(g4_items)},
        "G5a": {"passed": g5a_passed, "violations": claimed},
        "G6": {"passed": g6_passed, "violations": g6_violations},
    }
    # G5b（表述扫描）与 G5c（used_mock）是报告级规则，只在 apply_report_level_checks 判定。
    # 结论（blocking_rules / meets_gate）同样由 apply_report_level_checks 统一派生，
    # 这里给出的仅是样本级规则的中间结果，不作为最终结论对外发布。
    return {
        **LOCKED_GATE,
        "measured_failure_rate": failure_rate,
        "checks": checks,
        "blocking_rules": _derive_blocking_rules(checks),
        "meets_gate": not _derive_blocking_rules(checks),
    }


def scan_forbidden_phrases(report: dict) -> list[str]:
    """扫描整份报告，返回出现的禁用表述（G5b）。"""
    serialized = json.dumps(report, ensure_ascii=False)
    return [phrase for phrase in FORBIDDEN_VERIFICATION_PHRASES if phrase in serialized]


def probe_ocr_provider(ocr_provider: str) -> dict:
    """经真实工厂解析一次 provider，返回其类路径与是否为真实 provider。

    用于让 ``ocr_probe`` 的 ``used_mock`` / ``path`` 从**实际生效的 provider 对象**
    派生，而不是硬编码常量。
    """
    from app.core.config import settings
    from app.rag.ocr.factory import get_ocr_provider

    original = (settings.RAG_OCR_ENABLED, settings.RAG_OCR_PROVIDER)
    settings.RAG_OCR_ENABLED = True
    settings.RAG_OCR_PROVIDER = ocr_provider
    try:
        provider = get_ocr_provider()
        provider_class = f"{type(provider).__module__}.{type(provider).__qualname__}"
        return {
            "provider_class": provider_class,
            "factory_provider_is_real": provider_class.startswith(REAL_OCR_PROVIDER_MODULE_PREFIX),
            "factory_resolution_error": None,
        }
    except Exception as exc:  # noqa: BLE001 - 解析失败也要如实记录
        return {
            "provider_class": None,
            "factory_provider_is_real": False,
            "factory_resolution_error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        settings.RAG_OCR_ENABLED, settings.RAG_OCR_PROVIDER = original


def build_ocr_probe(results: list[dict], *, ocr_provider: str) -> dict:
    """组装 ocr_probe 段：全部字段从实际生效的 provider 派生。"""
    probe = probe_ocr_provider(ocr_provider)
    observed = next(
        (
            item["ocr_provider"]
            for item in results
            if item["kind"] == "ocr" and item.get("ocr_provider")
        ),
        None,
    )
    observed_is_real = observed is None or observed in REAL_OCR_PROVIDER_NAMES
    used_mock = not (probe["factory_provider_is_real"] and observed_is_real)
    return {
        "enabled_for_ocr_samples": True,
        "provider": ocr_provider,
        "provider_class": probe["provider_class"],
        "factory_resolution_error": probe["factory_resolution_error"],
        "observed_provider": observed,
        "real_provider_module_prefix": REAL_OCR_PROVIDER_MODULE_PREFIX,
        "real_provider_names": sorted(REAL_OCR_PROVIDER_NAMES),
        "used_mock": used_mock,
        "path": (
            f"app.rag.ocr.factory.get_ocr_provider -> {probe['provider_class']}"
            if probe["provider_class"]
            else "app.rag.ocr.factory.get_ocr_provider -> 解析失败"
        ),
    }


def apply_report_level_checks(report: dict) -> dict:
    """判定报告级规则（G5b / G5c），并**派生**门禁最终结论。

    这里是门禁结论的**唯一出口**：``blocking_rules`` 与 ``meets_gate`` 都由
    ``checks`` 重新算出来，不在别处单独维护。理由见 N-01——第 1 步「在统一入口
    重算」只有在有测试盯着 ``meets_gate`` 时才产生守护，因此这里无条件重算
    （不再是「只有出现违规才重算」），任何一处改了 checks 都会反映到结论上。
    """
    gate = report["gate"]
    checks = gate["checks"]

    # G5b（表述层面）：整份报告不得出现宣称「OCR 质量已经过验证」的禁用表述。
    forbidden = scan_forbidden_phrases(report)
    checks["G5b"] = {"passed": not forbidden, "violations": forbidden}

    # G5c（路径层面）：未走真实 provider 路径时，不得声明走了真实路径。
    used_mock = report["ocr_probe"]["used_mock"]
    checks["G5c"] = {
        "passed": not used_mock,
        "violations": (
            ["ocr_probe.used_mock=True：未走真实 provider 路径，不得声明走了真实路径"]
            if used_mock
            else []
        ),
    }

    gate["checks"] = {name: checks[name] for name in GATE_RULE_ORDER if name in checks}
    gate["blocking_rules"] = _derive_blocking_rules(gate["checks"])
    gate["meets_gate"] = not gate["blocking_rules"]
    return report


def build_report(*, ocr_provider: str) -> dict:
    """跑完整样本集并组装报告。"""
    manifest = load_manifest()
    results = [evaluate_sample(sample, ocr_provider=ocr_provider) for sample in manifest["samples"]]

    failures = [item for item in results if item["status"] == "fail"]
    dependency_missing = [item for item in results if item["status"] == "dependency_missing"]
    known_limitations = [item for item in results if item["status"] == "known_limitation"]
    gap_not_reproduced = [item for item in results if item["status"] == "gap_not_reproduced"]
    expected_failures = [item for item in results if item["status"] == "expected_failure"]
    passed = [item for item in results if item["status"] == "pass"]

    scored = [item for item in results if item["status"] in ("pass", "fail")]
    failure_rate = round(len(failures) / len(scored), 4) if scored else 0.0

    gate = evaluate_gate(results, failure_rate=failure_rate)
    report = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "card": "RAG-033 多格式解析与 OCR 质量基线",
        "corpus_dir": (
            str(CORPUS_DIR.relative_to(REPO_ROOT))
            if CORPUS_DIR.is_relative_to(REPO_ROOT)
            else str(CORPUS_DIR)
        ),
        "corpus_version": manifest.get("version"),
        "cjk_font": manifest.get("cjk_font"),
        "cjk_font_is_system_font": manifest.get("cjk_font_is_system_font"),
        "cjk_font_note": manifest.get("cjk_font_note"),
        "ocr_probe": build_ocr_probe(results, ocr_provider=ocr_provider),
        "dependencies": probe_dependencies(),
        "samples": results,
        "totals": {
            "samples": len(results),
            "passed": len(passed),
            "failed": len(failures),
            "dependency_missing": len(dependency_missing),
            "known_limitation": len(known_limitations),
            "gap_not_reproduced": len(gap_not_reproduced),
            "expected_failure": len(expected_failures),
        },
        "failures": failures,
        "dependency_missing_list": dependency_missing,
        "known_limitations": known_limitations,
        "gap_not_reproduced_list": gap_not_reproduced,
        "gate": gate,
    }

    # 报告级检查（禁用表述扫描 + used_mock 派生值）回写门禁。
    return apply_report_level_checks(report)


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
    lines.append("已知缺口（G2/G3 豁免：豁免门槛，但不计入 passed）：")
    for item in report["known_limitations"]:
        lines.append(
            f"  - {item['name']}: 覆盖={item['coverage']} 顺序={item['order_score']} "
            f"原因={item['failure_reason']}"
        )
    if report["gap_not_reproduced_list"]:
        lines.append("")
        lines.append("未注销的缺口（G1 阻断：需人工注销 known_gap）：")
        for item in report["gap_not_reproduced_list"]:
            lines.append(f"  - {item['name']}: {item['failure_reason']}")
    lines.append("")
    gate = report["gate"]
    lines.append(
        f"质量门禁 {gate['version']}（状态={gate['status']}，锁定人={gate['locked_by']}，"
        f"锁定日期={gate['locked_at']}）：覆盖率≥{gate['coverage_min']}、"
        f"顺序得分≥{gate['order_score_min']}、失败率≤{gate['failure_rate_max']}"
    )
    for rule_name in GATE_RULE_ORDER:
        check = gate["checks"][rule_name]
        lines.append(
            f"  {rule_name} {'通过' if check['passed'] else '不通过'}："
            f"{gate['rules'][rule_name]}"
            + (f"（命中：{check['violations']}）" if check["violations"] else "")
        )
    lines.append(
        f"  实测失败率={gate['measured_failure_rate']}；阻断规则={gate['blocking_rules']}；"
        f"meets_gate={gate['meets_gate']}"
    )
    return "\n".join(lines)


def default_report_name(now: datetime | None = None) -> str:
    """报告文件名：``parse-quality-<日期>-<门禁版本>.json``。

    把门禁版本写进文件名，门禁升级后产出的是新文件，不会静默覆盖旧门禁下
    已评审的报告，也避免「报告里写得好听但对应文件已被换掉」。
    """
    moment = now or datetime.now()
    gate_tag = LOCKED_GATE["version"].replace(".", "-")
    return f"parse-quality-{moment.strftime('%Y%m%d')}-{gate_tag}.json"


def write_json(report: dict, target: Path, *, force: bool = False) -> Path:
    """写入 JSON 报告。

    禁止覆盖冻结基线报告；同名报告默认也拒绝静默覆盖（需显式 ``--force``
    或改用 ``--json-path``），避免同一天重跑把已评审的报告悄悄换掉。
    """
    if target.name == FROZEN_BASELINE_NAME:
        raise SystemExit(f"拒绝写入冻结基线报告：{target}")
    if target.exists() and not force:
        raise SystemExit(
            f"目标报告已存在，拒绝静默覆盖：{target}（如需覆盖请显式加 --force，"
            f"或用 --json-path 指定新路径）"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="RAG-033 多格式解析与 OCR 质量基线报告")
    parser.add_argument("--json", action="store_true", help="同时落一份 JSON 报告到 evals/reports/")
    parser.add_argument("--json-path", default="", help="自定义 JSON 报告路径")
    parser.add_argument(
        "--force",
        action="store_true",
        help="允许覆盖同名报告（默认拒绝静默覆盖）",
    )
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
            else REPORT_DIR / default_report_name()
        )
        written = write_json(report, target, force=args.force)
        print(f"\nJSON 报告已写入：{written}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
