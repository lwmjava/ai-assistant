"""MultiHop-RAG 转换与映射测试：不访问网络，不调用真实 Embedding。"""

from __future__ import annotations

import importlib.util
import sys
from copy import deepcopy
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONVERT_PATH = REPO_ROOT / "evals" / "multihop_rag" / "convert.py"


def load_convert():
    spec = importlib.util.spec_from_file_location("multihop_rag_convert_test", CONVERT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_smoke_fixture_maps_evidence_and_keeps_null_query_unscored(tmp_path: Path) -> None:
    convert = load_convert()
    result = convert.convert_smoke(tmp_path)

    assert result.document_count == 3
    assert result.question_count == 3
    assert result.scorable_question_count == 2
    assert result.null_query_count == 1
    assert result.question_types["inference_query"] == 1
    assert result.question_types["comparison_query"] == 1
    assert result.question_types["null_query"] == 1

    cases = convert.load_json(result.cases_path)
    by_id = {case["case_id"]: case for case in cases}
    inference = by_id["multihop-0001"]
    assert inference["split"] == "comparison"
    assert inference["category"] == "inference_query"
    assert inference["should_answer"] is True
    assert inference["identity"]["tenant_id"] == "tenant-multihop"
    assert inference["forbidden_document_ids"] == []
    assert len(inference["expected_document_ids"]) == 2
    assert len(inference["expected_evidence"]) == 2
    for item in inference["expected_evidence"]:
        source = tmp_path / next(
            entry["source"]
            for entry in convert.load_json(result.manifest_path)["documents"]
            if entry["document_id"] == item["document_id"]
        )
        assert item["exact_quote"] in source.read_text(encoding="utf-8")

    null_case = by_id["multihop-0003"]
    assert null_case["category"] == "null_query"
    assert null_case["expected_document_ids"] == []
    assert null_case["expected_evidence"] == []
    assert null_case["should_answer"] is False


def test_document_ids_are_stable_under_input_shuffling(tmp_path: Path) -> None:
    convert = load_convert()
    corpus = convert.load_json(convert.SMOKE_FIXTURE_DIR / "corpus.json")
    queries = convert.load_json(convert.SMOKE_FIXTURE_DIR / "MultiHopRAG.json")
    first = convert.convert_records(
        corpus, queries, generated_at="2026-11-10T00:00:00+00:00", source_snapshot="test"
    )
    shuffled = list(reversed(corpus))
    second = convert.convert_records(
        shuffled, queries, generated_at="2026-11-10T00:00:00+00:00", source_snapshot="test"
    )
    first_docs = {item["url"]: item["document_id"] for item in first[0]}
    second_docs = {item["url"]: item["document_id"] for item in second[0]}
    assert first_docs == second_docs
    assert [case["expected_document_ids"] for case in first[1]] == [
        case["expected_document_ids"] for case in second[1]
    ]


def test_missing_evidence_url_is_rejected() -> None:
    convert = load_convert()
    corpus = convert.load_json(convert.SMOKE_FIXTURE_DIR / "corpus.json")
    queries = deepcopy(convert.load_json(convert.SMOKE_FIXTURE_DIR / "MultiHopRAG.json"))
    queries[0]["evidence_list"][0]["url"] = "https://example.com/missing"
    with pytest.raises(convert.ConversionError, match="无法映射到语料"):
        convert.convert_records(
            corpus, queries, generated_at="2026-11-10T00:00:00+00:00", source_snapshot="test"
        )


def test_fact_not_in_body_is_rejected() -> None:
    convert = load_convert()
    corpus = convert.load_json(convert.SMOKE_FIXTURE_DIR / "corpus.json")
    queries = deepcopy(convert.load_json(convert.SMOKE_FIXTURE_DIR / "MultiHopRAG.json"))
    queries[0]["evidence_list"][0]["fact"] = "This sentence does not appear in the source article body."
    with pytest.raises(convert.ConversionError, match="未出现在语料正文中"):
        convert.convert_records(
            corpus, queries, generated_at="2026-11-10T00:00:00+00:00", source_snapshot="test"
        )


def test_duplicate_corpus_url_is_rejected() -> None:
    convert = load_convert()
    corpus = convert.load_json(convert.SMOKE_FIXTURE_DIR / "corpus.json")
    corpus.append(deepcopy(corpus[0]))
    corpus[-1]["title"] = "A different title that would otherwise look unique"
    queries = convert.load_json(convert.SMOKE_FIXTURE_DIR / "MultiHopRAG.json")
    with pytest.raises(convert.ConversionError, match="url 重复"):
        convert.convert_records(
            corpus, queries, generated_at="2026-11-10T00:00:00+00:00", source_snapshot="test"
        )


def test_official_raw_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    convert = load_convert()
    (tmp_path / "corpus.json").write_text("[]\n", encoding="utf-8", newline="\n")
    (tmp_path / "MultiHopRAG.json").write_text("[]\n", encoding="utf-8", newline="\n")
    with pytest.raises(convert.ConversionError, match="校验失败"):
        convert.verify_official_raw(tmp_path)
