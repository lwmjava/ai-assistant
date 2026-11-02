"""Run one authorized CI evaluation with durable, conservative HTTP reservations."""

from __future__ import annotations

import argparse
import json
import os
import runpy
import sys
import threading
from decimal import Decimal
from pathlib import Path

import httpx


class EvaluationBudget:
    """Reserve before each attempt; failures and unknown usage are never refunded."""

    def __init__(self, limit: Decimal, ledger: Path):
        if not limit.is_finite() or not 0 < limit <= 3:
            raise ValueError("invalid_evaluation_budget")
        self.limit = limit
        self.ledger = ledger
        self.lock = threading.Lock()
        self.rows = json.loads(ledger.read_text(encoding="utf-8"))["attempts"] if ledger.exists() else []

    def reserve(self, request: httpx.Request) -> None:
        """Only current, explicitly priced official deployments are eligible."""
        url = request.url
        if (request.method != "POST" or url.scheme != "https" or url.port not in (None, 443)
                or url.username or url.password):
            raise ValueError("unpriced_evaluation_request")
        payload = json.loads(request.content)
        if (url.host == "api.deepseek.com" and url.path == "/v1/chat/completions"
                and payload.get("model") == "deepseek-flash"):
            output = payload.get("max_tokens")
            if (type(output) is not int or not 0 < output <= 1024 or payload.get("stream")
                    or type(payload.get("n", 1)) is not int or payload.get("n", 1) != 1):
                raise ValueError("unbounded_evaluation_output")
            # UTF-8 byte upper bound plus conservative framing allowance, at
            # peak cache-miss prices (CNY per token), verified 2026-11-02.
            amount = Decimal(len(request.content) * 4 + 4096) * Decimal("0.000002")
            amount += Decimal(output) * Decimal("0.000008")
        elif (url.host == "dashscope.aliyuncs.com" and url.path == "/compatible-mode/v1/embeddings"
              and payload.get("model") == "text-embedding-v3"):
            texts = payload.get("input")
            if not isinstance(texts, list) or not texts or any(not isinstance(t, str) for t in texts):
                raise ValueError("invalid_evaluation_embedding_input")
            amount = Decimal(sum(len(t.encode("utf-8")) * 4 + 256 for t in texts)) * Decimal("0.0000005")
        else:
            raise ValueError("unpriced_evaluation_request")
        with self.lock:
            committed = sum((Decimal(row["reserved_yuan"]) for row in self.rows), Decimal(0))
            if committed + amount > self.limit:
                raise ValueError("authorized_evaluation_budget_exhausted")
            self.rows.append({"attempt": len(self.rows) + 1, "model": payload["model"],
                              "reserved_yuan": str(amount)})
            self.ledger.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.ledger.with_suffix(".pending")
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump({"currency": "CNY", "limit_yuan": str(self.limit),
                           "reserved_upper_bound_yuan": str(committed + amount),
                           "attempts": self.rows}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.ledger)


def require_authorization() -> None:
    """Bind spending to one exact commit, workflow run and authorized attempt."""
    if (os.environ.get("OFFICIAL_EVALUATION_AUTHORIZED") != "true"
            or not os.environ.get("OFFICIAL_EVALUATION_BUDGET_REF", "").strip()
            or not os.environ.get("GITHUB_SHA")
            or os.environ.get("OFFICIAL_EVALUATION_AUTHORIZED_SHA") != os.environ["GITHUB_SHA"]
            or not os.environ.get("GITHUB_RUN_ID")
            or os.environ.get("OFFICIAL_EVALUATION_AUTHORIZED_RUN_ID") != os.environ["GITHUB_RUN_ID"]
            or not os.environ.get("GITHUB_RUN_ATTEMPT")
            or os.environ.get("OFFICIAL_EVALUATION_AUTHORIZED_RUN_ATTEMPT") != os.environ["GITHUB_RUN_ATTEMPT"]):
        raise ValueError("OFFICIAL_EVALUATION_NOT_AUTHORIZED")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--budget-yuan", required=True, type=Decimal)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("script", choices=["scripts/run_agent_chain_eval.py", "scripts/run_rag_baseline.py"])
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    require_authorization()
    budget = EvaluationBudget(args.budget_yuan, args.ledger)
    original_async, original_sync = httpx.AsyncClient.send, httpx.Client.send

    async def guarded_async(client, request, *a, **kw):
        if client.follow_redirects or kw.get("follow_redirects") is True:
            raise ValueError("evaluation_redirects_forbidden")
        budget.reserve(request)
        return await original_async(client, request, *a, **kw)

    def guarded_sync(client, request, *a, **kw):
        if client.follow_redirects or kw.get("follow_redirects") is True:
            raise ValueError("evaluation_redirects_forbidden")
        budget.reserve(request)
        return original_sync(client, request, *a, **kw)

    httpx.AsyncClient.send, httpx.Client.send = guarded_async, guarded_sync
    previous = sys.argv
    previous_path = sys.path[:]
    sys.argv = [args.script, *args.arguments]
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        runpy.run_path(args.script, run_name="__main__")
    finally:
        httpx.AsyncClient.send, httpx.Client.send = original_async, original_sync
        sys.argv = previous
        sys.path[:] = previous_path


if __name__ == "__main__":
    main()
