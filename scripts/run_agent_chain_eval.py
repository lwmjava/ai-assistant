"""意图分流和工具选择评测。

冒烟模式使用不读取标准答案的本地模型，只证明提示、评分和报告链路。
正式模式只调用真实模型。工具选择只解析输出并记在内存里，不执行工具，
不创建会话，不连接检索、沙箱、MCP 或数据库。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.agents.pipeline import (  # noqa: E402
    build_preflight_messages,
    build_tool_choice_messages,
    preflight_needs_full_pipeline,
)
from app.agents.prompts import SYSTEM_PREFLOW  # noqa: E402
from app.agents.tools.base import inspect_tool_call  # noqa: E402
from app.llm.base import LLMOptions  # noqa: E402
from app.llm.factory import get_llm_provider, llm_availability  # noqa: E402

EVALUATOR_VERSION = "agent-chain-eval-0.2.0"
SIDE_EFFECTS = {"sandbox": 0, "mcp": 0, "network": 0, "rag": 0, "database": 0}
CASES_PATH = REPO_ROOT / "evals" / "datasets" / "agent-chain-v0.1" / "cases.json"
INDEX_PATH = REPO_ROOT / "evals" / "datasets" / "agent-chain-v0.1" / "index.json"
REPORT_DIR = REPO_ROOT / "evals" / "reports"


class SmokeIntentModel:
    """只看送审提示的本地分流器，不读取 expected 字段。"""

    model = "smoke-intent"

    async def chat(self, messages, options=None) -> str:
        user = messages[-1].content if messages else ""
        if "## 可用外部工具" in user:
            latest = user.split("## 用户最新消息", 1)[-1].strip()
            if "code_sandbox" in user and latest in {"1", "好的"}:
                return '<tool_call>{"name": "code_sandbox", "arguments": {"code": "print(1)"}}</tool_call>'
            return "不调用工具"
        latest = user.split("## 用户最新消息", 1)[-1].strip()
        short_exact = {"你好", "在吗", "哈哈", "谢谢", "一加一等于几", "什么是质数，用一句话回答"}
        if latest in short_exact and "请选择" not in user and "确认后" not in user:
            return "NO"
        if latest in {"1", "好的"} and ("请选择" in user or "确认后" in user):
            return "YES"
        return "NO" if len(latest) <= 8 else "YES"

    async def stream_chat(self, messages, options=None):
        yield await self.chat(messages, options)


def git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def load_cases() -> list[dict]:
    payload = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("cases.json 必须是数组")
    return payload


def prompt_hash() -> str:
    return hashlib.sha256(SYSTEM_PREFLOW.encode("utf-8")).hexdigest()


def dataset_hash() -> str:
    return hashlib.sha256(CASES_PATH.read_bytes()).hexdigest()


def score(cases: list[dict], predictions: list[str]) -> dict:
    total = len(cases)
    correct = 0
    follow_total = 0
    follow_hit = 0
    rows = []
    for case, predicted in zip(cases, predictions, strict=True):
        expected = case["expected_route"]
        ok = predicted == expected
        correct += int(ok)
        if case["critical_followup"]:
            follow_total += 1
            follow_hit += int(ok)
        rows.append(
            {
                "case_id": case["case_id"],
                "expected_route": expected,
                "predicted_route": predicted,
                "critical_followup": case["critical_followup"],
                "passed": ok,
            }
        )
    return {
        "route_accuracy": correct / total if total else 0.0,
        "followup_recall": follow_hit / follow_total if follow_total else 0.0,
        "rows": rows,
    }


def score_tools(cases: list[dict], chosen: list[list[str]]) -> dict:
    total = 0
    hit = 0
    rows = []
    for case, names in zip(cases, chosen, strict=True):
        expected = case.get("expected_tools") or []
        if not expected:
            rows.append({"case_id": case["case_id"], "predicted_tools": names, "passed": True})
            continue
        total += 1
        ok = any(name in expected for name in names)
        hit += int(ok)
        rows.append({"case_id": case["case_id"], "predicted_tools": names, "passed": ok})
    return {
        "tool_selection_recall": hit / total if total else 1.0,
        "rows": rows,
    }


async def predict_tools(provider, cases: list[dict]) -> tuple[list[list[str]], list[str]]:
    """只记录模型选出的工具名。不调用工具实现。"""
    options = LLMOptions(temperature=0)
    chosen: list[list[str]] = []
    raws: list[str] = []
    for case in cases:
        if not case.get("expected_tools"):
            chosen.append([])
            raws.append("")
            continue
        messages = build_tool_choice_messages(case["history"], case["user_input"])
        blob = "\n".join(message.content for message in messages)
        if any(token in blob for token in ("expected_route", "expected_tools", "critical_followup")):
            raise RuntimeError("工具选择提示包含标准答案字段")
        raw = await provider.chat(messages, options)
        intent = inspect_tool_call(raw)
        name = intent.call.name if intent.status == "ok" and intent.call is not None else ""
        chosen.append([name] if name else [])
        raws.append(raw)
    return chosen, raws


async def predict(provider, cases: list[dict]) -> tuple[list[str], list[str]]:
    options = LLMOptions(temperature=0)
    routes: list[str] = []
    raws: list[str] = []
    for case in cases:
        messages = build_preflight_messages(case["history"], case["user_input"])
        blob = "\n".join(message.content for message in messages)
        if any(token in blob for token in ("expected_route", "expected_tools", "critical_followup")):
            raise RuntimeError("评测提示包含标准答案字段")
        raw = await provider.chat(messages, options)
        route = "short" if not preflight_needs_full_pipeline(raw) else "full"
        routes.append(route)
        raws.append(raw)
    return routes, raws


def write_report(path: Path, payload: dict) -> None:
    if path.exists():
        raise FileExistsError(f"拒绝覆盖已有报告：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def run(mode: str, report_dir: Path) -> int:
    cases = load_cases()
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    if mode == "official":
        if llm_availability() != "real":
            print("正式评测需要真实模型，当前不可用。")
            return 2
        provider = get_llm_provider("intent")
    elif mode == "smoke":
        provider = SmokeIntentModel()
    else:
        print("mode 只能是 smoke 或 official")
        return 2
    routes, raws = await predict(provider, cases)
    tool_names, tool_raws = await predict_tools(provider, cases)
    metrics = score(cases, routes)
    tool_metrics = score_tools(cases, tool_names)
    if any(SIDE_EFFECTS.values()):
        print("评测产生了真实副作用，拒绝写报告。")
        return 2
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_path = report_dir / f"agent-chain-v0.1-{mode}-{stamp}.json"
    report = {
        "dataset_version": index["dataset_version"],
        "dataset_hash": dataset_hash(),
        "prompt_hash": prompt_hash(),
        "prompt_version": index["prompt_version"],
        "evaluator_version": EVALUATOR_VERSION,
        "model": getattr(provider, "model", "unknown"),
        "temperature": 0,
        "seed_supported": False,
        "git_commit": git_commit(),
        "mode": mode,
        "route_accuracy": metrics["route_accuracy"],
        "followup_recall": metrics["followup_recall"],
        "tool_selection_recall": tool_metrics["tool_selection_recall"],
        "side_effects": dict(SIDE_EFFECTS),
        "cases": [
            {**row, "raw_reply": raw, "tool_reply": tool_raw, "predicted_tools": tools["predicted_tools"]}
            for row, raw, tool_raw, tools in zip(
                metrics["rows"], raws, tool_raws, tool_metrics["rows"], strict=True
            )
        ],
    }
    write_report(report_path, report)
    print(report_path)
    if mode == "official":
        accuracy_ok = metrics["route_accuracy"] >= index["thresholds"]["route_accuracy"]
        recall_ok = metrics["followup_recall"] >= index["thresholds"]["followup_recall"]
        tool_ok = tool_metrics["tool_selection_recall"] >= index["thresholds"]["tool_selection_recall"]
        return 0 if accuracy_ok and recall_ok and tool_ok else 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["smoke", "official"], required=True)
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.mode, args.report_dir)))


if __name__ == "__main__":
    main()
