"""RAG-005 基线夹具：把合成语料摄取进隔离索引，并按案例跑真实检索链路。

刻意复用生产对象（``RAGService.ingest_text`` / ``RAGService.search``），
而不是另写一套检索，否则基线只能证明夹具自身。索引落在独立 SQLite 文件里，
不污染开发库和 ``data/test_ai_assistant.db``。

权限说明：当前 ``Document`` 没有 ``resource_scope`` 字段，
``LocalVectorStore.hybrid_search`` 按 ``tenant_id`` 过滤；默认只保留 ``is_current``。
打开 ``RAG_EFFECTIVE_DATE_FILTER`` 后，另按 ADR-0003 处理 as-of / scheduled。
本夹具如实反映配置，不在评测侧补一层不存在的 ACL 来美化分数。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlmodel import Session, SQLModel, create_engine, select

from app.models.rag import Document
from app.rag.effective_date import parse_manifest_datetime
from app.rag.embeddings.base import EmbeddingProvider
from app.rag.service import RAGService
from tests.eval.metrics import (
    dedupe_preserving_order,
    mean_or_none,
    ndcg_at_k,
    percentile,
    quote_hit,
    recall_at_k,
    reciprocal_rank,
)
from tests.eval.validation import CORPUS_MANIFEST, REPO_ROOT, load_cases, load_json

RECALL_KS = (1, 5, 10)


@dataclass
class CaseOutcome:
    """单个案例的检索结果与确定性打分。"""

    case_id: str
    split: str
    tier: str
    category: str
    review_status: str
    tenant_id: str
    should_answer: bool
    expected_document_ids: list[str]
    forbidden_document_ids: list[str]
    retrieved_document_ids: list[str]
    retrieved_chunk_count: int
    recall: dict[int, float | None]
    reciprocal_rank: float | None
    ndcg_at_10: float | None
    evidence_total: int
    evidence_hit: int
    forbidden_hits: list[str]
    latency_ms: float
    failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "split": self.split,
            "tier": self.tier,
            "category": self.category,
            "review_status": self.review_status,
            "tenant_id": self.tenant_id,
            "should_answer": self.should_answer,
            "expected_document_ids": self.expected_document_ids,
            "forbidden_document_ids": self.forbidden_document_ids,
            "retrieved_document_ids": self.retrieved_document_ids,
            "retrieved_chunk_count": self.retrieved_chunk_count,
            "recall": {f"@{k}": v for k, v in self.recall.items()},
            "reciprocal_rank": self.reciprocal_rank,
            "ndcg@10": self.ndcg_at_10,
            "evidence_total": self.evidence_total,
            "evidence_hit": self.evidence_hit,
            "forbidden_hits": self.forbidden_hits,
            "latency_ms": round(self.latency_ms, 2),
            "failures": self.failures,
        }


@dataclass
class EvalIndex:
    """已建好的评测索引及其逻辑文档号映射。"""

    session: Session
    db_doc_id_to_logical: dict[str, str]
    chunk_total: int
    documents_by_tenant: dict[str, int]

    def close(self) -> None:
        self.session.close()


def load_corpus_manifest() -> list[dict[str, Any]]:
    return list(load_json(CORPUS_MANIFEST)["documents"])


async def build_eval_index(
    db_path: Path,
    embedding: EmbeddingProvider,
) -> EvalIndex:
    """重建隔离索引：删除旧文件，按 manifest 摄取全部合成语料。

    每次重建而非增量，保证基线可复现且不会残留上一次运行的向量。
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    session = Session(engine)

    manifest = load_corpus_manifest()
    services: dict[str, RAGService] = {}
    mapping: dict[str, str] = {}
    documents_by_tenant: dict[str, int] = {}

    for entry in manifest:
        tenant_id = entry["tenant_id"]
        service = services.get(tenant_id)
        if service is None:
            service = RAGService(session, tenant_id, embedding_provider=embedding)
            services[tenant_id] = service

        text = (REPO_ROOT / entry["source"]).read_text(encoding="utf-8")
        document = await service.ingest_text(
            text,
            title=entry["title"],
            source=entry["source"],
            user_id=f"eval-owner-{tenant_id}",
            is_current=bool(entry["is_current"]),
            effective_at=parse_manifest_datetime(entry.get("effective_at")),
            expires_at=parse_manifest_datetime(entry.get("expires_at")),
        )

        mapping[document.id] = entry["document_id"]
        documents_by_tenant[tenant_id] = documents_by_tenant.get(tenant_id, 0) + 1

    chunk_total = sum(
        doc.chunk_count for doc in session.exec(select(Document)).all()
    )
    return EvalIndex(
        session=session,
        db_doc_id_to_logical=mapping,
        chunk_total=chunk_total,
        documents_by_tenant=documents_by_tenant,
    )


async def run_case(
    case: dict[str, Any],
    index: EvalIndex,
    embedding: EmbeddingProvider,
    *,
    retrieval_depth: int,
) -> CaseOutcome:
    """按案例身份的租户跑一次检索，并做确定性打分。"""
    tenant_id = case["identity"]["tenant_id"]
    service = RAGService(index.session, tenant_id, embedding_provider=embedding)

    started = time.perf_counter()
    hits = await service.search(case["query"], top_k=retrieval_depth)
    latency_ms = (time.perf_counter() - started) * 1000.0

    logical_ids = [
        index.db_doc_id_to_logical.get(hit.document_id, f"unknown:{hit.document_id}")
        for hit in hits
    ]
    ranked_docs = dedupe_preserving_order(logical_ids)
    chunk_texts = [hit.content for hit in hits]

    expected = set(case["expected_document_ids"])
    forbidden = set(case["forbidden_document_ids"])

    evidence = case["expected_evidence"]
    evidence_hit = sum(1 for item in evidence if quote_hit(chunk_texts, item["exact_quote"]))
    forbidden_hits = [doc_id for doc_id in ranked_docs if doc_id in forbidden]

    recall = {k: recall_at_k(ranked_docs, expected, k) for k in RECALL_KS}

    failures: list[str] = []
    if forbidden_hits:
        failures.append(f"FORBIDDEN_DOC_RETRIEVED:{','.join(forbidden_hits)}")
    if expected and not (set(ranked_docs) & expected):
        failures.append("EXPECTED_DOC_MISSED")
    if evidence and evidence_hit < len(evidence):
        failures.append(f"EVIDENCE_QUOTE_MISSED:{evidence_hit}/{len(evidence)}")

    return CaseOutcome(
        case_id=case["case_id"],
        split=case["split"],
        tier=case["tier"],
        category=case["category"],
        review_status=case["provenance"]["review_status"],
        tenant_id=tenant_id,
        should_answer=case["should_answer"],
        expected_document_ids=sorted(expected),
        forbidden_document_ids=sorted(forbidden),
        retrieved_document_ids=ranked_docs,
        retrieved_chunk_count=len(hits),
        recall=recall,
        reciprocal_rank=reciprocal_rank(ranked_docs, expected),
        ndcg_at_10=ndcg_at_k(ranked_docs, expected, 10),
        evidence_total=len(evidence),
        evidence_hit=evidence_hit,
        forbidden_hits=forbidden_hits,
        latency_ms=latency_ms,
        failures=failures,
    )


async def run_all_cases(
    index: EvalIndex,
    embedding: EmbeddingProvider,
    *,
    retrieval_depth: int,
    splits: set[str] | None = None,
) -> list[CaseOutcome]:
    """按 case_id 顺序跑全部（或指定 split 的）案例，保证结果可比对。"""
    cases = sorted(load_cases(), key=lambda item: item["case_id"])
    if splits is not None:
        cases = [case for case in cases if case["split"] in splits]
    outcomes: list[CaseOutcome] = []
    for case in cases:
        outcomes.append(
            await run_case(case, index, embedding, retrieval_depth=retrieval_depth)
        )
    return outcomes


def summarize(outcomes: list[CaseOutcome]) -> dict[str, Any]:
    """聚合一组案例的指标。

    排序指标只在「有期望文档」的案例上取均值；无期望文档的案例计入 ``not_scorable``
    并单列，避免拒答类案例被当成 0 分稀释检索表现。
    """
    scorable = [item for item in outcomes if item.expected_document_ids]
    with_evidence = [item for item in outcomes if item.evidence_total]
    with_forbidden = [item for item in outcomes if item.forbidden_document_ids]
    violations = [item for item in with_forbidden if item.forbidden_hits]
    latencies = [item.latency_ms for item in outcomes]

    evidence_total = sum(item.evidence_total for item in with_evidence)
    evidence_hit = sum(item.evidence_hit for item in with_evidence)

    return {
        "cases": len(outcomes),
        "scorable_cases": len(scorable),
        "not_scorable_cases": len(outcomes) - len(scorable),
        "recall": {
            f"@{k}": mean_or_none([
                value for item in scorable if (value := item.recall[k]) is not None
            ])
            for k in RECALL_KS
        },
        "mrr": mean_or_none([
            item.reciprocal_rank for item in scorable if item.reciprocal_rank is not None
        ]),
        "ndcg@10": mean_or_none([
            item.ndcg_at_10 for item in scorable if item.ndcg_at_10 is not None
        ]),
        "citation": {
            "cases_with_evidence": len(with_evidence),
            "evidence_total": evidence_total,
            "evidence_hit": evidence_hit,
            "quote_coverage": (evidence_hit / evidence_total) if evidence_total else None,
        },
        "safety": {
            "cases_with_forbidden_docs": len(with_forbidden),
            "violation_cases": len(violations),
            "violation_case_ids": [item.case_id for item in violations],
            "violation_rate": (len(violations) / len(with_forbidden)) if with_forbidden else None,
        },
        "latency_ms": {
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
            "max": max(latencies) if latencies else None,
        },
    }


def group_summaries(outcomes: list[CaseOutcome], key: str) -> dict[str, dict[str, Any]]:
    """按 split / category / tier 等字段分组聚合。"""
    groups: dict[str, list[CaseOutcome]] = {}
    for outcome in outcomes:
        groups.setdefault(getattr(outcome, key), []).append(outcome)
    return {name: summarize(items) for name, items in sorted(groups.items())}


def dump_outcomes(outcomes: list[CaseOutcome], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [outcome.to_dict() for outcome in outcomes]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
