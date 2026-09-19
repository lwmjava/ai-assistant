"""基线夹具的链路冒烟测试（Mock 嵌入）。

Mock 只用于证明「摄取 → 索引 → 检索 → 打分」链路可跑通与可复现，
不得据此对检索质量下结论；正式基线由 ``scripts/run_rag_baseline.py`` 用真实嵌入产出。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.rag.embeddings.mock import MockEmbeddingProvider
from tests.eval.harness import build_eval_index, load_corpus_manifest, run_case
from tests.eval.validation import load_cases


@pytest.fixture
async def mock_index(tmp_path: Path):
    index = await build_eval_index(tmp_path / "eval_smoke.db", MockEmbeddingProvider(dim=64))
    yield index
    index.close()


async def test_index_covers_every_manifest_document(mock_index):
    manifest = load_corpus_manifest()
    assert len(mock_index.db_doc_id_to_logical) == len(manifest)
    assert set(mock_index.db_doc_id_to_logical.values()) == {
        entry["document_id"] for entry in manifest
    }
    assert mock_index.chunk_total > 0


async def test_non_current_versions_are_not_retrievable(mock_index):
    """Flag 默认关闭时，manifest 非当前版本不得出现在检索结果中。"""
    non_current = {
        entry["document_id"] for entry in load_corpus_manifest() if not entry["is_current"]
    }
    assert non_current, "语料应包含历史/未生效版本，否则 stale_version 类案例无意义"

    case = next(item for item in load_cases() if item["case_id"] == "rag-027")
    outcome = await run_case(case, mock_index, MockEmbeddingProvider(dim=64), retrieval_depth=10)
    assert not (set(outcome.retrieved_document_ids) & non_current)


async def test_tenant_isolation_holds_in_eval_index(mock_index):
    """跨租户案例：tenant-a 的查询不得命中 tenant-b 文档。"""
    case = next(item for item in load_cases() if item["case_id"] == "rag-033")
    outcome = await run_case(case, mock_index, MockEmbeddingProvider(dim=64), retrieval_depth=10)
    assert outcome.tenant_id == "tenant-a"
    assert "doc-tenant-b-billing" not in outcome.retrieved_document_ids


async def test_case_outcome_is_scorable_and_serializable(mock_index):
    case = next(item for item in load_cases() if item["case_id"] == "rag-001")
    outcome = await run_case(case, mock_index, MockEmbeddingProvider(dim=64), retrieval_depth=10)
    payload = outcome.to_dict()
    assert payload["case_id"] == "rag-001"
    assert payload["retrieved_chunk_count"] > 0
    assert set(payload["recall"]) == {"@1", "@5", "@10"}
    assert payload["latency_ms"] >= 0


async def test_cases_without_expected_docs_are_excluded_from_ranking(mock_index):
    """no_answer / private_resource 类案例不得产生排序分数。"""
    case = next(item for item in load_cases() if item["case_id"] == "rag-036")
    outcome = await run_case(case, mock_index, MockEmbeddingProvider(dim=64), retrieval_depth=10)
    assert outcome.expected_document_ids == []
    assert outcome.reciprocal_rank is None
    assert outcome.ndcg_at_10 is None
    assert all(value is None for value in outcome.recall.values())
