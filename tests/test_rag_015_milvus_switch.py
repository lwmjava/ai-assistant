from __future__ import annotations

from pathlib import Path

import pytest

from scripts.milvus_switch_check import (
    AcceptanceCheckError,
    _diagnostic,
    _run_counterexample_child,
    assert_l2_normalized,
    assert_local_preserved,
    assert_milvus_contains,
    assert_milvus_dimension_matches,
    assert_milvus_recall_not_silently_empty,
    assert_milvus_row_count_matches_sql,
    assert_similarity_not_placeholder,
    assert_sparse_side_not_empty,
    compare_results,
    counterexample_scenarios,
    dry_run_plan,
)

_FAKE_PORT = 19531


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


def test_milvus_dimension_must_match_registered_index() -> None:
    assert_milvus_dimension_matches(64, [{"dim": 64}, {"dim": 64}])
    with pytest.raises(AcceptanceCheckError, match="维度不符的向量不可比"):
        assert_milvus_dimension_matches(64, [{"dim": 32}])


def test_milvus_zero_recall_is_reported_as_failure_not_empty_knowledge() -> None:
    with pytest.raises(AcceptanceCheckError, match="不得把检索故障当成空知识库"):
        assert_milvus_recall_not_silently_empty([], 3)


def test_sparse_side_all_zero_is_reported_as_fake_hybrid() -> None:
    with pytest.raises(AcceptanceCheckError, match="稠密单路伪装成混合检索"):
        assert_sparse_side_not_empty([{"logical_id": "a"}], 0)


def test_stale_vectors_break_row_count_against_sql() -> None:
    assert_milvus_row_count_matches_sql(3, 3)
    with pytest.raises(AcceptanceCheckError, match="旧身份残留"):
        assert_milvus_row_count_matches_sql(3, 5)


def test_similarity_fixed_at_one_is_reported_as_placeholder() -> None:
    with pytest.raises(AcceptanceCheckError, match="仍为占位值"):
        assert_similarity_not_placeholder(
            [{"similarity": 1.0}, {"similarity": 1.0}],
        )
    assert_similarity_not_placeholder([{"similarity": 0.9}, {"similarity": 0.4}])


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


@pytest.mark.parametrize("scenario", sorted(counterexample_scenarios()))
def test_fake_milvus_counterexample_must_not_pass(scenario: str) -> None:
    """每个假 Milvus 反例都必须让脚本非 0 退出，并给出可诊断错误。

    这些用例不需要真实 Milvus：假库由脚本内部注入，退出码取自在独立子进程里
    真实跑出来的 ``sys.exit`` 值。若某个反例被判为通过，说明脚本存在恒真风险。
    """
    exit_code, report = _run_counterexample_child(scenario, _FAKE_PORT)

    assert exit_code != 0, f"反例 {scenario} 被脚本判为通过（exit=0），脚本存在恒真风险"
    diagnostic = _diagnostic(report)
    assert diagnostic != "no_failure_reported", f"反例 {scenario} 非 0 退出但没有留下可诊断错误"
