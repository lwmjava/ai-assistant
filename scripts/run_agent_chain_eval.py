"""意图分流和工具选择评测。

冒烟模式使用不读取标准答案的本地模型，只证明提示、评分和报告链路。
正式模式只调用真实模型。工具提议经参数校验后交给内存记录工具，不执行真实工具，
不创建会话，不连接检索、沙箱、MCP 或数据库。
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import json
import math
import os
import re
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
    native_tool_definitions,
    preflight_needs_full_pipeline,
    selection_tool_registry,
)
from app.agents.tools.base import Tool, inspect_tool_call  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.llm.base import LLMOptions  # noqa: E402
from app.llm.factory import get_llm_provider  # noqa: E402
from app.llm.openai_compatible import LLMResponseError, OpenAICompatibleProvider  # noqa: E402

EVALUATOR_VERSION = "agent-chain-eval-0.5.0"
# Compatibility declaration: no Tool/DB/RAG execution; these are NOT runtime counters.
SIDE_EFFECTS = {"sandbox": 0, "mcp": 0, "network": 0, "rag": 0, "database": 0}
REPORT_SCHEMA_VERSION = "agent-chain-report-3.0.0"
GATE_VERSION = "agent-chain-gate-3.0.0"
CALL_VALIDITY_THRESHOLD = 1.0
SIDE_EFFECTS_EVIDENCE = "recording-only-no-real-tool-execution-design-not-runtime-counters"
FROZEN_CASES_HASH = "77ff27c8c67c68afb55eed51384e20dd07171b1d047de6cde346c976f38f5ad6"
FROZEN_INDEX_HASH = "929e4114ef2f548de0e2d89e3d28f8ebb174f57caee39273c911fd3518c58251"
DATA_HASH_ALGORITHM = "sha256-utf8-crlf-to-lf-v1"
MAX_REPORT_BYTES = 1024 * 1024
MAX_REPLY_BYTES = 16 * 1024
CALL_TIMEOUT = 60.0
TOTAL_TIMEOUT = 780.0
CASES_PATH = REPO_ROOT / "evals" / "datasets" / "agent-chain-v0.1.1" / "cases.json"
INDEX_PATH = REPO_ROOT / "evals" / "datasets" / "agent-chain-v0.1.1" / "index.json"
REPORT_DIR = REPO_ROOT / "evals" / "reports"


class SmokeIntentModel:
    """只看送审提示的本地分流器，不读取 expected 字段。"""

    model = "smoke-intent"

    async def chat(self, messages, options=None) -> str:
        user = messages[-1].content if messages else ""
        if "## 可用外部工具" in user:
            latest = user.split("## 用户消息\n", 1)[-1].split("\n\n", 1)[0].strip()
            if "code_sandbox" in user and latest in {"1", "好的"}:
                code = "print(1)"
                if "```python\n" in user:
                    code = user.split("```python\n", 1)[1].split("```", 1)[0]
                payload = json.dumps({"name": "code_sandbox", "arguments": {"code": code}})
                return "<tool_call>" + payload + "</tool_call>"
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
    inputs.append({
        "stage": "native_tool_schema",
        "tools": [item.model_dump(mode="json") for item in native_tool_definitions(selection_tool_registry())],
    })
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


def schema_valid(value, schema: dict) -> bool:
    """Fail-closed validator for the declared Schema subset; no extra dependency.

    Unsupported validation keywords are an evaluator configuration error,
    never silently ignored. Used for the production tools and case contracts.
    """
    supported = {"$schema", "$id", "title", "description", "default", "type", "properties", "required",
                 "additionalProperties", "enum", "const", "items", "minItems", "maxItems",
                 "minLength", "maxLength", "pattern", "minimum", "maximum"}
    if set(schema) - supported:
        raise ValueError("unsupported_schema")
    kind = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "boolean": bool, "integer": int}
    if kind == "number":
        if type(value) not in {int, float}:
            return False
        try:
            if not math.isfinite(value):
                return False
        except OverflowError:
            return False
    elif kind is not None:
        if kind not in types:
            raise ValueError("unsupported_schema_type")
        if type(value) is not types[kind]:
            return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if "const" in schema and (type(value) is not type(schema["const"]) or value != schema["const"]):
        return False
    if kind == "object":
        properties = schema.get("properties", {})
        if any(key not in value for key in schema.get("required", [])):
            return False
        if schema.get("additionalProperties") is False and set(value) - properties.keys():
            return False
        if any(not schema_valid(item, properties[key]) for key, item in value.items() if key in properties):
            return False
    if kind in {"array", "string"}:
        lo, hi = ("minItems", "maxItems") if kind == "array" else ("minLength", "maxLength")
        if len(value) < schema.get(lo, 0) or len(value) > schema.get(hi, math.inf):
            return False
    if kind == "array" and "items" in schema:
        return all(schema_valid(item, schema["items"]) for item in value)
    if kind == "string" and "pattern" in schema and re.search(schema["pattern"], value) is None:
        return False
    if kind in {"number", "integer"}:
        if value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf):
            return False
    return True


def _unique_call(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("invalid_envelope")
        value[key] = item
    return value


def _nonfinite_call(value: str) -> None:
    raise ValueError("invalid_parameters")


def _validate_call(case: dict, raw: str) -> dict:
    record = {"intent": "not_requested", "tool_name": "", "arguments_valid": False,
              "error_code": "", "arguments_hash": "", "recording_attempts": 0,
              "recorded_calls": 0, "recording_result": "not_attempted"}
    if not case.get("expected_tools"):
        return record
    try:
        intent = inspect_tool_call(raw)
        record["intent"] = intent.status
        if intent.status != "ok" or intent.call is None:
            record["error_code"] = "no_tool_call" if intent.status == "none" else "invalid_envelope"
            return record
        # Standard JSON keeps its independent strict check. DSML was already
        # decoded with duplicate/nonfinite rejection by the shared parser.
        if "<tool_call>" in raw:
            payload = raw.split("<tool_call>", 1)[1].split("</tool_call>", 1)[0]
            json.loads(payload, object_pairs_hook=_unique_call,
                       parse_constant=_nonfinite_call)
        record["tool_name"] = intent.call.name
        tool = selection_tool_registry().get(intent.call.name)
        if tool is None:
            record["error_code"] = "tool_not_allowed"
            return record
        arguments = intent.call.arguments
        if not schema_valid(arguments, tool.parameters):
            record["error_code"] = "invalid_parameters"
            return record
        code = arguments.get("code") if tool.name == "code_sandbox" else None
        if tool.name == "code_sandbox":
            timeout = arguments.get("timeout", 30)
            if not code.strip() or timeout <= 0:
                record["error_code"] = "invalid_parameters"
                return record
            try:
                tree = ast.parse(code)
                if not tree.body:
                    record["error_code"] = "invalid_code"
                    return record
            except (SyntaxError, ValueError, RecursionError):
                record["error_code"] = "invalid_code"
                return record
            if case["case_id"] == "agent-004":
                prior = [turn["content"] for turn in case["history"]
                         if turn["role"] == "assistant" and "```python\n" in turn["content"]]
                if not prior:
                    record["error_code"] = "missing_prior_code"
                    return record
                expected = prior[-1].split("```python\n", 1)[1].split("```", 1)[0]
                if ast.dump(tree) != ast.dump(ast.parse(expected)):
                    record["error_code"] = "prior_code_mismatch"
                    return record
        if tool.name == "calculator" and not arguments["expression"].strip():
            record["error_code"] = "invalid_parameters"
            return record
        record["arguments_hash"] = hashlib.sha256(json.dumps(
            arguments, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
        record["arguments_valid"] = True
        return record
    except (ValueError, TypeError, RecursionError):
        record["error_code"] = "invalid_parameters"
        return record


def expected_tool_record(case: dict, raw: str) -> dict:
    """Pure replay contract; not a claim that the recording callback was invoked."""
    record = _validate_call(case, raw)
    if record["arguments_valid"]:
        record.update(recording_attempts=1, recorded_calls=1, recording_result="recorded")
    return record


async def record_tool_call(case: dict, raw: str) -> dict:
    """Execute only an isolated Tool callback which records a valid proposal."""
    record = _validate_call(case, raw)
    if not record["arguments_valid"]:
        return record
    intent = inspect_tool_call(raw)
    assert intent.call is not None
    metadata = selection_tool_registry().get(intent.call.name)
    assert metadata is not None

    def recording_fake(arguments: dict) -> str:
        record["recorded_calls"] += 1
        return "recorded"

    fake = Tool(metadata.name, metadata.description, metadata.parameters, recording_fake, metadata.contract)
    record["recording_attempts"] += 1
    record["recording_result"] = await fake.execute(intent.call.arguments)
    return record


def score_valid_calls(cases: list[dict], records: list[dict]) -> float:
    hits = [record["arguments_valid"] and record["recorded_calls"] == 1
            and record["tool_name"] in case["expected_tools"]
            for case, record in zip(cases, records, strict=True) if case.get("expected_tools")]
    return sum(hits) / len(hits) if hits else 1.0


async def predict_tools(provider, cases: list[dict], calls=None) -> tuple[list[list[str]], list[str], list[dict]]:
    """Propose once, validate, then invoke an in-memory recording Tool only."""
    options = LLMOptions(
        temperature=0,
        timeout=CALL_TIMEOUT,
        tools=native_tool_definitions(selection_tool_registry()),
    )
    chosen: list[list[str]] = []
    raws: list[str] = []
    records: list[dict] = []
    for case in cases:
        if not case.get("expected_tools"):
            chosen.append([])
            raws.append("")
            records.append(await record_tool_call(case, ""))
            continue
        messages = build_tool_choice_messages(case["history"], case["user_input"])
        blob = "\n".join(message.content for message in messages)
        if any(token in blob for token in ("expected_route", "expected_tools", "critical_followup")):
            raise RuntimeError("工具选择提示包含标准答案字段")
        raw = await model_reply(provider, messages, options, calls=calls,
                                case_id=case["case_id"], stage="tool_choice")
        intent = inspect_tool_call(raw)
        name = intent.call.name if intent.status == "ok" and intent.call is not None else ""
        chosen.append([name] if name else [])
        raws.append(raw)
        records.append(await record_tool_call(case, raw))
    return chosen, raws, records


async def predict(provider, cases: list[dict], calls=None) -> tuple[list[str], list[str]]:
    options = LLMOptions(temperature=0, timeout=CALL_TIMEOUT)
    routes: list[str] = []
    raws: list[str] = []
    for case in cases:
        messages = build_preflight_messages(case["history"], case["user_input"])
        blob = "\n".join(message.content for message in messages)
        if any(token in blob for token in ("expected_route", "expected_tools", "critical_followup")):
            raise RuntimeError("评测提示包含标准答案字段")
        raw = await model_reply(provider, messages, options, calls=calls,
                                case_id=case["case_id"], stage="route")
        route = "short" if not preflight_needs_full_pipeline(raw) else "full"
        routes.append(route)
        raws.append(raw)
    return routes, raws


def reply_attempt(raw: str) -> dict:
    if not raw.strip():
        return {"reply": raw, "error_code": "llm_empty_response",
                "finish_reason": "missing", "native_tool_calls": False}
    return {"reply": raw, "error_code": "", "finish_reason": "unavailable",
            "native_tool_calls": None}


class EvaluationCalls:
    """One run owns one recovery budget; providers never retry implicitly."""

    def __init__(self) -> None:
        self.groups: list[dict] = []
        self.retries = 0


async def model_reply(provider, messages, options, *, calls=None, case_id="", stage="") -> str:
    attempts: list[dict] = []
    if calls is not None:
        calls.groups.append({"case_id": case_id, "stage": stage, "attempts": attempts})
    async with asyncio.timeout(CALL_TIMEOUT):
        while True:
            attempts.append({"reply": None, "error_code": "model_call_failed",
                             "finish_reason": "unavailable", "native_tool_calls": None})
            try:
                raw = await provider.chat(messages, options)
                if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_REPLY_BYTES:
                    raise ValueError("invalid_reply")
                key = getattr(provider, "api_key", "")
                if key and key in raw:
                    raise ValueError("unsafe_reply")
                if not raw.strip():
                    raise LLMResponseError("llm_empty_response", reply=raw)
                attempts[-1] = reply_attempt(raw)
                return raw
            except LLMResponseError as exc:
                if exc.error_code == "llm_invalid_tool_call":
                    raw = "<tool_call></tool_call>"
                    attempts[-1] = reply_attempt(raw)
                    return raw
                if exc.error_code != "llm_empty_response":
                    raise
                if exc.reply is not None and len(exc.reply.encode("utf-8")) > MAX_REPLY_BYTES:
                    raise ValueError("invalid_reply") from None
                attempts[-1] = {"reply": exc.reply, "error_code": exc.error_code,
                                "finish_reason": exc.finish_reason,
                                "native_tool_calls": exc.native_tool_calls}
                if (calls is not None and calls.retries == 0 and len(attempts) == 1
                        and not exc.native_tool_calls and exc.finish_reason in {"stop", "missing"}):
                    calls.retries += 1
                    continue
                return exc.reply or ""


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


def real_single_uncached_intent(provider) -> bool:
    """正式评测只接受一个未缓存的真实兼容客户端。单跳熔断包装仍是这一个客户端。"""
    if settings.LLM_RESPONSE_CACHE_ENABLED or not isinstance(provider, OpenAICompatibleProvider):
        return False
    key = getattr(provider, "api_key", "")
    model = getattr(provider, "model", "")
    if not isinstance(key, str) or not key.strip() or not isinstance(model, str) or not model.strip():
        return False
    folded = model.lower()
    return "mock" not in folded and "smoke" not in folded and model != "unknown"


def build_report(mode, provider, cases, routes, raws, tool_names, tool_raws, records, call_attempts=None) -> dict:
    metrics = score(cases, routes)
    tool_metrics = score_tools(cases, tool_names)
    real = real_single_uncached_intent(provider)
    if call_attempts is None:
        call_attempts = [{"case_id": case["case_id"], "stage": "route", "attempts": [reply_attempt(raw)]}
                         for case, raw in zip(cases, raws, strict=True)]
        call_attempts += [{"case_id": case["case_id"], "stage": "tool_choice", "attempts": [reply_attempt(raw)]}
                          for case, raw in zip(cases, tool_raws, strict=True) if case.get("expected_tools")]
    logical_requests = len(cases) + sum(bool(case.get("expected_tools")) for case in cases)
    physical_requests = sum(len(group["attempts"]) for group in call_attempts)
    return {
        **report_identity(cases), "report_schema_version": REPORT_SCHEMA_VERSION, "gate_version": GATE_VERSION,
        "provider": "openai-compatible" if real else "smoke",
        "model": provider.model, "endpoint_hash": endpoint_hash(provider.base_url) if real else "",
        "temperature": 0, "seed_supported": False, "git_commit": git_commit(), "mode": mode,
        "logical_requests": logical_requests, "model_requests": physical_requests,
        "empty_response_retries": physical_requests - logical_requests, "call_attempts": call_attempts,
        "route_accuracy": metrics["route_accuracy"], "followup_recall": metrics["followup_recall"],
        "tool_selection_recall": tool_metrics["tool_selection_recall"],
        "tool_call_validity_recall": score_valid_calls(cases, records),
        "side_effects": dict(SIDE_EFFECTS),
        "side_effects_evidence": SIDE_EFFECTS_EVIDENCE,
        "tool_reply_encoding": "client_normalized_internal_envelope",
        "cases": [
            {**row, "raw_reply": raw, "tool_reply": tool_raw, "predicted_tools": tools["predicted_tools"],
             "tool_record": record}
            for row, raw, tool_raw, tools, record in zip(
                metrics["rows"], raws, tool_raws, tool_metrics["rows"], records, strict=True)
        ],
    }


async def run(mode: str, report_dir: Path, out: Path | None = None) -> int:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    report_path = out if out is not None else report_dir / f"agent-chain-v0.1-{mode}-{stamp}.json"
    if not allowed_report_path(report_path):
        print("AGENT_EVAL_ERROR:report_path_out_of_scope")
        return 2
    if report_path.exists() or report_path.with_suffix(".attempts.json").exists():
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
        if not real_single_uncached_intent(provider):
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
    calls = EvaluationCalls()
    try:
        async with asyncio.timeout(TOTAL_TIMEOUT):
            routes, raws = await predict(provider, cases, calls)
            tool_names, tool_raws, records = await predict_tools(provider, cases, calls)
    except (Exception, asyncio.CancelledError):
        write_report(report_path.with_suffix(".attempts.json"), {
            **report_identity(cases), "report_schema_version": "agent-chain-failed-attempts-1.0.0",
            "mode": "failed-attempts", "requested_mode": mode, "git_commit": git_commit(),
            "model": provider.model,
            "endpoint_hash": endpoint_hash(provider.base_url) if mode == "official" else "",
            "model_requests": sum(len(group["attempts"]) for group in calls.groups),
            "empty_response_retries": calls.retries, "call_attempts": calls.groups,
        })
        raise
    report = build_report(mode, provider, cases, routes, raws, tool_names, tool_raws, records, calls.groups)
    write_report(report_path, report)
    print(report_path)
    if mode == "official":
        accuracy_ok = report["route_accuracy"] >= index["thresholds"]["route_accuracy"]
        recall_ok = report["followup_recall"] >= index["thresholds"]["followup_recall"]
        tool_ok = report["tool_selection_recall"] >= index["thresholds"]["tool_selection_recall"]
        valid_ok = report["tool_call_validity_recall"] >= CALL_VALIDITY_THRESHOLD
        empty_ok = all(not group["attempts"][-1]["error_code"] for group in calls.groups)
        for name, ok in (("route_accuracy", accuracy_ok), ("followup_recall", recall_ok),
                         ("tool_selection_recall", tool_ok), ("tool_call_validity_recall", valid_ok),
                         ("empty_model_response", empty_ok)):
            if not ok:
                print("AGENT_EVAL_METRIC:" + name)
        for group in calls.groups:
            for number, attempt in enumerate(group["attempts"], 1):
                if attempt["error_code"]:
                    print(f"AGENT_EVAL_RESPONSE:{group['case_id']}:{group['stage']}:{number}:"
                          f"{attempt['error_code']}:{attempt['finish_reason']}")
        return 0 if accuracy_ok and recall_ok and tool_ok and valid_ok and empty_ok else 1
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
