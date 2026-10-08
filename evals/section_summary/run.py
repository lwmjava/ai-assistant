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

from app.llm.base import ChatMessage, LLMOptions, LLMProvider
from app.rag.section_summaries import PROMPT_VERSION, PROTOCOL_VERSION, ChapterSummarizer

# 合成样本：非 Gold、非真实客户文档；只证明链路。
SAMPLE_CHAPTER = (
    "第一章 背景。本系统是企业级 AI 助手平台，包含 FastAPI 后端与 React 控制台。"
    "第二章 目标。提供 RAG 文档解析、混合检索与多租户隔离。"
    "第三章 约束。默认向量库为 local，Milvus 仍为 Partial；资源级 ACL 为 Planned。"
)


class SyntheticProvider(LLMProvider):
    """离线合成 provider：不联网、不产生费用，输出可预测。"""

    model = "synthetic-summary-provider"

    async def chat(self, messages: list[ChatMessage], options: LLMOptions | None = None) -> str:
        return "[synthetic] 章节要点：背景、目标与约束三条。"

    async def stream_chat(self, messages: list[ChatMessage], options: LLMOptions | None = None):  # type: ignore[override]
        yield await self.chat(messages, options)


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
    source_chunk_ids_recorded: list[str] = field(default_factory=list)
    real_calls_made: bool = False
    human_review_status: str = "pending"
    note: str = ""


async def run_skeleton() -> SummaryEvalReport:
    provider = SyntheticProvider()
    summarizer = ChapterSummarizer(
        provider, max_calls=4, max_chars_per_batch=4000, output_tokens=256, timeout=20.0,
    )
    started = time.perf_counter()
    outcome = await summarizer.summarize(SAMPLE_CHAPTER)
    latency_ms = int((time.perf_counter() - started) * 1000)
    fingerprint = hashlib.sha256(SAMPLE_CHAPTER.encode("utf-8")).hexdigest()[:16]
    return SummaryEvalReport(
        model=provider.model,
        data_fingerprint=fingerprint,
        calls_used=summarizer.calls_used,
        latency_ms=latency_ms,
        summary_chars=len(outcome.text),
        real_calls_made=False,
        note="合成 provider 链路跑通；真实保真评价未验证（无真实调用授权，待人工 Gold）。",
    )


def main() -> None:
    report = asyncio.run(run_skeleton())
    print(json.dumps(asdict(report), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
