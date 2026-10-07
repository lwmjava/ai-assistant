from __future__ import annotations

from pathlib import Path

import pytest

from scripts.milvus_switch_check import (
    AcceptanceCheckError,
    assert_l2_normalized,
    assert_local_preserved,
    assert_milvus_contains,
    compare_results,
    dry_run_plan,
)


def test_dry_run_only_prints_plan_and_does_not_create_report(tmp_path: Path) -> None:
    report = tmp_path / "report.json"

    plan = dry_run_plan("http://127.0.0.1:19530", report)

    assert plan["mode"] == "dry-run"
    assert plan["writes_performed"] is False
    assert "--apply" in plan["apply_command"]
    assert not report.exists()


def test_milvus_presence_requires_rows_from_milvus_not_sql_only() -> None:
    with pytest.raises(AcceptanceCheckError, match="仅有 DocumentChunk 不能算 Milvus 写入通过"):
        assert_milvus_contains(["sql-chunk-a"], [])


def test_milvus_presence_rejects_partial_collection_write() -> None:
    with pytest.raises(AcceptanceCheckError, match="missing=.*sql-chunk-b"):
        assert_milvus_contains(
            ["sql-chunk-a", "sql-chunk-b"],
            [{"id": "sql-chunk-a"}],
        )


def test_switch_back_rejects_silent_no_hit_when_local_data_exists() -> None:
    with pytest.raises(AcceptanceCheckError, match="不得静默当成空知识库"):
        assert_local_preserved(
            ["local-document"],
            ["local-chunk"],
            ["local-document"],
            "no_hit",
        )


def test_switch_back_requires_original_local_document_hit() -> None:
    with pytest.raises(AcceptanceCheckError, match="missing=.*local-document"):
        assert_local_preserved(
            ["local-document"],
            ["local-chunk"],
            ["other-document"],
            "ok",
        )


def test_write_vectors_must_be_l2_normalized() -> None:
    assert assert_l2_normalized([[0.6, 0.8]]) == {"min": 1.0, "max": 1.0}
    with pytest.raises(AcceptanceCheckError, match="未按 L2 归一化"):
        assert_l2_normalized([[1.0, 1.0]])


def test_cross_backend_comparison_records_sets_ranks_and_ranges() -> None:
    local = [
        {"logical_id": "a", "score": 0.03, "similarity": 0.9},
        {"logical_id": "b", "score": 0.02, "similarity": 0.1},
    ]
    milvus = [
        {"logical_id": "b", "score": 0.04, "similarity": 1.0},
        {"logical_id": "c", "score": 0.01, "similarity": 1.0},
    ]

    comparison = compare_results(local, milvus)

    assert comparison["only_local"] == ["a"]
    assert comparison["only_milvus"] == ["c"]
    assert comparison["rank_differences"] == {"b": {"local": 2, "milvus": 1}}
    assert comparison["score_range"]["local"] == {"min": 0.02, "max": 0.03}
    assert comparison["similarity_range"]["milvus"] == {"min": 1.0, "max": 1.0}
