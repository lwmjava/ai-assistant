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


def test_version_registry_declares_an_authoritative_baseline() -> None:
    """RAG-034：仓库必须明文写明哪份报告是权威基线。

    「两份 baseline 并存、指标不同、谁也没说谁是权威」是这次核对发现的真实缺口：
    Recall@1 有 0.7952 和 0.8095 两个数，不写明就会被混着引。
    这里只守住"声明存在且指名了冻结基线"，不校验具体指标——指标会随代码演进变。
    """
    registry = INDEX_PATH.parent.parent.parent / "VERSIONS.md"
    assert registry.is_file(), "缺少 evals/VERSIONS.md 版本登记表"
    text = registry.read_text(encoding="utf-8")
    assert "rag-v0.1-baseline-20260919.json" in text
    assert "rag-v0.1-baseline-20261004.json" in text
    # 光列两份不够，必须点出冻结基线；否则仍是谁都能当权威。
    assert "冻结基线（权威）" in text
    # 语料身份两侧必须一起登记，避免 index 改了 manifest 没改。
    assert "northwind-demo-kb" in text
    assert "0.1.0" in text


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
    # 21 不是随手数出来的：RAG-034 审核链核对把 rag-019 / rag-020 / rag-041
    # 三条 no_answer 用例从 Gold 降到 Silver（缺反向证据且无人工说明，
    # 不满足口径 C），降级记录见 index.json 的 downgrade_log。
    # 这里钉死数字，是为了让"悄悄补回 Gold"这类改动必须显式改测试。
    assert index["gold_count"] == 21
    assert len(gold) == 21
    assert set(index["gold_case_ids"]) == {case["case_id"] for case in gold}
    for case in gold:
        assert case["review"]["reviewer"] == "阿明"
        assert case["review"]["reviewed_at"] == "2026-09-13"
        assert case["review"]["decision"] == "ACCEPT_GOLD"


def test_downgraded_cases_carry_a_traceable_decision() -> None:
    """RAG-034：降级不是改个标签了事，必须留下可追溯的降级记录。

    防止两种悄悄发生的回归：把 Silver 直接改回 Gold 而删掉降级记录，
    或保留了记录却没同步 ``review_status`` / ``review.decision``。
    """
    index = load_json(INDEX_PATH)
    cases = {case["case_id"]: case for case in load_cases()}
    log = {entry["case_id"]: entry for entry in index.get("downgrade_log", [])}
    assert set(log) == {"rag-019", "rag-020", "rag-041"}
    gold_ids = set(index["gold_case_ids"])
    for case_id, entry in log.items():
        case = cases[case_id]
        assert case["provenance"]["review_status"] == "silver", case_id
        assert case["review"]["decision"] == "KEEP_SILVER", case_id
        assert entry["from"] == "gold", case_id
        assert entry["to"] == "silver", case_id
        assert entry["reason"], f"{case_id} 降级必须写明原因"
        assert entry["downgraded_at"], f"{case_id} 降级必须写明时间"
        assert case_id not in gold_ids
        # 降级理由写在用例级 notes 与 downgrade_log 里。
        # 刻意**不**写进 review.notes —— 那是人工审核记录字段，Agent 往里写结论
        # 会被读成「人工已复核」，正是本卡要防的事。
        assert "降级" in (case.get("notes") or ""), case_id
        assert "RAG-034" not in (case["review"].get("notes") or ""), (
            f"{case_id} 的 review.notes 混入了 Agent 写的降级说明，污染人工审核记录"
        )


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
