"""Deterministic Silver case generation grounded in selected real RAG documents."""

from __future__ import annotations

import hashlib
import json

from sqlmodel import Session, col, select

from app.models.evaluation import EvaluationDataset, EvaluationDatasetVersion
from app.models.rag import DocumentChunk
from app.models.user import User
from app.services.evaluation_datasets import canonical_cases_hash


class SilverGenerationError(ValueError):
    """Selected resources cannot yield a grounded Silver dataset."""


def ensure_rag_silver_dataset(
    session: Session,
    *,
    initiator: User,
    tenant_id: str,
    targets: list[dict[str, object]],
    max_cases: int = 50,
) -> tuple[EvaluationDataset, EvaluationDatasetVersion]:
    """Create or reuse an approved deterministic Silver set for an exact target snapshot."""
    if initiator.role != "system_admin":
        raise SilverGenerationError("system_admin_required")
    if max_cases < 1 or max_cases > 200:
        raise SilverGenerationError("invalid_case_limit")
    target_ids = [str(item["id"]) for item in targets]
    index_ids = {
        str(metadata["index_id"])
        for item in targets
        if isinstance((metadata := item.get("metadata")), dict) and metadata.get("index_id")
    }
    if not target_ids or len(index_ids) != 1:
        raise SilverGenerationError("invalid_rag_snapshot")
    index_id = next(iter(index_ids))
    chunks = list(
        session.exec(
            select(DocumentChunk)
            .where(
                col(DocumentChunk.tenant_id) == tenant_id,
                col(DocumentChunk.document_id).in_(target_ids),
                col(DocumentChunk.index_id) == index_id,
                col(DocumentChunk.parent_id).is_(None),
            )
            .order_by(col(DocumentChunk.document_id), col(DocumentChunk.chunk_index))
            .limit(max_cases)
        ).all()
    )
    if not chunks:
        raise SilverGenerationError("no_grounded_chunks")
    cases: list[dict[str, object]] = []
    for chunk in chunks:
        compact = " ".join(chunk.content.split())
        if len(compact) < 12:
            continue
        evidence = compact[:240]
        cases.append(
            {
                "case_id": f"silver-{chunk.id}",
                "split": "validation",
                "tier": "silver",
                "query": f"请检索与以下真实资料片段最相关的文档：{evidence[:120]}",
                "expected_document_ids": [chunk.document_id],
                "expected_evidence": evidence,
                "source_chunk_id": chunk.id,
                "generator": "deterministic-grounded-v1",
            }
        )
    if not cases:
        raise SilverGenerationError("no_grounded_chunks")
    selection_hash = hashlib.sha256(
        json.dumps(
            {"tenant_id": tenant_id, "target_ids": target_ids, "index_id": index_id},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    slug = f"auto-rag-{selection_hash[:16]}"
    dataset = session.exec(
        select(EvaluationDataset).where(
            col(EvaluationDataset.tenant_id) == tenant_id,
            col(EvaluationDataset.slug) == slug,
        )
    ).first()
    if dataset is None:
        dataset = EvaluationDataset(
            tenant_id=tenant_id,
            name="真实文档自动 Silver 测试集",
            slug=slug,
            family="retrieval",
            created_by=initiator.id,
            description="由所选真实文档当前发布版本的索引分块确定性生成。",
        )
        session.add(dataset)
        session.flush()
    content_hash = canonical_cases_hash(cases)
    version_name = f"silver-{content_hash[:12]}"
    existing = session.exec(
        select(EvaluationDatasetVersion).where(
            col(EvaluationDatasetVersion.dataset_id) == dataset.id,
            col(EvaluationDatasetVersion.version) == version_name,
        )
    ).first()
    if existing is not None:
        return dataset, existing
    version = EvaluationDatasetVersion(
        dataset_id=dataset.id,
        tenant_id=tenant_id,
        version=version_name,
        status="approved",
        source="ai_generated",
        content_hash=content_hash,
        schema_name="grounded-rag-silver-v1",
        cases_json=json.dumps(cases, ensure_ascii=False, sort_keys=True),
        index_json=json.dumps(
            {
                "target_ids": target_ids,
                "index_id": index_id,
                "generator": "deterministic-grounded-v1",
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        case_count=len(cases),
        is_frozen=True,
        created_by=initiator.id,
        review_decision="AUTO_SILVER",
        review_notes="Silver 自动生成；未经过人工 Gold 审核。",
    )
    session.add(version)
    session.commit()
    session.refresh(dataset)
    session.refresh(version)
    return dataset, version
