"""RAG-004：Schema、引用、重复、split 泄漏和敏感扫描。"""

from __future__ import annotations

import json
from collections import Counter

from tests.eval.validation import (
    CASES_PATH,
    CORPUS_MANIFEST,
    INDEX_PATH,
    REQUIRED_CATEGORIES,
    SCHEMA_PATH,
    load_cases,
    load_json,
    load_manifest,
    quote_in_source,
    validate_dataset,
)


def test_schema_and_index_files_exist() -> None:
    assert SCHEMA_PATH.is_file()
    assert CASES_PATH.is_file()
    assert INDEX_PATH.is_file()
    schema = load_json(SCHEMA_PATH)
    assert schema["required"]
    assert "exact_quote" in json.dumps(schema, ensure_ascii=False)


def test_dataset_passes_deterministic_checks() -> None:
    findings = validate_dataset()
    assert findings == [], [item.model_dump() for item in findings]


def test_answerable_cases_have_quote_and_version() -> None:
    manifest = load_manifest()
    cases = load_cases()
    answerable = [case for case in cases if case["should_answer"]]
    assert answerable, "至少应有可回答案例"
    for case in answerable:
        assert case["expected_evidence"], case["case_id"]
        for evidence in case["expected_evidence"]:
            meta = manifest[evidence["document_id"]]
            assert evidence["version"] == meta["version"]
            assert evidence["exact_quote"]
            assert evidence["source"] == meta["source"]


def test_required_risk_slices_present() -> None:
    cases = load_cases()
    categories = {case["category"] for case in cases}
    assert REQUIRED_CATEGORIES <= categories
    assert any(case["category"] == "cross_tenant" for case in cases)
    assert any(case["category"] == "prompt_injection" for case in cases)
    assert any(case["category"] == "no_answer" for case in cases)
    assert any(case["category"] == "conflict" for case in cases)
    assert any(case["category"] == "stale_version" for case in cases)


def test_splits_are_partitioned() -> None:
    cases = load_cases()
    counts = Counter(case["split"] for case in cases)
    assert counts["development"] >= 1
    assert counts["validation"] >= 1
    assert counts["holdout"] >= 1
    assert sum(counts.values()) == 44


def test_gold_v01_has_human_review_records() -> None:
    index = load_json(INDEX_PATH)
    cases = load_cases()
    gold = [case for case in cases if case["provenance"]["review_status"] == "gold"]
    assert index["gold_status"] == "approved_v0.1"
    assert index["gold_count"] == 24
    assert len(gold) == 24
    assert set(index["gold_case_ids"]) == {case["case_id"] for case in gold}
    for case in gold:
        assert case["review"]["reviewer"] == "阿明"
        assert case["review"]["reviewed_at"] == "2026-09-13"
        assert case["review"]["decision"] == "ACCEPT_GOLD"


def test_a1_does_not_treat_tenant_current_docs_as_retrieval_violations() -> None:
    """A1：同租户当前文档不得再列入 forbidden_document_ids。"""
    cases = {case["case_id"]: case for case in load_cases()}
    assert cases["rag-026"]["forbidden_document_ids"] == []
    assert cases["rag-037"]["forbidden_document_ids"] == []
    assert "audience_ranking" in cases["rag-026"]["tags"]


def test_rag036_must_not_return_employee_id() -> None:
    """A1：检索命中同租户手册不记越权；生成层仍不得返回编制编号。"""
    cases = {case["case_id"]: case for case in load_cases()}
    case = cases["rag-036"]
    assert case["should_answer"] is False
    assert case["forbidden_document_ids"] == []
    assert "NW-HR-001" in case["forbidden_answer_points"]
    assert case["expected_document_ids"] == []
    assert case["expected_evidence"] == []
    assert "不得返回员工编制编号" in case["expected_answer_points"]
    assert "acl_planned" in case["tags"]


def test_corpus_is_authorized_synthetic() -> None:
    manifest = load_json(CORPUS_MANIFEST)
    assert manifest["authorized"] is True
    assert "合成" in manifest["authorization_notes"]


def test_quote_mismatch_is_detected() -> None:
    cases = load_cases()
    evidence = cases[0]["expected_evidence"][0]
    assert quote_in_source(evidence["source"], evidence["exact_quote"]) is True
    assert quote_in_source(evidence["source"], "这段文字不在任何语料快照中-RAG004") is False
