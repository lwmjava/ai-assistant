"""RAG-017 门禁判定测试：正常报告通过，退化/违规/伪造配置一律拒绝。

用 subprocess 直接执行 scripts/rag_gate_check.py，基于真实报告复制出变体，
验证退出码语义与 CI 一致（0=通过，1=拦截）。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests._utf8_subprocess import python_utf8

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_SCRIPT = REPO_ROOT / "scripts" / "rag_gate_check.py"
REAL_REPORT = REPO_ROOT / "evals" / "reports" / "rag-v0.1-baseline-20261010.json"
BASELINE = REPO_ROOT / "evals" / "reports" / "rag-v0.1-baseline-20260919.json"


def run_gate(report: Path, baseline: Path = BASELINE) -> subprocess.CompletedProcess[str]:
    """以 subprocess 运行门禁脚本并返回结果。"""
    return python_utf8([str(GATE_SCRIPT), "--report", str(report), "--baseline", str(baseline)])


@pytest.fixture()
def report_copy(tmp_path: Path) -> dict:
    """基于 2026-10-10 真实官方报告复制一份可修改的报告副本。"""
    payload = json.loads(REAL_REPORT.read_text(encoding="utf-8"))
    return payload


def write_report(tmp_path: Path, payload: dict) -> Path:
    """把报告字典写入临时 JSON 并返回路径。"""
    out = tmp_path / "report.json"
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return out


def test_gate_passes_on_real_report(report_copy: dict, tmp_path: Path) -> None:
    """真实官方报告（真实嵌入、达标）应通过。"""
    result = run_gate(write_report(tmp_path, report_copy))
    assert result.returncode == 0
    assert "门禁通过" in result.stdout


def test_gate_rejects_recall_degradation(report_copy: dict, tmp_path: Path) -> None:
    """人为制造 Recall@1 退化应被拦截。"""
    report_copy["overall"]["recall"]["@1"] = 0.5
    result = run_gate(write_report(tmp_path, report_copy))
    assert result.returncode == 1
    assert "Recall@1" in result.stdout


def test_gate_rejects_mrr_degradation(report_copy: dict, tmp_path: Path) -> None:
    """人为制造 MRR 退化应被拦截。"""
    report_copy["overall"]["mrr"] = 0.8
    result = run_gate(write_report(tmp_path, report_copy))
    assert result.returncode == 1
    assert "MRR" in result.stdout


def test_gate_rejects_ndcg_degradation(report_copy: dict, tmp_path: Path) -> None:
    """人为制造 nDCG@10 退化应被拦截。"""
    report_copy["overall"]["ndcg@10"] = 0.8
    result = run_gate(write_report(tmp_path, report_copy))
    assert result.returncode == 1
    assert "nDCG@10" in result.stdout


def test_gate_rejects_violation_case(report_copy: dict, tmp_path: Path) -> None:
    """安全切片出现越权案例应被拦截（零容忍）。"""
    report_copy["overall"]["safety"]["violation_cases"] = 1
    result = run_gate(write_report(tmp_path, report_copy))
    assert result.returncode == 1
    assert "越权案例" in result.stdout


def test_gate_rejects_mock_embedding(report_copy: dict, tmp_path: Path) -> None:
    """报告嵌入为 Mock 应被拦截，不得记质量通过。"""
    report_copy["run_config"]["embedding_provider"] = "MockEmbeddingProvider"
    result = run_gate(write_report(tmp_path, report_copy))
    assert result.returncode == 1
    assert "Mock" in result.stdout


def test_gate_rejects_config_mismatch(report_copy: dict, tmp_path: Path) -> None:
    """索引期配置与冻结基线不一致应被拦截。"""
    report_copy["run_config"]["chunk_strategy"] = "auto"
    result = run_gate(write_report(tmp_path, report_copy))
    assert result.returncode == 1
    assert "chunk_strategy" in result.stdout


def test_gate_rejects_missing_report(tmp_path: Path) -> None:
    """报告缺失时（外部模型失败语义）应直接失败，不得通过。"""
    result = run_gate(tmp_path / "does-not-exist.json")
    assert result.returncode == 1
    assert "无法读取" in f"{result.stdout}\n{result.stderr}"


def test_gate_rejects_missing_metric(report_copy: dict, tmp_path: Path) -> None:
    """报告字段不全（如 mrr 缺失）应失败，不得静默放行。"""
    del report_copy["overall"]["mrr"]
    result = run_gate(write_report(tmp_path, report_copy))
    assert result.returncode == 1
    assert "mrr 缺失" in result.stdout


METRIC_PATHS = [
    ("recall", "@1"), ("recall", "@5"), ("recall", "@10"),
    ("mrr",), ("ndcg@10",),
]


@pytest.mark.parametrize("path", METRIC_PATHS)
@pytest.mark.parametrize("value", [
    float("nan"), float("inf"), -float("inf"), -0.1, 1.1,
    True, False, "1.0", [], {}, 10**400,
])
def test_gate_rejects_invalid_metric(report_copy: dict, tmp_path: Path, path: tuple, value: object) -> None:
    """所有排序指标必须是有限且在 [0, 1] 内的数值，不能用布尔或字符串替代。"""
    metrics = report_copy["overall"]
    for key in path[:-1]:
        metrics = metrics[key]
    metrics[path[-1]] = value
    result = run_gate(write_report(tmp_path, report_copy))
    assert result.returncode == 1
    assert "非法" in result.stdout
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("value", [
    -1, 0.0, 0.5, True, False, float("nan"), float("inf"), -float("inf"), "0", [], {},
])
def test_gate_rejects_invalid_violation_count(report_copy: dict, tmp_path: Path, value: object) -> None:
    """越权案例数必须是非负整数；零容忍不能由负值或其他类型绕过。"""
    report_copy["overall"]["safety"]["violation_cases"] = value
    result = run_gate(write_report(tmp_path, report_copy))
    assert result.returncode == 1
    assert "非法" in result.stdout
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("path,value", [
    (("overall",), []), (("overall", "recall"), "invalid"),
    (("overall", "safety"), [0]),
])
def test_gate_rejects_invalid_metric_structure(report_copy: dict, tmp_path: Path, path: tuple, value: object) -> None:
    """损坏指标结构以明确门禁问题返回，避免类型错误堆栈。"""
    parent = report_copy
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    result = run_gate(write_report(tmp_path, report_copy))
    assert result.returncode == 1
    assert "非法" in result.stdout
    assert "Traceback" not in result.stderr


def test_gate_accepts_metric_upper_bounds(report_copy: dict, tmp_path: Path) -> None:
    """合法的满分指标及零越权保持可通过。"""
    report_copy["overall"]["recall"] = dict.fromkeys(["@1", "@5", "@10"], 1)
    report_copy["overall"]["mrr"] = 1.0
    report_copy["overall"]["ndcg@10"] = 1
    result = run_gate(write_report(tmp_path, report_copy))
    assert result.returncode == 0


def test_gate_accepts_approved_thresholds(report_copy: dict, tmp_path: Path) -> None:
    """批准阈值为包含式下限，修复不提高质量门槛。"""
    report_copy["overall"]["recall"] = {"@1": 0.78, "@5": 0.94, "@10": 0.94}
    report_copy["overall"]["mrr"] = 0.92
    report_copy["overall"]["ndcg@10"] = 0.92
    assert run_gate(write_report(tmp_path, report_copy)).returncode == 0
