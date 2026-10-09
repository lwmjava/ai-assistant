"""RAG-031 真实单变量质量与成本评测（gpt-4o-mini）。

用法：
    python -m evals.llm_boundary.run_real --output evals/llm_boundary/report-real-<date>.json

授权（2026-10-09 用户批复）：模型 gpt-4o-mini @ api.openai.com、约 30 次调用上限、
USD $0.1 费用停止线。**无 OPENAI_API_KEY 时不发任何请求**，直接产出阻塞报告。

费用护栏：每次请求按最坏价格预占；余额不足以覆盖下一次请求即停止；httpx 不启用
隐式重试，每次 HTTP 尝试计数；未知 usage / 超时 / 5xx 不计为评测通过。
本脚本不记录任何凭据。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import pathlib

import httpx

from app.llm.base import LLMOptions
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.rag.chunking.llm_boundaries import (
    PROMPT_VERSION,
    PROTOCOL_VERSION,
    BoundaryAdvisor,
)
from app.rag.chunking.structure import protected_split, resolve_boundary_table

EVAL_VERSION = "llm-boundary-real-v0.1"

MODEL = "gpt-4o-mini"
BASE_URL = "https://api.openai.com/v1"
# gpt-4o-mini 公开价（USD / 1M tokens），成本估算依据；实际以返回 usage 为准。
PRICE_PER_1M_INPUT = 0.150
PRICE_PER_1M_OUTPUT = 0.600
BUDGET_USD = 0.10
HARD_CALL_CAP = 30
MAX_CHARS_PER_CALL = 1500
OUTPUT_TOKENS = 256
# 最坏单次输入 token 上界（保守；用于预占，不依赖真实计数）。
WORST_INPUT_TOKENS = 1200
WORST_PER_CALL_USD = (
    WORST_INPUT_TOKENS * PRICE_PER_1M_INPUT / 1_000_000
    + OUTPUT_TOKENS * PRICE_PER_1M_OUTPUT / 1_000_000
)
BOUNDARY_TOLERANCE_SENTENCES = 2  # 合成样本“边界命中”容差（非 Gold）


class BudgetExhaustedError(RuntimeError):
    pass


class _UsageProvider(OpenAICompatibleProvider):
    """复用 openai-compatible 路径与 RAG-028 Guard，额外采集 usage 与请求计数。"""

    def __init__(self, *args, budget, **kwargs):
        super().__init__(*args, **kwargs)
        self.budget = budget
        self.usage_log: list[dict] = []
        self.http_attempts = 0

    async def chat(self, messages, options=None):
        if not self.budget.can_afford(WORST_PER_CALL_USD):
            raise BudgetExhaustedError("预算停止线")
        self._guard(messages, options)  # RAG-028 上下文预算 Guard
        opts = options or LLMOptions()
        self.http_attempts += 1
        async with httpx.AsyncClient(timeout=opts.timeout or self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=self._payload(messages, options, False),
            )
            resp.raise_for_status()
            data = resp.json()
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage") or {}
        self.usage_log.append({
            "request_id": data.get("id"),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
        })
        self.budget.record(
            (usage.get("prompt_tokens", 0) or 0) * PRICE_PER_1M_INPUT / 1_000_000
            + (usage.get("completion_tokens", 0) or 0) * PRICE_PER_1M_OUTPUT / 1_000_000
        )
        return content


class Budget:
    def __init__(self, limit: float) -> None:
        self.limit = limit
        self.spent = 0.0

    def can_afford(self, worst: float) -> bool:
        return self.spent + worst <= self.limit

    def record(self, actual: float) -> None:
        self.spent += actual


def _synth_block(topic_a: str, topic_b: str, n_a: int, n_b: int):
    sents = [f"{topic_a}，第{i}句叙述。" for i in range(1, n_a + 1)]
    sents += [f"{topic_b}，第{i}句叙述。" for i in range(1, n_b + 1)]
    return "".join(sents), n_a  # 期望边界在第 n_a 句之后


def _build_doc(doc_id: int) -> tuple[str, list[int]]:
    fence = "```python\nprint(1)\n```\n"
    topics_a = ["数据库索引设计", "早餐食谱", "股票走势分析", "花园种植计划", "相机评测"]
    topics_b = ["物流路线优化", "周末徒步路线", "家庭预算规划", "摄影构图技巧", "红酒品鉴笔记"]
    expected = []
    parts = [fence]
    for k in range(3):
        block, shift = _synth_block(topics_a[(doc_id + k) % 5], topics_b[(doc_id + k) % 5], 6, 6)
        expected.append(shift)
        parts.append(block)
        if k < 2:
            parts.append(fence)
    return "".join(parts), expected


async def _run_real(provider: _UsageProvider) -> dict:
    rows = []
    total_calls = 0
    for doc_id in range(10):  # 10 文档 × 3 间隙 = 上限 30 次调用
        text, expected_shifts = _build_doc(doc_id)
        advisor = BoundaryAdvisor(
            provider=provider,
            max_calls=3,
            max_chars_per_call=MAX_CHARS_PER_CALL,
            output_tokens=OUTPUT_TOKENS,
            timeout=20.0,
        )
        table = await resolve_boundary_table(text, chunk_size=40, advisor=advisor)
        chunks = protected_split(text, 40, None, boundaries=table)
        metrics = {
            "chunk_count": len(chunks),
            "char_coverage_equal": "".join(c.text for c in chunks) == text,
        }
        rows.append({
            "doc_id": doc_id,
            "expected_shift_sentences": expected_shifts,
            "llm_calls": advisor.calls_used,
            "metrics": metrics,
        })
        total_calls += advisor.calls_used
        if total_calls >= HARD_CALL_CAP:
            break
    in_tok = sum(u["prompt_tokens"] or 0 for u in provider.usage_log)
    out_tok = sum(u["completion_tokens"] or 0 for u in provider.usage_log)
    return {
        "rows": rows,
        "real_calls": total_calls,
        "http_attempts": provider.http_attempts,
        "prompt_tokens_total": in_tok,
        "completion_tokens_total": out_tok,
        "estimated_cost_usd": round(provider.budget.spent, 6),
        "usage_log": provider.usage_log,
    }


async def run() -> dict:
    code_paths = [
        pathlib.Path("app/rag/chunking/llm_boundaries.py"),
        pathlib.Path("app/rag/chunking/structure.py"),
        pathlib.Path(__file__),
    ]
    base = {
        "version": EVAL_VERSION,
        "prompt_version": PROMPT_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "model": {"provider": "openai", "deployment": BASE_URL, "model": MODEL},
        "prices_usd_per_1m": {"input": PRICE_PER_1M_INPUT, "output": PRICE_PER_1M_OUTPUT},
        "budget_usd": BUDGET_USD,
        "hard_call_cap": HARD_CALL_CAP,
        "params": {"max_chars_per_call": MAX_CHARS_PER_CALL, "output_tokens": OUTPUT_TOKENS,
                   "max_calls_per_doc": 3, "chunk_size": 40},
        "provenance": (
            "AI-authored synthetic Smoke/Adversarial samples; NOT Gold; "
            "holdout not applicable (single-variable synthetic)"
        ),
        "code_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in code_paths},
    }
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        return {
            **base,
            "status": "blocked_no_credentials",
            "real_calls": 0,
            "http_attempts": 0,
            "reason": (
                "OPENAI_API_KEY 未配置（.env 与进程环境均为空）；现有 LLM_API_KEY 指向 "
                "api.deepseek.com，无法访问 gpt-4o-mini。按预检要求不发起调用。"
            ),
            "quality_cost": "未验证（无凭据）",
        }
    provider = _UsageProvider(
        base_url=BASE_URL, api_key=key, model=MODEL, timeout=20.0, budget=Budget(BUDGET_USD),
    )
    result = await _run_real(provider)
    return {
        **base,
        "status": "completed",
        **result,
        "quality_cost": {
            "boundary_accuracy_note": (
                f"合成样本边界命中（±{BOUNDARY_TOLERANCE_SENTENCES} 句）为 AI-authored Smoke，非 Gold"
            ),
            "per_call_cost_usd": round(result["estimated_cost_usd"] / max(1, result["real_calls"]), 6),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(run())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("status:", report.get("status"), "real_calls:", report.get("real_calls"))


if __name__ == "__main__":
    main()
