"""Versioned input repair; deterministic evidence, never model quality evidence."""
import asyncio
import copy
import hashlib
import json
import subprocess
import sys

import pytest

from app.agents.pipeline import build_preflight_messages, build_tool_choice_messages
from scripts import agent_gate_check as gate
from scripts import run_agent_chain_eval as runner

OLD = runner.REPO_ROOT / "evals/datasets/agent-chain-v0.1"
NEW = runner.REPO_ROOT / "evals/datasets/agent-chain-v0.1.1"
OLD_HASHES = {
    "cases.json": "5bfad42d3a2d765363d3489036ea1b91346c6fff49ca58d13fe9147ff974905b",
    "index.json": "8cc903dbc9f406bc070bea620952396a2323e3b41a8eb44ca0a72044bc57669d",
}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_old_frozen_evidence_preserved():
    for name, digest in OLD_HASHES.items():
        assert hashlib.sha256((OLD / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest() == digest


def test_candidate_only_repairs_missing_history_and_versions():
    original = read(OLD / "cases.json")
    candidate = read(NEW / "cases.json")
    assert len(candidate) == len(original) == 10
    for before, after in zip(original, candidate, strict=True):
        normalized = copy.deepcopy(after)
        assert normalized["dataset_version"] == "0.1.1"
        normalized["dataset_version"] = before["dataset_version"]
        if before["case_id"] == "agent-004":
            assert [turn["role"] for turn in normalized["history"][:2]] == ["user", "assistant"]
            assert len(normalized["history"]) == 4
            normalized["history"] = normalized["history"][2:]
        assert normalized == before
    index = read(NEW / "index.json")
    assert index["dataset_version"] == "0.1.1"
    assert index["counts"] == read(OLD / "index.json")["counts"]
    assert index["thresholds"] == gate.THRESHOLDS
    assert index["tier"] == "silver" and index["gold_status"] == "not_gold"


def test_prior_code_is_executable_and_reaches_both_prompts(tmp_path):
    case = next(case for case in read(NEW / "cases.json") if case["case_id"] == "agent-004")
    content = case["history"][1]["content"]
    assert len(content) <= 300  # Older history is bounded by the existing Prompt builder.
    code = content.split("```python\n", 1)[1].split("```", 1)[0]
    assert "def bubble_sort" in code
    script = tmp_path / "synthetic_bubble.py"
    script.write_text(code, encoding="utf-8")
    result = subprocess.run([sys.executable, "-I", str(script)], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0
    assert result.stdout.strip() == "[1, 2, 4, 5, 8]"
    for builder in (build_preflight_messages, build_tool_choice_messages):
        messages = builder(case["history"], case["user_input"])
        prompt = "\n".join(message.content for message in messages)
        assert code in prompt
        assert all(field not in prompt for field in ("expected_route", "expected_tools", "critical_followup"))


def test_runner_and_independent_gate_bind_new_version():
    assert runner.CASES_PATH == NEW / "cases.json"
    assert runner.INDEX_PATH == NEW / "index.json"
    assert runner.dataset_hash() == runner.FROZEN_CASES_HASH == gate.FROZEN_CASES_HASH
    assert runner.content_hash(runner.INDEX_PATH) == runner.FROZEN_INDEX_HASH == gate.FROZEN_INDEX_HASH
    assert runner.FROZEN_CASES_HASH != OLD_HASHES["cases.json"]
    assert runner.FROZEN_INDEX_HASH != OLD_HASHES["index.json"]


def test_previous_dataset_binding_rejected_even_with_current_other_identity():
    cases = runner.load_cases()
    routes = [case["expected_route"] for case in cases]
    names, tools, records = asyncio.run(runner.predict_tools(runner.SmokeIntentModel(), cases))
    provider = runner.OpenAICompatibleProvider("https://isolated.invalid/v1", "synthetic-only", "test-model")
    report = runner.build_report("official", provider, cases, routes,
                                 ["YES" if route == "full" else "NO" for route in routes], names,
                                 tools, records)
    endpoint = runner.endpoint_hash(provider.base_url)
    assert gate.check_report(report, provider.model, endpoint) == []
    for field, value in (("dataset_version", "0.1.0"), ("dataset_hash", OLD_HASHES["cases.json"]),
                         ("index_hash", OLD_HASHES["index.json"])):
        stale = copy.deepcopy(report)
        stale[field] = value
        with pytest.raises(ValueError, match="binding_" + field):
            gate.check_report(stale, provider.model, endpoint)
