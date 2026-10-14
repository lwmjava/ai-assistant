"""Runner entry regressions; synthetic providers never prove model quality."""

import asyncio
import json
from pathlib import Path

import pytest
import yaml

from app.agents import pipeline
from scripts import run_agent_chain_eval as runner


@pytest.mark.parametrize("empty", ["", " \n", None])
def test_empty_response_recovers_once_with_identical_input(isolated_real_transport, monkeypatch, tmp_path, empty):
    from scripts import agent_gate_check as gate

    provider, _ = isolated_real_transport
    physical = []

    async def reply(self, messages, options):
        physical.append((messages, options.model_dump()))
        if len(physical) == 11:
            raise runner.LLMResponseError("llm_empty_response", finish_reason="stop", reply=empty)
        return await runner.SmokeIntentModel().chat(messages, options)

    monkeypatch.setattr(runner.OpenAICompatibleProvider, "chat", reply)
    target = tmp_path / "recovered.json"
    assert asyncio.run(runner.run("official", tmp_path, target)) == 0
    report = json.loads(target.read_text(encoding="utf-8"))
    assert len(physical) == report["model_requests"] == 13
    assert physical[10] == physical[11]
    assert report["logical_requests"] == 12 and report["empty_response_retries"] == 1
    attempts = report["call_attempts"][10]["attempts"]
    assert attempts[0]["reply"] == empty
    assert attempts[0]["error_code"] == "llm_empty_response"
    assert attempts[1]["reply"] == report["cases"][2]["tool_reply"]
    assert gate.check_report(report, provider.model, report["endpoint_hash"]) == []


@pytest.mark.parametrize("reason,native", [("length", False), ("content_filter", False),
                                          ("other", False), ("tool_calls", True), ("stop", True)])
def test_ineligible_empty_is_not_retried(isolated_real_transport, monkeypatch, tmp_path, reason, native):
    from scripts import agent_gate_check as gate

    provider, _ = isolated_real_transport
    physical = []

    async def reply(self, messages, options):
        physical.append(messages)
        if len(physical) == 11:
            raise runner.LLMResponseError("llm_empty_response", finish_reason=reason, native_tool_calls=native)
        return await runner.SmokeIntentModel().chat(messages, options)

    monkeypatch.setattr(runner.OpenAICompatibleProvider, "chat", reply)
    target = tmp_path / "failed.json"
    assert asyncio.run(runner.run("official", tmp_path, target)) == 1
    report = json.loads(target.read_text(encoding="utf-8"))
    assert len(physical) == report["model_requests"] == 12
    assert report["empty_response_retries"] == 0
    assert "empty_model_response" in gate.check_report(report, provider.model, report["endpoint_hash"])


def test_run_recovery_budget_is_global_and_wrong_answers_never_retry(isolated_real_transport, monkeypatch, tmp_path):
    from scripts import agent_gate_check as gate

    provider, _ = isolated_real_transport
    physical = []

    async def reply(self, messages, options):
        physical.append(messages)
        if len(physical) in {3, 12}:
            raise runner.LLMResponseError("llm_empty_response", finish_reason="stop")
        if len(physical) == 4:
            return "NO"  # Recovery remains a wrong nonempty answer; no best-of sampling.
        return await runner.SmokeIntentModel().chat(messages, options)

    monkeypatch.setattr(runner.OpenAICompatibleProvider, "chat", reply)
    target = tmp_path / "failed.json"
    assert asyncio.run(runner.run("official", tmp_path, target)) == 1
    report = json.loads(target.read_text(encoding="utf-8"))
    assert len(physical) == report["model_requests"] == 13
    assert report["cases"][2]["raw_reply"] == "NO"
    assert len(report["call_attempts"][10]["attempts"]) == 1
    failed = gate.check_report(report, provider.model, report["endpoint_hash"])
    assert "followup_recall" in failed and "empty_model_response" in failed


def test_official_rejects_mock_intent_even_when_chat_is_real(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "llm_availability", lambda: "real", raising=False)
    monkeypatch.setattr(runner, "get_llm_provider", lambda task: runner.SmokeIntentModel())
    result = asyncio.run(runner.run("official", tmp_path))
    assert result == 2
    assert list(tmp_path.iterdir()) == []


def test_prompt_fingerprint_covers_action_prompt(monkeypatch):
    before = runner.prompt_hash()
    monkeypatch.setattr(pipeline, "SYSTEM_ACT", pipeline.SYSTEM_ACT + "\nchanged action policy")
    assert runner.prompt_hash() != before


@pytest.fixture
def isolated_real_transport(monkeypatch):
    """Real provider class with isolated transport: never E1 quality evidence."""
    provider = runner.OpenAICompatibleProvider("https://isolated.invalid/v1", "isolated-test-key", "deepseek-chat")
    calls = []

    async def reply(self, messages, options):
        calls.append(messages)
        return await runner.SmokeIntentModel().chat(messages, options)

    monkeypatch.setattr(runner.OpenAICompatibleProvider, "chat", reply)
    monkeypatch.setattr(runner.OpenAICompatibleProvider, "_effective_capability", lambda self: object())
    monkeypatch.setattr(runner, "get_llm_provider", lambda task: provider)
    monkeypatch.setattr(runner.settings, "LLM_RESPONSE_CACHE_ENABLED", False)
    monkeypatch.setattr(runner.settings, "LLM_CAPABILITY_GUARD_ENABLED", True)
    return provider, calls


def test_complete_official_entry_with_isolated_transport(isolated_real_transport, tmp_path):
    provider, calls = isolated_real_transport
    target = tmp_path / "official.json"
    assert asyncio.run(runner.run("official", tmp_path, target)) == 0
    report = json.loads(target.read_text(encoding="utf-8"))
    assert len(calls) == report["model_requests"] == 12
    assert report["endpoint_hash"] == runner.endpoint_hash(provider.base_url)
    assert report["model"] == provider.model
    assert report["provider"] == "openai-compatible"
    assert report["side_effects_evidence"] == runner.SIDE_EFFECTS_EVIDENCE
    assert report["tool_call_validity_recall"] == 1.0
    assert sum(row["tool_record"]["recorded_calls"] for row in report["cases"]) == 2
    assert report["report_schema_version"] == runner.REPORT_SCHEMA_VERSION
    assert report["source_hashes"] == runner.report_identity()["source_hashes"]
    assert all(not any(token in message.content for token in ("expected_route", "expected_tools"))
               for messages in calls for message in messages)


def test_existing_report_rejected_before_model_call(isolated_real_transport, tmp_path):
    _, calls = isolated_real_transport
    target = tmp_path / "previous.json"
    target.write_bytes(b"previous evidence")
    assert asyncio.run(runner.run("official", tmp_path, target)) == 2
    assert target.read_bytes() == b"previous evidence"
    assert calls == []


def test_existing_failed_ledger_rejected_before_model_call(isolated_real_transport, tmp_path):
    _, calls = isolated_real_transport
    target = tmp_path / "previous.json"
    ledger = target.with_suffix(".attempts.json")
    ledger.write_bytes(b"previous failed evidence")
    assert asyncio.run(runner.run("official", tmp_path, target)) == 2
    assert ledger.read_bytes() == b"previous failed evidence"
    assert calls == [] and not target.exists()


def test_partial_remote_failure_leaves_no_report(isolated_real_transport, monkeypatch, tmp_path):
    calls = []

    async def failing(self, messages, options):
        calls.append(messages)
        if len(calls) == 3:
            raise ConnectionError("isolated failure")
        return "NO"

    monkeypatch.setattr(runner.OpenAICompatibleProvider, "chat", failing)
    with pytest.raises(ConnectionError):
        asyncio.run(runner.run("official", tmp_path, tmp_path / "failed.json"))
    assert len(calls) == 3
    assert not (tmp_path / "failed.json").exists()
    ledger = json.loads((tmp_path / "failed.attempts.json").read_text(encoding="utf-8"))
    assert ledger["mode"] == "failed-attempts" and ledger["model_requests"] == 3


def test_model_call_timeout_leaves_no_report(isolated_real_transport, monkeypatch, tmp_path):
    async def delayed(self, messages, options):
        await asyncio.sleep(0.1)
        return "NO"

    monkeypatch.setattr(runner.OpenAICompatibleProvider, "chat", delayed)
    monkeypatch.setattr(runner, "CALL_TIMEOUT", 0.001)
    with pytest.raises(TimeoutError):
        asyncio.run(runner.run("official", tmp_path))
    assert len(list(tmp_path.glob("*.attempts.json"))) == 1


def test_overall_timeout_leaves_no_report(isolated_real_transport, monkeypatch, tmp_path):
    async def delayed(self, messages, options):
        await asyncio.sleep(0.02)
        return "NO"

    monkeypatch.setattr(runner.OpenAICompatibleProvider, "chat", delayed)
    monkeypatch.setattr(runner, "TOTAL_TIMEOUT", 0.001)
    with pytest.raises(TimeoutError):
        asyncio.run(runner.run("official", tmp_path))
    assert len(list(tmp_path.glob("*.attempts.json"))) == 1


@pytest.mark.parametrize("value", [None, 5, "x" * (16 * 1024 + 1), "界" * 6000],
                         ids=["null", "number", "oversize-ascii", "oversize-utf8"])
def test_bad_or_oversized_remote_reply(isolated_real_transport, monkeypatch, tmp_path, value):
    async def reply(self, messages, options):
        return value

    monkeypatch.setattr(runner.OpenAICompatibleProvider, "chat", reply)
    with pytest.raises(ValueError, match="invalid_reply"):
        asyncio.run(runner.run("official", tmp_path))
    ledger_path = next(tmp_path.glob("*.attempts.json"))
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert ledger["model_requests"] == 1
    assert ledger["call_attempts"][0]["attempts"][0]["reply"] is None


def test_recovery_timeout_preserves_both_attempts(isolated_real_transport, monkeypatch, tmp_path):
    physical = []

    async def reply(self, messages, options):
        physical.append(messages)
        if len(physical) == 1:
            raise runner.LLMResponseError("llm_empty_response", finish_reason="stop")
        await asyncio.sleep(0.1)
        return "NO"

    monkeypatch.setattr(runner.OpenAICompatibleProvider, "chat", reply)
    monkeypatch.setattr(runner, "CALL_TIMEOUT", 0.01)
    target = tmp_path / "failed.json"
    with pytest.raises(TimeoutError):
        asyncio.run(runner.run("official", tmp_path, target))
    assert len(physical) == 2 and not target.exists()
    ledger = json.loads(target.with_suffix(".attempts.json").read_text(encoding="utf-8"))
    assert ledger["model_requests"] == 2 and ledger["empty_response_retries"] == 1
    assert len(ledger["call_attempts"][0]["attempts"]) == 2
    assert ledger["call_attempts"][0]["attempts"][0]["error_code"] == "llm_empty_response"
    assert ledger["call_attempts"][0]["attempts"][1]["error_code"] == "model_call_failed"


def test_cached_provider_cannot_be_official(isolated_real_transport, monkeypatch, tmp_path):
    _, calls = isolated_real_transport
    monkeypatch.setattr(runner.settings, "LLM_RESPONSE_CACHE_ENABLED", True)
    assert asyncio.run(runner.run("official", tmp_path)) == 2
    assert calls == []


def test_unapproved_capability_rejected_before_requests(isolated_real_transport, monkeypatch, tmp_path):
    _, calls = isolated_real_transport
    monkeypatch.setattr(runner.OpenAICompatibleProvider, "_effective_capability", lambda self: None)
    assert asyncio.run(runner.run("official", tmp_path)) == 2
    assert calls == []
    assert list(tmp_path.iterdir()) == []


def test_dataset_changes_rejected_before_data_sent(isolated_real_transport, monkeypatch, tmp_path):
    _, calls = isolated_real_transport
    monkeypatch.setattr(runner, "dataset_hash", lambda: "a" * 64)
    assert asyncio.run(runner.run("official", tmp_path)) == 2
    assert calls == []


def test_frozen_dataset_accepts_lf_newlines(isolated_real_transport, monkeypatch, tmp_path):
    for name, attribute in [("cases.json", "CASES_PATH"), ("index.json", "INDEX_PATH")]:
        blob = getattr(runner, attribute).read_bytes().replace(b"\r\n", b"\n")
        target = tmp_path / name
        target.write_bytes(blob)
        monkeypatch.setattr(runner, attribute, target)
    assert asyncio.run(runner.run("official", tmp_path, tmp_path / "report.json")) == 0


@pytest.mark.parametrize("url", ["https://user:secret@example.invalid/v1", "file:///tmp/x",
                                 "https://example.invalid/v1?key=secret", "https://example.invalid/v1#x"])
def test_endpoint_rejects_credentials_or_ambiguous_config(url):
    with pytest.raises(ValueError, match="invalid_endpoint"):
        runner.endpoint_hash(url)


def test_atomic_publish_failure_preserves_existing_evidence(tmp_path, monkeypatch):
    target = tmp_path / "report.json"

    def fail(*args):
        raise OSError("isolated publication failure")

    monkeypatch.setattr(runner.os, "link", fail)
    with pytest.raises(OSError):
        runner.write_report(target, {"mode": "smoke"})
    assert list(tmp_path.iterdir()) == []


def test_report_out_of_scope_rejected_before_publication(monkeypatch):
    def forbidden_publication(*args):
        raise AssertionError("report publication attempted outside evaluation locations")

    monkeypatch.setattr(runner.os, "link", forbidden_publication)
    with pytest.raises(ValueError, match="report_path_out_of_scope"):
        runner.write_report(runner.REPO_ROOT / "scripts/forbidden-report.json", {"mode": "smoke"})


def test_ci_is_independent_fail_closed_and_secrets_scoped():
    ci = yaml.load((Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml").read_text(),
                   Loader=yaml.BaseLoader)
    assert set(ci["on"]) == {"push", "pull_request"}
    assert ci["permissions"] == {"contents": "read"}
    job = ci["jobs"]["agent-baseline"]
    assert "needs" not in job and "if" not in job
    assert "LLM_API_KEY" not in job["env"]
    assert job["env"]["LLM_INTENT_FALLBACK_CHAIN"] == "chat"
    assert job["env"]["LLM_RESPONSE_CACHE_ENABLED"] == "false"
    steps = job["steps"]
    for step in steps:
        assert "continue-on-error" not in step
        if "LLM_API_KEY" in step.get("env", {}):
            assert step["name"] == "Agent official evaluation"
            assert "--mode official" in step["run"]
            assert "if" not in step
    check = next(step for step in steps if step.get("name") == "Agent report gate and regression probes")
    assert "agent_gate_check.py" in check["run"] and "agent_gate_probe.py" in check["run"]
    assert "--expected-model" in check["run"] and "--expected-endpoint-hash" in check["run"]
    artifact = next(step for step in steps if step.get("uses", "").startswith("actions/upload-artifact"))
    assert artifact["with"]["path"] == "evals/reports/agent-ci/"
