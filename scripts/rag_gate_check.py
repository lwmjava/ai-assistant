"""RAG-017 门禁判定：校验 RAG 基线评测报告的配置一致性与指标门槛。

作用：CI 中跑完 `run_rag_baseline.py --mode official` 后，用本脚本决定是否放行。
任一条件不满足即退出码非 0，外部模型失败（报告缺失/损坏/字段不全）直接失败，
不得记质量通过。报告嵌入提供商解析到 Mock 时一律拒绝。

门槛数值：2026-10-10 用户批准（建议档），见 docs/plans/plan_rag_017_gate_20261010.md。
配置一致性：报告 run_config 必须与冻结基线（2026-09-19）一致，防止「改配置跑出
高分」制造假通过；门禁评测本身走真实对话过滤契约（RAGService 按 tenant 过滤）。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

# 指标门槛（建议档，2026-10-10 批准）
RECALL_THRESHOLDS: dict[str, float] = {"@1": 0.78, "@5": 0.94, "@10": 0.94}
MRR_THRESHOLD = 0.92
NDCG10_THRESHOLD = 0.92
MAX_VIOLATION_CASES = 0

# 报告 run_config 中必须与冻结基线一致的键（embedding_provider 另做 Mock 检查）
CONFIG_KEYS = (
    "embedding_provider",
    "embedding_model",
    "embedding_dim",
    "rag_backend",
    "chunk_strategy",
    "chunk_size",
    "chunk_overlap",
    "rrf_k",
    "vector_store",
)

DEFAULT_BASELINE = Path(__file__).resolve().parents[1] / "evals" / "reports" / "rag-v0.1-baseline-20260919.json"


def _load_json(path: Path) -> dict:
    """读取报告或基线 JSON，失败时抛出带路径的异常。

    作用：让缺失/损坏文件直接成为失败，而不是静默放行。
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"无法读取 {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"{path} 不是 JSON 对象")
    return payload


def check_config(report_cfg: dict, baseline_cfg: dict) -> list[str]:
    """核对报告运行配置与冻结基线一致，并拒绝 Mock 嵌入。

    入参：report_cfg 为本次报告 run_config；baseline_cfg 为冻结基线 run_config。
    出参：问题列表；为空表示配置一致。
    """
    problems: list[str] = []
    provider = str(report_cfg.get("embedding_provider", ""))
    if "Mock" in provider:
        problems.append("embedding_provider 解析到 Mock 嵌入，禁止作为质量证据")
    for key in CONFIG_KEYS:
        expected = baseline_cfg.get(key)
        actual = report_cfg.get(key)
        if actual != expected:
            problems.append(f"run_config.{key}: 报告={actual!r} 期望={expected!r}（与冻结基线不一致）")
    return problems


def check_metrics(overall: dict) -> list[str]:
    """按批准门槛检查 Recall/MRR/nDCG 与安全切片越权。

    入参：overall 为报告总体指标字典。
    出参：问题列表；为空表示全部达标。
    """
    problems: list[str] = []
    recall = overall.get("recall") or {}
    for k, threshold in RECALL_THRESHOLDS.items():
        value = recall.get(k)
        if value is None:
            problems.append(f"overall.recall.{k} 缺失")
        elif value < threshold:
            problems.append(f"Recall{k}={value:.4f} < 门槛 {threshold}")
    mrr = overall.get("mrr")
    if mrr is None:
        problems.append("overall.mrr 缺失")
    elif mrr < MRR_THRESHOLD:
        problems.append(f"MRR={mrr:.4f} < 门槛 {MRR_THRESHOLD}")
    ndcg = overall.get("ndcg@10")
    if ndcg is None:
        problems.append("overall.ndcg@10 缺失")
    elif ndcg < NDCG10_THRESHOLD:
        problems.append(f"nDCG@10={ndcg:.4f} < 门槛 {NDCG10_THRESHOLD}")
    safety = overall.get("safety") or {}
    violations = safety.get("violation_cases")
    if violations is None:
        problems.append("overall.safety.violation_cases 缺失")
    elif violations > MAX_VIOLATION_CASES:
        problems.append(f"安全切片越权案例 {violations} > 允许值 {MAX_VIOLATION_CASES}")
    return problems


def run_gate(report: dict, baseline: dict) -> list[str]:
    """执行门禁判定，返回全部问题。

    入参：report 为本次评测报告；baseline 为冻结基线。
    出参：问题列表；为空表示通过。
    """
    problems = check_config(report.get("run_config") or {}, baseline.get("run_config") or {})
    problems.extend(check_metrics(report.get("overall") or {}))
    return problems


def main(argv: list[str] | None = None) -> int:
    """解析命令行并判定门禁。

    入参：argv 为命令行参数；None 时用 sys.argv。
    出参：进程退出码，通过 0，否则 1。
    """
    parser = argparse.ArgumentParser(description="RAG-017 基线门禁判定")
    parser.add_argument(
        "--report",
        required=True,
        type=Path,
        help="本次 official 评测报告 JSON（run_rag_baseline.py 的输出）",
    )
    parser.add_argument(
        "--baseline",
        default=DEFAULT_BASELINE,
        type=Path,
        help="冻结基线 JSON，默认 rag-v0.1-baseline-20260919.json",
    )
    args = parser.parse_args(argv)

    report = _load_json(args.report)
    baseline = _load_json(args.baseline)
    problems = run_gate(report, baseline)
    if problems:
        print("RAG 基线门禁失败：")
        for item in problems:
            print(f"  - {item}")
        return 1
    overall = report["overall"]
    print("RAG 基线门禁通过：")
    print(
        f"  Recall@1={overall['recall']['@1']:.4f} "
        f"@5={overall['recall']['@5']:.4f} "
        f"@10={overall['recall']['@10']:.4f} "
        f"MRR={overall['mrr']:.4f} nDCG@10={overall['ndcg@10']:.4f} "
        f"越权={overall['safety']['violation_cases']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
