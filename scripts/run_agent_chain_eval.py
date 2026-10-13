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
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.agents.pipeline import (  # noqa: E402
    build_preflight_messages,
    build_tool_choice_messages,
    preflight_needs_full_pipeline,
)
from app.agents.tools.base import inspect_tool_call  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.llm.base import LLMOptions  # noqa: E402
from app.llm.factory import get_llm_provider  # noqa: E402
from app.llm.openai_compatible import OpenAICompatibleProvider  # noqa: E402

EVALUATOR_VERSION = "agent-chain-eval-0.2.0"
# Compatibility declaration: no Tool/DB/RAG execution; these are NOT runtime counters.
SIDE_EFFECTS = {"sandbox": 0, "mcp": 0, "network": 0, "rag": 0, "database": 0}
REPORT_SCHEMA_VERSION = "agent-chain-report-1.0.0"
GATE_VERSION = "agent-chain-gate-1.0.0"
FROZEN_CASES_HASH = "5bfad42d3a2d765363d3489036ea1b91346c6fff49ca58d13fe9147ff974905b"
FROZEN_INDEX_HASH = "8cc903dbc9f406bc070bea620952396a2323e3b41a8eb44ca0a72044bc57669d"
DATA_HASH_ALGORITHM = "sha256-utf8-crlf-to-lf-v1"
MAX_REPORT_BYTES = 1024 * 1024
MAX_REPLY_BYTES = 16 * 1024
CALL_TIMEOUT = 60.0
TOTAL_TIMEOUT = 780.0
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


def prompt_hash(cases: list[dict] | None = None) -> str:
    inputs = []
    for case in cases if cases is not None else load_cases():
        stages = [("route", build_preflight_messages(case["history"], case["user_input"]))]
        if case.get("expected_tools"):
            stages.append(("tool", build_tool_choice_messages(case["history"], case["user_input"])))
        for stage, messages in stages:
            inputs.append({"case_id": case["case_id"], "stage": stage,
                           "messages": [message.model_dump(mode="json") for message in messages]})
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def endpoint_hash(url: str) -> str:
    parts = urlsplit(url)
    if (parts.scheme not in {"https", "http"} or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.query or parts.fragment):
        raise ValueError("invalid_endpoint")
    # Preserve path case and explicit port: the hash identifies the actual configured endpoint.
    normalized = f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path.rstrip('/')}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def report_identity(cases: list[dict] | None = None) -> dict:
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    paths = sorted((REPO_ROOT / "app" / "agents").rglob("*.py"))
    paths += [REPO_ROOT / "app/llm/factory.py", REPO_ROOT / "app/llm/openai_compatible.py",
              REPO_ROOT / "app/llm/base.py", REPO_ROOT / "app/llm/budget.py", REPO_ROOT / "app/llm/capabilities.py",
              REPO_ROOT / ".github/workflows/ci.yml",
              REPO_ROOT / "scripts/run_agent_chain_eval.py", REPO_ROOT / "scripts/agent_gate_check.py",
              REPO_ROOT / "scripts/agent_gate_probe.py"]
    return {
        "dataset_version": index["dataset_version"], "dataset_hash": dataset_hash(),
        "index_hash": content_hash(INDEX_PATH), "data_hash_algorithm": DATA_HASH_ALGORITHM,
        "prompt_version": index["prompt_version"], "prompt_hash": prompt_hash(cases),
        "evaluator_version": EVALUATOR_VERSION,
        "source_hashes": {str(path.relative_to(REPO_ROOT)).replace("\\", "/"):
                          hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
    }


def content_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def dataset_hash() -> str:
    return content_hash(CASES_PATH)


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
    options = LLMOptions(temperature=0, timeout=CALL_TIMEOUT)
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
        raw = await model_reply(provider, messages, options)
        intent = inspect_tool_call(raw)
        name = intent.call.name if intent.status == "ok" and intent.call is not None else ""
        chosen.append([name] if name else [])
        raws.append(raw)
    return chosen, raws


async def predict(provider, cases: list[dict]) -> tuple[list[str], list[str]]:
    options = LLMOptions(temperature=0, timeout=CALL_TIMEOUT)
    routes: list[str] = []
    raws: list[str] = []
    for case in cases:
        messages = build_preflight_messages(case["history"], case["user_input"])
        blob = "\n".join(message.content for message in messages)
        if any(token in blob for token in ("expected_route", "expected_tools", "critical_followup")):
            raise RuntimeError("评测提示包含标准答案字段")
        raw = await model_reply(provider, messages, options)
        route = "short" if not preflight_needs_full_pipeline(raw) else "full"
        routes.append(route)
        raws.append(raw)
    return routes, raws


async def model_reply(provider, messages, options) -> str:
    async with asyncio.timeout(CALL_TIMEOUT):
        raw = await provider.chat(messages, options)
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_REPLY_BYTES:
        raise ValueError("invalid_reply")
    key = getattr(provider, "api_key", "")
    if key and key in raw:
        raise ValueError("unsafe_reply")
    return raw


def allowed_report_path(path: Path) -> bool:
    allowed = [REPO_ROOT / "evals", REPO_ROOT / "data", Path(tempfile.gettempdir())]
    return any(path.resolve().is_relative_to(root.resolve()) for root in allowed)


def write_report(path: Path, payload: dict) -> None:
    if not allowed_report_path(path):
        raise ValueError("report_path_out_of_scope")
    blob = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    if len(blob) > MAX_REPORT_BYTES:
        raise ValueError("report_too_large")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Prepare fully before publishing. The hard link atomically creates without replacing evidence.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(blob)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def build_report(mode, provider, cases, routes, raws, tool_names, tool_raws) -> dict:
    metrics = score(cases, routes)
    tool_metrics = score_tools(cases, tool_names)
    real = type(provider) is OpenAICompatibleProvider
    return {
        **report_identity(cases), "report_schema_version": REPORT_SCHEMA_VERSION, "gate_version": GATE_VERSION,
        "provider": "openai-compatible" if real else "smoke",
        "model": provider.model, "endpoint_hash": endpoint_hash(provider.base_url) if real else "",
        "temperature": 0, "seed_supported": False, "git_commit": git_commit(), "mode": mode,
        "model_requests": len(cases) + sum(bool(case.get("expected_tools")) for case in cases),
        "route_accuracy": metrics["route_accuracy"], "followup_recall": metrics["followup_recall"],
        "tool_selection_recall": tool_metrics["tool_selection_recall"],
        "side_effects": dict(SIDE_EFFECTS),
        "side_effects_evidence": "selection-only-design-not-runtime-counters",
        "cases": [
            {**row, "raw_reply": raw, "tool_reply": tool_raw, "predicted_tools": tools["predicted_tools"]}
            for row, raw, tool_raw, tools in zip(metrics["rows"], raws, tool_raws, tool_metrics["rows"], strict=True)
        ],
    }


async def run(mode: str, report_dir: Path, out: Path | None = None) -> int:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    report_path = out if out is not None else report_dir / f"agent-chain-v0.1-{mode}-{stamp}.json"
    if not allowed_report_path(report_path):
        print("AGENT_EVAL_ERROR:report_path_out_of_scope")
        return 2
    if report_path.exists():
        print("AGENT_EVAL_ERROR:report_exists")
        return 2
    cases = load_cases()
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    if mode == "official":
        if (dataset_hash() != FROZEN_CASES_HASH
                or content_hash(INDEX_PATH) != FROZEN_INDEX_HASH):
            print("AGENT_EVAL_ERROR:frozen_dataset_required")
            return 2
        provider = get_llm_provider("intent")
        if (type(provider) is not OpenAICompatibleProvider or not provider.api_key.strip()
                or not provider.model.strip() or "mock" in provider.model.lower()
                or "smoke" in provider.model.lower() or provider.model == "unknown"
                or settings.LLM_RESPONSE_CACHE_ENABLED):
            print("AGENT_EVAL_ERROR:real_single_uncached_intent_required")
            return 2
        endpoint_hash(provider.base_url)
        if not settings.LLM_CAPABILITY_GUARD_ENABLED or provider._effective_capability() is None:
            print("AGENT_EVAL_ERROR:approved_model_capability_required")
            return 2
    elif mode == "smoke":
        provider = SmokeIntentModel()
    else:
        print("mode 只能是 smoke 或 official")
        return 2
    async with asyncio.timeout(TOTAL_TIMEOUT):
        routes, raws = await predict(provider, cases)
        tool_names, tool_raws = await predict_tools(provider, cases)
    report = build_report(mode, provider, cases, routes, raws, tool_names, tool_raws)
    write_report(report_path, report)
    print(report_path)
    if mode == "official":
        accuracy_ok = report["route_accuracy"] >= index["thresholds"]["route_accuracy"]
        recall_ok = report["followup_recall"] >= index["thresholds"]["followup_recall"]
        tool_ok = report["tool_selection_recall"] >= index["thresholds"]["tool_selection_recall"]
        return 0 if accuracy_ok and recall_ok and tool_ok else 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["smoke", "official"], required=True)
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        result = asyncio.run(run(args.mode, args.report_dir, args.out))
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 -- no API error bodies or credentials in logs
        print(f"AGENT_EVAL_ERROR:{type(exc).__name__}")
        result = 2
    raise SystemExit(result)


if __name__ == "__main__":
    main()
