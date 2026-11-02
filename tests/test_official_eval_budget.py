"""Deterministic spending controls; no real provider calls."""

import asyncio
import json
import sys
from decimal import Decimal

import httpx
import pytest
import yaml

from scripts.run_official_eval import EvaluationBudget, require_authorization


def chat_request(**changes):
    return httpx.Request("POST", "https://api.deepseek.com/v1/chat/completions",
                         json={"model": "deepseek-flash", "messages": [], "max_tokens": 1024, **changes})


def test_reservations_persist_and_failed_attempts_are_not_refunded(tmp_path):
    path = tmp_path / "ledger.json"
    budget = EvaluationBudget(Decimal("0.03"), path)
    budget.reserve(chat_request())
    assert json.loads(path.read_text())["attempts"][0]["reserved_yuan"]
    reloaded = EvaluationBudget(Decimal("0.03"), path)
    with pytest.raises(ValueError, match="exhausted"):
        reloaded.reserve(chat_request())
    assert len(json.loads(path.read_text())["attempts"]) == 1


@pytest.mark.parametrize("changes", [{"model": "unknown"}, {"max_tokens": None},
                                     {"max_tokens": 1025}, {"stream": True}, {"n": 2}, {"n": True}])
def test_unpriced_or_unbounded_requests_never_reserve(tmp_path, changes):
    path = tmp_path / "ledger.json"
    with pytest.raises(ValueError):
        EvaluationBudget(Decimal(1), path).reserve(chat_request(**changes))
    assert not path.exists()


def test_embedding_reserves_without_persisting_text_or_credentials(tmp_path):
    path = tmp_path / "ledger.json"
    request = httpx.Request("POST", "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings",
                            json={"model": "text-embedding-v3", "input": ["synthetic-sentinel"]},
                            headers={"Authorization": "Bearer synthetic-key"})
    EvaluationBudget(Decimal(1), path).reserve(request)
    assert "sentinel" not in path.read_text() and "synthetic-key" not in path.read_text()


def test_unknown_destination_rejected(tmp_path):
    request = httpx.Request("POST", "https://unpriced.example/chat/completions", json={})
    with pytest.raises(ValueError, match="unpriced"):
        EvaluationBudget(Decimal(1), tmp_path / "ledger.json").reserve(request)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("redirects", [False, True])
def test_wrapper_reserves_each_transport_attempt_and_blocks_redirects(monkeypatch, tmp_path,
                                                                    asynchronous, redirects):
    from scripts import run_official_eval as runner

    path = tmp_path / "ledger.json"
    monkeypatch.setattr(runner, "require_authorization", lambda: None)
    monkeypatch.setattr(sys, "argv", ["run_official_eval.py", "--budget-yuan", "0.03",
                                     "--ledger", str(path), "scripts/run_agent_chain_eval.py"])
    sent = []

    def transport(request):
        sent.append(request)
        return httpx.Response(500)

    def execute(*args, **kwargs):
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(transport),
                                         follow_redirects=redirects) as client:
                await client.send(chat_request())
                await client.send(chat_request())

        if asynchronous:
            asyncio.run(run())
        else:
            with httpx.Client(transport=httpx.MockTransport(transport), follow_redirects=redirects) as client:
                client.send(chat_request())
                client.send(chat_request())

    monkeypatch.setattr(runner.runpy, "run_path", execute)
    with pytest.raises(ValueError, match="redirects_forbidden" if redirects else "budget_exhausted"):
        runner.main()
    assert len(sent) == (0 if redirects else 1)
    assert path.exists() is not redirects


def test_exact_run_commit_attempt_required(monkeypatch):
    env = {"OFFICIAL_EVALUATION_AUTHORIZED": "true", "OFFICIAL_EVALUATION_BUDGET_REF": "approved-5-CNY",
           "GITHUB_SHA": "synthetic-sha", "OFFICIAL_EVALUATION_AUTHORIZED_SHA": "synthetic-sha",
           "GITHUB_RUN_ID": "42", "OFFICIAL_EVALUATION_AUTHORIZED_RUN_ID": "42",
           "GITHUB_RUN_ATTEMPT": "2", "OFFICIAL_EVALUATION_AUTHORIZED_RUN_ATTEMPT": "2"}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    require_authorization()
    for key in env:
        monkeypatch.setenv(key, "")
        with pytest.raises(ValueError, match="NOT_AUTHORIZED"):
            require_authorization()
        monkeypatch.setenv(key, env[key])


def test_workflow_allocations_total_five_yuan():
    from pathlib import Path

    workflow = yaml.safe_load(Path(".github/workflows/ci.yml").read_text(encoding="utf-8"))
    allocations = []
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            for command in step.get("run", "").splitlines():
                if "python scripts/run_official_eval.py " in command:
                    allocations.append(Decimal(command.split("--budget-yuan ")[1].split()[0]))
    assert sorted(allocations) == [1, 1, 3]
    assert sum(allocations) == 5
