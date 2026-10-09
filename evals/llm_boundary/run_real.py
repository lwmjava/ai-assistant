"""RAG-031 真实单变量质量与成本评测（国内 DeepSeek 模型，OpenAI 兼容接口）。

用法：
    python -m evals.llm_boundary.run_real --output evals/llm_boundary/report-real-<date>.json

模型选择（用户 2026-10-09 约束，取消 gpt-4o-mini）：
  1) 若 .env 已声明 LLM_CAPABILITY_DECLARED 覆盖 deepseek-chat → 用 LLM_DEFAULT_MODEL
     （deepseek-chat，api.deepseek.com OpenAI 兼容，走 RAG-028 Guard）；
  2) 否则 → deepseek-flash（RAG-035 已验证，能力登记 deepseek-flash-1m-384k-20261007，同 key 同部署）。
两种均 thinking disabled（与 RAG-035 一致）。

费用护栏：USD $0.1 停止线（按 7.2 RMB/USD 折算 = ¥0.72；DeepSeek 高峰价输入 ¥2/M、
输出 ¥8/M tokens）；每请求按最坏价预占，余额不足即停；httpx 不启用 SDK 隐式重试，
每次 HTTP 尝试计数；缺 usage/超时/5xx 不算通过；本脚本不记录任何凭据。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import pathlib

import httpx

from app.core.config import settings
from app.llm.base import ChatMessage, LLMOptions
from app.rag.chunking.llm_boundaries import (
    PROMPT_VERSION,
    PROTOCOL_VERSION,
    BoundaryAdvisor,
    parse_boundary_indices,
)
from app.rag.chunking.structure import protected_split, resolve_boundary_table

EVAL_VERSION = "llm-boundary-real-v0.2"

BASE_URL = "https://api.deepseek.com/v1"
# 折算与计价（报告须记录依据）。
RMB_PER_USD = 7.2
COST_USD_STOP = 0.10
COST_YUAN_STOP = round(COST_USD_STOP * RMB_PER_USD, 4)  # ¥0.72
PRICE_INPUT_PER_MTOK_YUAN = 2.0
PRICE_OUTPUT_PER_MTOK_YUAN = 8.0
MAX_HTTP_ATTEMPTS = 30
MAX_CHARS_PER_CALL = 1500
OUTPUT_TOKENS = 256
CHUNK_SIZE = 40
TOLERANCE_SENTENCES = 2  # 合成样本“边界命中”容差（非 Gold）
EXPECTED_SHIFT = 6  # 每段合成正文：前 6 句主题 A，后 6 句主题 B


class BudgetExhaustedError(RuntimeError):
    pass


class UnknownUsageError(RuntimeError):
    pass


class _GuardedDeepSeekProvider:
    """实现 BoundaryAdvisor 所需的最小 provider 接口（.model + async chat）。

    直连 api.deepseek.com（OpenAI 兼容），无 SDK 隐式重试；发请求前按最坏价预占。
    """

    def __init__(self, api_key: str, model: str, capability_version: str) -> None:
        self.model = model
        self._key = api_key
        self._capability_version = capability_version
        self.attempts = 0
        self.spent_yuan = 0.0
        self.used_input_tokens = 0
        self.used_output_tokens = 0
        self.logs: list[dict] = []

    def _worst(self, est_in_tokens: int) -> float:
        return (
            est_in_tokens * PRICE_INPUT_PER_MTOK_YUAN / 1_000_000
            + OUTPUT_TOKENS * PRICE_OUTPUT_PER_MTOK_YUAN / 1_000_000
        )

    async def chat(self, messages: list[ChatMessage], options: LLMOptions | None = None) -> str:
        est_in = sum(len(m.content.encode("utf-8")) for m in messages)
        worst = self._worst(est_in)
        if self.attempts >= MAX_HTTP_ATTEMPTS:
            raise BudgetExhaustedError("HTTP 尝试上限")
        if self.spent_yuan + worst > COST_YUAN_STOP:
            raise BudgetExhaustedError("费用停止线")
        opts = options or LLMOptions()
        self.attempts += 1
        payload = {
            "model": self.model,
            "messages": [{"role": m.role.value, "content": m.content} for m in messages],
            "temperature": 0,
            "max_tokens": OUTPUT_TOKENS,
            "stream": False,
            "thinking": {"type": "disabled"},
        }
        async with httpx.AsyncClient(timeout=opts.timeout or 20.0) as client:
            resp = await client.post(
                f"{BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        content = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage") or {}
        pt, ct = usage.get("prompt_tokens"), usage.get("completion_tokens")
        if not isinstance(pt, int) or not isinstance(ct, int):
            raise UnknownUsageError("usage 缺失，按失败处理")
        self.used_input_tokens += pt
        self.used_output_tokens += ct
        self.spent_yuan += pt * PRICE_INPUT_PER_MTOK_YUAN / 1_000_000 + ct * PRICE_OUTPUT_PER_MTOK_YUAN / 1_000_000
        self.logs.append({
            "request_id": data.get("id"),
            "response_model": data.get("model"),
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "raw_output": content,
        })
        return content


def _select_model() -> tuple[str, str, str | None]:
    """返回 (model, capability_version, note)。无 deepseek-chat 声明则用 deepseek-flash。"""
    declared = ""
    dotenv = pathlib.Path(".env")
    if dotenv.exists():
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("LLM_CAPABILITY_DECLARED="):
                declared = s.split("=", 1)[1].strip().strip('"').strip("'")
    if "deepseek-chat" in declared:
        return settings.LLM_DEFAULT_MODEL, "deepseek-chat-declared", "按 .env 声明使用 LLM_DEFAULT_MODEL"
    note = "无 deepseek-chat 声明，按 RAG-035 验证路径用 deepseek-flash"
    return "deepseek-flash", "deepseek-flash-1m-384k-20261007", note


def _synth_doc(doc_id: int) -> str:
    fence = "```python\nprint(1)\n```\n"
    topics_a = ["数据库索引设计", "早餐食谱", "股票走势分析", "花园种植计划", "相机评测"]
    topics_b = ["物流路线优化", "周末徒步路线", "家庭预算规划", "摄影构图技巧", "红酒品鉴笔记"]
    a = topics_a[doc_id % 5]
    b = topics_b[doc_id % 5]
    sents = [f"{a}，第{i}句叙述。" for i in range(1, EXPECTED_SHIFT + 1)]
    sents += [f"{b}，第{i}句叙述。" for i in range(1, EXPECTED_SHIFT + 1)]
    return fence + "".join(sents) + fence


async def _run(provider: _GuardedDeepSeekProvider) -> dict:
    rows = []
    for doc_id in range(30):  # 30 文档 × 1 间隙 = 上限 30 次 HTTP 尝试
        text = _synth_doc(doc_id)
        advisor = BoundaryAdvisor(
            provider=provider,
            max_calls=1,
            max_chars_per_call=MAX_CHARS_PER_CALL,
            output_tokens=OUTPUT_TOKENS,
            timeout=20.0,
        )
        table = await resolve_boundary_table(text, chunk_size=CHUNK_SIZE, advisor=advisor)
        chunks = protected_split(text, CHUNK_SIZE, None, boundaries=table)
        rows.append({
            "doc_id": doc_id,
            "llm_calls": advisor.calls_used,
            "chunk_count": len(chunks),
            "char_coverage_equal": "".join(c.text for c in chunks) == text,
        })
    # 边界准确率：逐次调用对照已知 shift（EXPECTED_SHIFT），容差 ±TOLERANCE_SENTENCES。
    hits, parsed_calls = 0, 0
    for entry in provider.logs:
        indices = parse_boundary_indices(entry["raw_output"], EXPECTED_SHIFT * 2)
        if indices is None:
            continue
        parsed_calls += 1
        if any(abs(i - EXPECTED_SHIFT) <= TOLERANCE_SENTENCES for i in indices):
            hits += 1
    return {
        "rows": rows,
        "http_attempts": provider.attempts,
        "prompt_tokens_total": provider.used_input_tokens,
        "completion_tokens_total": provider.used_output_tokens,
        "spent_yuan": round(provider.spent_yuan, 6),
        "spent_usd": round(provider.spent_yuan / RMB_PER_USD, 6),
        "usage_log": provider.logs,
        "boundary_accuracy": {
            "parsed_calls": parsed_calls,
            "hits": hits,
            "accuracy": round(hits / parsed_calls, 4) if parsed_calls else None,
            "note": (
                f"AI-authored 合成样本（shift=第{EXPECTED_SHIFT}句），"
                f"±{TOLERANCE_SENTENCES} 句容差；非 Gold"
            ),
        },
    }


async def run() -> dict:
    code_paths = [
        pathlib.Path("app/rag/chunking/llm_boundaries.py"),
        pathlib.Path("app/rag/chunking/structure.py"),
        pathlib.Path(__file__),
    ]
    model, cap_ver, note = _select_model()
    base = {
        "version": EVAL_VERSION,
        "prompt_version": PROMPT_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "model": {
            "provider": "deepseek-official",
            "base_url": BASE_URL,
            "model": model,
            "capability_version": cap_ver,
            "non_thinking": True,
            "temperature": 0,
            "max_tokens": OUTPUT_TOKENS,
        },
        "model_selection_note": note,
        "pricing": {
            "rmb_per_usd": RMB_PER_USD,
            "cost_usd_stop": COST_USD_STOP,
            "cost_yuan_stop": COST_YUAN_STOP,
            "price_input_per_mtok_yuan": PRICE_INPUT_PER_MTOK_YUAN,
            "price_output_per_mtok_yuan": PRICE_OUTPUT_PER_MTOK_YUAN,
            "source": "DeepSeek 公开定价（高峰价）+ 7.2 RMB/USD 折算",
        },
        "params": {
            "max_http_attempts": MAX_HTTP_ATTEMPTS,
            "max_chars_per_call": MAX_CHARS_PER_CALL,
            "output_tokens": OUTPUT_TOKENS,
            "chunk_size": CHUNK_SIZE,
            "docs": 30,
        },
        "provenance": "AI-authored synthetic Smoke/Adversarial samples; NOT Gold; holdout not applicable",
        "code_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in code_paths},
    }
    key = (settings.LLM_API_KEY or "").strip()
    if not key:
        return {**base, "status": "blocked_no_credentials", "reason": "settings.LLM_API_KEY 为空，不发起调用。"}
    try:
        result = await _run(_GuardedDeepSeekProvider(api_key=key, model=model, capability_version=cap_ver))
    except Exception as exc:  # 网络/服务错误不得冒充通过
        return {**base, "status": "blocked_error", "reason": f"{type(exc).__name__}: {exc}"}
    return {**base, "status": "completed", **result}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(run())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("status:", report.get("status"), "http_attempts:", report.get("http_attempts"))


if __name__ == "__main__":
    main()
