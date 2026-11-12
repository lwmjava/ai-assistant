"""证据冲突验证与有限纠错的离线对照运行器。

只允许 mock 模式。真实模型、付费调用和默认 Web 外发都拒绝。
holdout 只写入报告，不参与采纳决策。生产默认保持关闭。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.agents.evidence_conflict import (  # noqa: E402
    MAX_CALLS_PER_CASE,
    CandidateAnswer,
    CorrectionContext,
    run_bounded_correction,
)
from evals.evidence_conflict.dataset import build_cases, dataset_index, evidence_from_case  # noqa: E402
from tests.eval.evidence_conflict import (  # noqa: E402
    behavior_match,
    decide_evidence_correction,
    summarize_outcomes,
)

REPORT_DIR = REPO_ROOT / "evals" / "reports"
EVAL_DOC_DIR = REPO_ROOT / "docs" / "evaluations"
SHANGHAI = ZoneInfo("Asia/Shanghai")


class LiveRunRefusedError(RuntimeError):
    """真实或付费运行未授权。"""


def refuse_live_run(mode: str) -> None:
    """本实验只允许 mock，拒绝 live、official 或付费模式。"""
    normalized = mode.strip().lower()
    if normalized != "mock":
        raise LiveRunRefusedError("本实验只允许 mock；真实模型、付费调用和 live 运行未授权")


def _run_case(case: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    items = evidence_from_case(case)
    baseline = case["baseline_answer"]
    answer = CandidateAnswer(
        text=baseline["text"],
        claimed_subject=baseline["claimed_subject"],
        claimed_value=baseline["claimed_value"],
        used_evidence_ids=tuple(baseline["used_evidence_ids"]),
    )
    context = CorrectionContext(
        tenant_id=case["tenant_id"],
        user_id=case["user_id"],
        fact_domain=case["fact_domain"],
        cancelled=case["context"]["cancelled"],
        budget_remaining=case["context"]["budget_remaining"],
        max_corrections=case["context"]["max_corrections"],
    )
    started = time.perf_counter()
    control_answer = answer
    control_latency = (time.perf_counter() - started) * 1000.0
    started = time.perf_counter()
    result = run_bounded_correction(answer, items, context)
    treatment_latency = (time.perf_counter() - started) * 1000.0
    control = {
        "case_id": case["case_id"],
        "split": case["split"],
        "slice": case["slice"],
        "text": control_answer.text,
        "value": control_answer.claimed_value,
        "status": "unchanged",
        "attempts": 0,
        "calls": 0,
        "leaked": False,
        "used_ineligible": False,
        "behavior_match": False,
        "latency_ms": control_latency,
    }
    treatment = {
        "case_id": case["case_id"],
        "split": case["split"],
        "slice": case["slice"],
        "text": result.answer.text,
        "value": result.answer.claimed_value,
        "status": result.status,
        "attempts": result.attempts,
        "calls": result.calls,
        "leaked": result.leaked,
        "used_ineligible": result.used_ineligible,
        "behavior_match": behavior_match(case, result),
        "latency_ms": treatment_latency,
        "conflict_type": result.conflict_type,
        "winning_evidence_ids": list(result.winning_evidence_ids),
        "dropped_evidence_ids": list(result.dropped_evidence_ids),
    }
    if result.calls > MAX_CALLS_PER_CASE:
        raise RuntimeError(f"{case['case_id']} 超过每例调用上限")
    return control, treatment


def run_experiment(mode: str = "mock") -> dict[str, Any]:
    """运行锁定夹具上的对照实验，并给出离线采纳结论。"""
    refuse_live_run(mode)
    cases = build_cases()
    control_rows: list[dict[str, Any]] = []
    treatment_rows: list[dict[str, Any]] = []
    for case in cases:
        control, treatment = _run_case(case)
        control_rows.append(control)
        treatment_rows.append(treatment)
    decision_summary = summarize_outcomes(cases, control_rows, treatment_rows, include_holdout=False)
    recorded_summary = summarize_outcomes(cases, control_rows, treatment_rows, include_holdout=True)
    decision = decide_evidence_correction(decision_summary)
    return {
        "experiment": "evidence-conflict-correction",
        "dataset": dataset_index(cases),
        "mode": "mock",
        "paid_calls": 0,
        "live_run": False,
        "run_at": datetime.now(SHANGHAI).isoformat(),
        "timezone": "Asia/Shanghai",
        "decision_inputs": decision_summary,
        "recorded_including_holdout": recorded_summary,
        "decision": decision,
        "control_cases": control_rows,
        "treatment_cases": treatment_rows,
    }


def render_markdown(report: dict[str, Any]) -> str:
    """把对照结果写成复核用的 Markdown。"""
    decision = report["decision"]
    summary = report["decision_inputs"]
    recorded = report["recorded_including_holdout"]
    dataset = report["dataset"]
    lines = [
        "# 证据冲突验证与有限纠错离线实验",
        "",
        f"> 运行时间（Asia/Shanghai）：{report['run_at']}",
        (
            f"> 数据集：`{dataset['dataset_id']}@{dataset['dataset_version']}`，"
            f"分类 {dataset['classification']}，共 {dataset['case_count']} 条。"
        ),
        f"> 模式：{report['mode']}，付费调用 {report['paid_calls']} 次。",
        "",
        "## 结论",
        "",
        f"- 夹具决策：`{decision['decision']}`（{decision['reason_code']}）。",
        f"- 生产决策：`{decision['production_decision']}`。本实验不接入回答链，也不修改生产默认。",
        f"- 原因：{decision['reason']}。",
        "",
        "## 决策集指标（不含 holdout）",
        "",
        f"- 样本数：{summary['case_count']}",
        f"- 对照目标错误率：{summary['control_error_rate']}",
        f"- 处理目标错误率：{summary['treatment_error_rate']}",
        f"- 相对错误下降：{summary['relative_error_reduction']}",
        f"- 正确回答误改率：{summary['miscorrection_rate']}",
        f"- 安全违规：{summary['safety_violations']}",
        f"- 纠错越界：{summary['bound_violations']}",
        f"- 行为复现率：{summary['behavior_pass_rate']}",
        f"- 对照 p95：{summary['latency_ms']['control_p95']} ms",
        f"- 处理 p95：{summary['latency_ms']['treatment_p95']} ms",
        "",
        "## 含 holdout 的记录（不参与决策）",
        "",
        f"- 样本数：{recorded['case_count']}",
        f"- 对照目标错误率：{recorded['control_error_rate']}",
        f"- 处理目标错误率：{recorded['treatment_error_rate']}",
        "",
        "## 预注册门槛",
        "",
    ]
    thresholds = decision["thresholds"]
    lines.append(f"- 目标错误率相对下降 ≥ {thresholds['relative_error_reduction_min']}")
    lines.append(f"- 正确回答误改 ≤ {thresholds['miscorrection_max']}")
    lines.append(f"- 安全违规 = {thresholds['safety_violations']}，纠错越界 = {thresholds['bound_violations']}")
    lines.append(f"- 行为复现率 = {thresholds['behavior_pass_rate']}")
    lines.append(f"- p95 延迟比 ≤ {thresholds['latency_p95_max_ratio']}")
    lines.append(f"- 每例最多纠错 {thresholds['max_corrections']} 次、最多调用 {thresholds['max_calls_per_case']} 次")
    lines.append("")
    lines.append("这些门槛只用于本次离线实验，不是官方 RAG 或 Agent 评测阈值，也未改写任何冻结报告。")
    lines.append("")
    if decision["failed_gates"]:
        lines.append("未通过的门禁：")
        lines.append("")
        for item in decision["failed_gates"]:
            lines.append(f"- `{item}`")
        lines.append("")
    lines.append("## 切片")
    lines.append("")
    lines.append("| 切片 | 决策集条数 | 对照错误 | 处理错误 | 行为命中 |")
    lines.append("|---|---:|---:|---:|---:|")
    for name, bucket in summary["by_slice"].items():
        lines.append(
            "| {} | {} | {} | {} | {} |".format(
                name,
                bucket["cases"],
                bucket["control_errors"],
                bucket["treatment_errors"],
                bucket["behavior_hits"],
            )
        )
    lines.append("")
    lines.append("## 约束")
    lines.append("")
    lines.append("- 查询改写、生效日过滤、官方 35 条门槛和冻结 holdout 均未改动。")
    lines.append("- 未使用真实模型，也没有默认 Web 外发。")
    lines.append("- 负结果或仅夹具通过都不能创建生产接线。")
    lines.append("")
    return "\n".join(lines) + "\n"


def write_reports(report: dict[str, Any]) -> tuple[Path, Path]:
    """写入 JSON 报告和 Markdown 说明。"""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    EVAL_DOC_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / "evidence-conflict-correction-v0.1.json"
    md_path = EVAL_DOC_DIR / "evidence-conflict-correction-v0.1.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="证据冲突有限纠错离线实验（仅 mock）")
    parser.add_argument("--mode", default="mock", help="只接受 mock")
    parser.add_argument("--write", action="store_true", help="写入报告文件")
    args = parser.parse_args(argv)
    try:
        report = run_experiment(mode=args.mode)
    except LiveRunRefusedError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.write:
        json_path, md_path = write_reports(report)
        print(json_path)
        print(md_path)
    print(json.dumps(report["decision"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
