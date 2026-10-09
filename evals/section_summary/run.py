"""RAG-040 章节摘要「真实保真评价」骨架。

用途：在**没有真实模型/费用授权**时，先把评价链路与报告 schema 跑通并落盘——
记录模型 / Prompt / 协议版本、数据指纹、调用计数与延迟。本骨架默认注入一个
**合成 provider**，不发起任何真实收费调用。

真实保真（人工核对「摘要是否覆盖原文关键事实、有无幻觉、引用块能否回查」）
需要授权真实调用与人工 Gold 复核，本卡**不做**，报告中 ``human_review_status``
恒为 ``pending``，``provisional`` 结论不得当作发布依据。

用法（合成 provider，离线）：
    python -m evals.section_summary.run
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.llm.base import ChatMessage, LLMOptions, LLMProvider
from app.rag.section_summaries import PROMPT_VERSION, PROTOCOL_VERSION, ChapterSummarizer

# 合成样本：非 Gold、非真实客户文档；只证明链路。
SAMPLE_CHAPTER = (
    "第一章 背景。本系统是企业级 AI 助手平台，包含 FastAPI 后端与 React 控制台。"
    "第二章 目标。提供 RAG 文档解析、混合检索与多租户隔离。"
    "第三章 约束。默认向量库为 local，Milvus 仍为 Partial；资源级 ACL 为 Planned。"
)

# 护栏内预算预占（最坏 peak，CNY；见 docs/plans/proposal_rag_040_fidelity_eval_authorization_20261009.md）。
# 数值是「每请求最坏可能费用」的预占，不是真实账单；真实调用前由预算信封在每次请求前拦截。
WORST_INPUT_PER_1M_CNY = 3.0   # ¥ / 1M input token（保守上预占）
WORST_OUTPUT_PER_1M_CNY = 10.0 # ¥ / 1M output token（保守上预占）
PER_CALL_INPUT_TOKENS_CAP = 4000
PER_CALL_OUTPUT_TOKENS_CAP = 256
# 真实评价的硬调用上限（8 章 + 至多 2 次合并冗余）。
REAL_CALL_HARD_CAP = 10


class BudgetExhaustedError(RuntimeError):
    """预算信封不足以覆盖下一次最坏请求；调用方必须立即停止，不得重试。"""


@dataclass
class BudgetEnvelope:
    """费用护栏：按最坏情况预占，每次请求前先问「还付不付得起下一次」。

    不依赖服务端返回的 usage（缺失 usage 不算评测通过）；纯本地估算 + 预占。
    ``ceiling_cny`` 是预算信封（提案建议 ¥1.0），绝对 ¥5 由外层批复约束。
    """

    ceiling_cny: float
    calls_used: int = 0
    estimated_cost_cny: float = 0.0

    @property
    def worst_cost_per_call(self) -> float:
        return (
            PER_CALL_INPUT_TOKENS_CAP / 1e6 * WORST_INPUT_PER_1M_CNY
            + PER_CALL_OUTPUT_TOKENS_CAP / 1e6 * WORST_OUTPUT_PER_1M_CNY
        )

    def can_afford_next(self) -> bool:
        if self.calls_used >= REAL_CALL_HARD_CAP:
            return False
        return self.estimated_cost_cny + self.worst_cost_per_call <= self.ceiling_cny

    def reserve(self) -> None:
        """预占下一次请求的最坏费用；付不起即抛 ``BudgetExhausted``（不发请求）。"""
        if not self.can_afford_next():
            raise BudgetExhaustedError(
                f"预算信封将耗尽：已 {self.estimated_cost_cny:.4f} 元 / 上限 {self.ceiling_cny:.2f} 元，"
                f"已用 {self.calls_used}/{REAL_CALL_HARD_CAP} 次；停止，不再发请求。"
            )
        self.calls_used += 1
        self.estimated_cost_cny += self.worst_cost_per_call


class SyntheticProvider(LLMProvider):
    """离线合成 provider：不联网、不产生费用，输出可预测。"""

    model = "synthetic-summary-provider"

    async def chat(self, messages: list[ChatMessage], options: LLMOptions | None = None) -> str:
        return "[synthetic] 章节要点：背景、目标与约束三条。"

    async def stream_chat(self, messages: list[ChatMessage], options: LLMOptions | None = None):  # type: ignore[override]
        yield await self.chat(messages, options)


def build_real_provider(model: str = "deepseek-flash") -> LLMProvider:
    """构造真实 provider（**仅在获批后由人工显式调用**；本骨架 main() 不调用）。

    复用现有 LLM base_url/key，把模型覆盖为 RAG-028 已登记的 ``deepseek-flash``。
    无 key 或能力未登记时抛错——不静默降级成合成、不冒充真实调用。
    """
    from app.core.config import settings
    from app.llm.capabilities import resolve_effective_capability
    from app.llm.openai_compatible import OpenAICompatibleProvider

    if not (settings.LLM_API_KEY or "").strip():
        raise RuntimeError("未配置 LLM_API_KEY，不能发起真实调用")
    if resolve_effective_capability(settings.LLM_BASE_URL, model) is None:
        raise RuntimeError(
            f"模型 {model} @ {settings.LLM_BASE_URL} 无已登记能力，RAG-028 Guard 会拒绝；"
            "请先在 LLM_CAPABILITY_DECLARED 登记，不要绕过预算护栏。"
        )
    return OpenAICompatibleProvider(
        base_url=settings.LLM_BASE_URL, api_key=settings.LLM_API_KEY,
        model=model, timeout=settings.LLM_TIMEOUT,
    )


@dataclass
class SummaryEvalReport:
    """一次章节摘要评价的可复算报告（不含正文/密钥）。"""

    eval_name: str = "rag-section-summary-fidelity-skeleton"
    prompt_version: str = PROMPT_VERSION
    protocol_version: str = PROTOCOL_VERSION
    model: str = ""
    data_fingerprint: str = ""
    calls_used: int = 0
    latency_ms: int = 0
    summary_chars: int = 0
    estimated_cost_cny: float = 0.0
    source_chunk_ids_recorded: list[str] = field(default_factory=list)
    real_calls_made: bool = False
    human_review_status: str = "pending"
    note: str = ""


async def run_skeleton(
    provider: LLMProvider | None = None,
    envelope: BudgetEnvelope | None = None,
) -> SummaryEvalReport:
    provider = provider or SyntheticProvider()
    envelope = envelope or BudgetEnvelope(ceiling_cny=1.0)
    # 发请求前先过预算信封（合成链路不真实计费，但护栏同样生效）。
    envelope.reserve()
    summarizer = ChapterSummarizer(
        provider, max_calls=4, max_chars_per_batch=4000, output_tokens=256, timeout=20.0,
    )
    started = time.perf_counter()
    outcome = await summarizer.summarize(SAMPLE_CHAPTER)
    latency_ms = int((time.perf_counter() - started) * 1000)
    fingerprint = hashlib.sha256(SAMPLE_CHAPTER.encode("utf-8")).hexdigest()[:16]
    real = not isinstance(provider, SyntheticProvider)
    return SummaryEvalReport(
        model=provider.model,
        data_fingerprint=fingerprint,
        calls_used=summarizer.calls_used,
        latency_ms=latency_ms,
        summary_chars=len(outcome.text),
        estimated_cost_cny=envelope.estimated_cost_cny,
        real_calls_made=real,
        note=(
            "合成 provider 链路跑通；真实保真评价未验证（无真实调用授权，待人工 Gold）。"
            if not real else "真实 provider 已跑通链路；保真数值待人工 Gold 复核。"
        ),
    )


# 8 章真实保真样本（合成、非客户文档）：4 个普通 + 1 空章边界 + 2 个超长（>4000 字符触发分批合并）。
_LONG_A = ("第三章 系统架构。" + "分层为 API、Service、Agent、Tool、RAG 与存储，依赖方向单向向下。" * 60)
_LONG_B = ("第七章 安全与权限。" + "所有数据访问校验身份、租户、资源与动作，RAG 与工具返回均作不可信数据处理。" * 60)
SAMPLE_CHAPTERS: list[tuple[str, str]] = [
    ("ch01", "第一章 背景。本系统是企业级 AI 助手平台，包含 FastAPI 后端与 React 管理控制台。"),
    ("ch02", "第二章 目标。提供 RAG 文档解析、混合检索与多租户隔离能力，默认向量库为 local。"),
    ("ch03", "第三章 约束。Milvus 仍为 Partial；资源级 ACL 为 Planned；Prompt Injection 检测但不阻断。"),
    ("ch04", "第四章 成本。所有生成调用受上下文预算 Guard 与单任务调用数上限约束，超限即停。"),
    ("ch05", "第五章 失效。摘要绑定源文档版本；源软删或被替换时摘要立即不可读，不物理删除。"),
    ("ch06", ""),  # 空章边界：不发请求
    ("ch07", _LONG_A),
    ("ch08", _LONG_B),
]


class _GatedProvider(LLMProvider):
    """在每次 HTTP 调用前过预算信封；付不起即 BudgetExhausted，请求不发出。"""

    def __init__(self, inner: LLMProvider, envelope: BudgetEnvelope) -> None:
        self._inner = inner
        self._envelope = envelope
        self.model = inner.model
        self.http_attempts = 0

    async def chat(self, messages: list[ChatMessage], options: LLMOptions | None = None) -> str:
        self._envelope.reserve()  # 付不起抛 BudgetExhausted，不发请求
        self.http_attempts += 1
        return await self._inner.chat(messages, options)

    async def stream_chat(self, messages: list[ChatMessage], options: LLMOptions | None = None):  # type: ignore[override]
        raise NotImplementedError("fidelity eval 不使用流式")


def _est_input_tokens(text: str) -> int:
    """中文字符≈1.5 字符/token 的保守估算（provider 不返回 usage，仅本地预占）。"""
    return max(1, int(len(text) / 1.5))


async def run_real_eval(ceiling_cny: float = 1.0) -> dict:
    """用已批准的真实国内模型跑 8 章保真评价，落版本化 JSON。

    仅在用户批准后由人工显式调用 ``python -m evals.section_summary.run --real``。
    不记录密钥；报告只含 provider 与模型名。AI 判定一律 human_review_status=pending。
    """
    provider = build_real_provider("deepseek-flash")
    envelope = BudgetEnvelope(ceiling_cny=ceiling_cny)
    gated = _GatedProvider(provider, envelope)
    summarizer = ChapterSummarizer(
        gated, max_calls=REAL_CALL_HARD_CAP, max_chars_per_batch=4000,
        output_tokens=256, timeout=30.0,
    )
    per_chapter: list[dict] = []
    est_in = 0
    est_out = 0
    for cid, text in SAMPLE_CHAPTERS:
        if not envelope.can_afford_next():
            per_chapter.append({"chapter": cid, "kind": "stopped", "reason": "budget_exhausted"})
            break
        started = time.perf_counter()
        outcome = await summarizer.summarize(text)
        latency = int((time.perf_counter() - started) * 1000)
        est_in += _est_input_tokens(text)
        est_out += 256 if outcome.kind == "ready" else 0
        per_chapter.append({
            "chapter": cid,
            "kind": outcome.kind,
            "reason": outcome.reason,
            "input_chars": len(text),
            "summary_chars": len(outcome.text),
            "latency_ms": latency,
            # 摘要正文写入报告用于人工复核（合成样本、非客户文档）；不含密钥。
            "summary_excerpt": outcome.text[:200],
        })
    cost_cny = envelope.estimated_cost_cny
    report = {
        "eval_name": "rag-section-summary-fidelity-real",
        "provider": "deepseek-official",
        "model": provider.model,
        "capability_version": "deepseek-flash-1m-384k-20261007",
        "prompt_version": PROMPT_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "data": "synthetic-8-chapters-non-customer",
        "data_fingerprint": hashlib.sha256(
            json.dumps([(c, t) for c, t in SAMPLE_CHAPTERS], ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16],
        "temperature": 0.0,
        "thinking": "disabled",
        "http_attempts": gated.http_attempts,
        "calls_used": summarizer.calls_used,
        "hard_call_cap": REAL_CALL_HARD_CAP,
        "estimated_input_tokens": est_in,
        "estimated_output_tokens": est_out,
        "usage_source": "estimated_provider_usage_not_returned",
        "estimated_cost_cny": round(cost_cny, 4),
        "estimated_cost_usd": round(cost_cny / 7.2, 4),
        "budget_ceiling_cny": ceiling_cny,
        "absolute_ceiling_cny": 5.0,
        "retry_disabled": True,
        "per_chapter": per_chapter,
        "real_calls_made": True,
        "human_review_status": "pending",
        "fidelity_graded_pass": False,
        "note": "真实调用已执行；覆盖/幻觉/引用准确性未人工复核，不得写成达标。",
    }
    out_path = Path(__file__).parent / "report-real-20261009.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    import sys

    if "--real" in sys.argv:
        rep = asyncio.run(run_real_eval())
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return
    report = asyncio.run(run_skeleton())
    print(json.dumps(asdict(report), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
