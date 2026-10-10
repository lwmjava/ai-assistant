"""RAG-017 门禁判定测试：正常报告通过，退化/违规/伪造配置一律拒绝。

用 subprocess 直接执行 scripts/rag_gate_check.py，基于真实报告复制出变体，
验证退出码语义与 CI 一致（0=通过，1=拦截）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_SCRIPT = REPO_ROOT / "scripts" / "rag_gate_check.py"
REAL_REPORT = REPO_ROOT / "evals" / "reports" / "rag-v0.1-baseline-20261010.json"
BASELINE = REPO_ROOT / "evals" / "reports" / "rag-v0.1-baseline-20260919.json"


def run_gate(report: Path, baseline: Path = BASELINE) -> subprocess.CompletedProcess:
    """以 subprocess 运行门禁脚本并返回结果。"""
    return subprocess.run(
        [sys.executable, str(GATE_SCRIPT), "--report", str(report), "--baseline", str(baseline)],
        capture_output=True,
        text=True,
        timeout=60,
    )


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
