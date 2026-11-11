"""证据冲突验证、有限纠错和控制组的确定性实验测试。"""

from __future__ import annotations

from app.agents.evidence_conflict import (
    MAX_CALLS_PER_CASE,
    REFUSAL_TEXT,
    CandidateAnswer,
    CorrectionContext,
    EvidenceItem,
    apply_correction,
    normalize_value,
    run_bounded_correction,
    verify_answer,
)
from evals.evidence_conflict.dataset import (
    CROSS_TENANT_MARKER,
    build_cases,
    dataset_index,
    evidence_from_case,
)
from evals.evidence_conflict.run import LiveRunRefusedError, refuse_live_run
from tests.eval.evidence_conflict import (
    behavior_match,
    decide_evidence_correction,
    is_decision_case,
    summarize_outcomes,
)


def _memory(**kwargs: object) -> EvidenceItem:
    data: dict[str, object] = {
        "id": "mem-1",
        "source_type": "memory",
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "subject_key": "plan_price",
        "value": "199",
        "content": "当前月费是 199。",
        "authorized": True,
        "version": 2,
        "recorded_at": "2026-11-01T00:00:00+08:00",
    }
    data.update(kwargs)
    return EvidenceItem(**data)  # type: ignore[arg-type]


def _answer(**kwargs: object) -> CandidateAnswer:
    data: dict[str, object] = {
        "text": "月费是 99。",
        "claimed_subject": "plan_price",
        "claimed_value": "99",
        "used_evidence_ids": ("mem-old",),
    }
    data.update(kwargs)
    return CandidateAnswer(**data)  # type: ignore[arg-type]


def _context(**kwargs: object) -> CorrectionContext:
    data: dict[str, object] = {
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "fact_domain": "personal",
        "cancelled": False,
        "budget_remaining": 2,
        "max_corrections": 1,
    }
    data.update(kwargs)
    return CorrectionContext(**data)  # type: ignore[arg-type]


def test_partition_and_verify_ignore_cross_tenant_and_unauthorized() -> None:
    """跨租户和未授权证据不能成为当前事实，也不能泄漏到输出。"""
    items = [
        _memory(id="mem-own", value="199", content="当前月费是 199。"),
        _memory(
            id="mem-other",
            tenant_id="tenant-b",
            user_id="user-b",
            value=CROSS_TENANT_MARKER,
            content=f"秘密是{CROSS_TENANT_MARKER}",
        ),
    ]
    leaked = _answer(
        text=f"月费是{CROSS_TENANT_MARKER}。",
        claimed_value=CROSS_TENANT_MARKER,
        used_evidence_ids=("mem-other",),
    )
    verdict = verify_answer(leaked, items, _context())
    assert verdict.ok is False
    assert verdict.used_ineligible is True
    assert verdict.leaked is True
    assert verdict.winning_value == "199"

    result = run_bounded_correction(leaked, items, _context())
    assert result.status == "corrected"
    assert result.answer.claimed_value == "199"
    assert CROSS_TENANT_MARKER not in result.answer.text
    assert result.leaked is False
    assert "mem-other" not in result.answer.used_evidence_ids


def test_old_memory_is_corrected_to_newer_version() -> None:
    """被取代的旧记忆不能继续作答，应纠正为新版本。"""
    items = [
        _memory(id="mem-old", value="99", content="旧月费是 99。", version=1, superseded=True),
        _memory(id="mem-new", value="199", content="新月费是 199。", version=2),
    ]
    result = run_bounded_correction(_answer(), items, _context())
    assert result.status == "corrected"
    assert result.conflict_type in {"none", "old_vs_new_memory"}
    assert result.answer.claimed_value == "199"
    assert "99" not in result.answer.text
    assert result.attempts == 1
    assert result.calls <= MAX_CALLS_PER_CASE


def test_memory_beats_retrieval_for_personal_facts() -> None:
    """个人事实以已授权的用户陈述记忆为准。"""
    items = [
        _memory(id="mem-name", subject_key="preferred_name", value="张工", content="请叫我张工。"),
        EvidenceItem(
            id="doc-name",
            source_type="retrieval",
            tenant_id="tenant-a",
            user_id="user-a",
            subject_key="preferred_name",
            value="李工",
            content="文档写着李工。",
        ),
    ]
    answer = _answer(
        text="资料显示称呼是李工。",
        claimed_subject="preferred_name",
        claimed_value="李工",
        used_evidence_ids=("doc-name",),
    )
    result = run_bounded_correction(answer, items, _context(fact_domain="personal"))
    assert result.status == "corrected"
    assert result.answer.claimed_value == "张工"


def test_retrieval_beats_memory_for_published_facts() -> None:
    """已发布政策以已授权检索文档为准。"""
    items = [
        _memory(id="mem-price", subject_key="list_price", value="99", content="记忆里是 99。"),
        EvidenceItem(
            id="doc-price",
            source_type="retrieval",
            tenant_id="tenant-a",
            user_id="user-a",
            subject_key="list_price",
            value="129",
            content="公示价是 129。",
            version=3,
            recorded_at="2026-11-03T00:00:00+08:00",
            grade="document",
        ),
    ]
    answer = _answer(
        text="我记得公示价是 99。",
        claimed_subject="list_price",
        claimed_value="99",
        used_evidence_ids=("mem-price",),
    )
    result = run_bounded_correction(answer, items, _context(fact_domain="published"))
    assert result.status == "corrected"
    assert result.answer.claimed_value == "129"


def test_irreconcilable_conflict_refuses() -> None:
    """两份同等有效且互相矛盾的记忆应拒答，而不是改成其中一边。"""
    items = [
        _memory(
            id="mem-a",
            subject_key="shift",
            value="早班",
            content="一份说早班。",
            version=2,
            recorded_at="2026-11-05T00:00:00+08:00",
        ),
        _memory(
            id="mem-b",
            subject_key="shift",
            value="晚班",
            content="一份说晚班。",
            version=2,
            recorded_at="2026-11-05T00:00:00+08:00",
        ),
    ]
    answer = _answer(text="班次是早班。", claimed_subject="shift", claimed_value="早班", used_evidence_ids=("mem-a",))
    result = run_bounded_correction(answer, items, _context())
    assert result.status == "refused"
    assert result.answer.text == REFUSAL_TEXT
    assert result.answer.claimed_value == ""


def test_already_correct_answer_is_not_rewritten() -> None:
    """正确回答控制组不得被误改。"""
    items = [_memory(id="mem-ok", value="199", content="当前月费是 199。")]
    answer = _answer(text="月费是 199。", claimed_value="199", used_evidence_ids=("mem-ok",))
    result = run_bounded_correction(answer, items, _context())
    assert result.status == "unchanged"
    assert result.attempts == 0
    assert result.answer.claimed_value == "199"
    assert result.answer.text == "月费是 199。"


def test_budget_exhaustion_does_not_correct() -> None:
    """预算耗尽时必须停止，不得继续纠错。"""
    items = [
        _memory(id="mem-old", value="99", content="旧月费是 99。", version=1, superseded=True),
        _memory(id="mem-new", value="199", content="新月费是 199。", version=2),
    ]
    result = run_bounded_correction(_answer(), items, _context(budget_remaining=0))
    assert result.status == "budget_exhausted"
    assert result.attempts == 0
    assert result.calls == 0
    assert result.answer.claimed_value == "99"


def test_cancelled_run_stops_without_correction() -> None:
    """已取消的运行不能再发起验证后的纠错。"""
    items = [
        _memory(id="mem-old", value="99", content="旧月费是 99。", superseded=True),
        _memory(id="mem-new", value="199", content="新月费是 199。", version=2),
    ]
    result = run_bounded_correction(_answer(), items, _context(cancelled=True))
    assert result.status == "cancelled"
    assert result.attempts == 0
    assert result.calls == 0
    assert result.answer.claimed_value == "99"


def test_correction_attempts_are_bounded() -> None:
    """纠错次数不能超过上下文上限。"""
    items = [
        _memory(id="mem-old", value="99", content="旧月费是 99。", superseded=True),
        _memory(id="mem-new", value="199", content="新月费是 199。", version=2),
    ]
    result = run_bounded_correction(_answer(), items, _context(max_corrections=0, budget_remaining=2))
    assert result.attempts == 0
    assert result.status == "budget_exhausted"
    assert result.calls <= MAX_CALLS_PER_CASE


def test_apply_correction_strips_ineligible_markers() -> None:
    """纠错后的文本必须去掉淘汰证据中的内容。"""
    answer = _answer(text=f"月费是{CROSS_TENANT_MARKER}。", claimed_value=CROSS_TENANT_MARKER)
    verdict = verify_answer(
        answer,
        [_memory(id="mem-own", value="199", content="当前月费是 199。")],
        _context(),
    )
    fixed = apply_correction(answer, verdict, (CROSS_TENANT_MARKER,))
    assert CROSS_TENANT_MARKER not in fixed.text
    assert normalize_value(fixed.claimed_value) == normalize_value("199")


def test_locked_dataset_shape() -> None:
    """锁定数据集至少 50 条，且覆盖全部约定切片和正确回答控制组。"""
    cases = build_cases()
    index = dataset_index(cases)
    assert index["case_count"] >= 50
    assert index["case_count"] == 56
    assert index["by_slice"]["already_correct"] == 20
    assert index["by_slice"]["old_vs_new_memory"] == 10
    assert index["by_slice"]["memory_vs_retrieval"] == 10
    assert index["by_slice"]["refuse"] == 6
    assert index["by_slice"]["budget_exhausted"] == 4
    assert index["by_slice"]["safety"] == 6
    assert set(index["by_split"]) == {"development", "validation", "holdout"}
    assert sum(index["by_split"].values()) == 56
    ids = [case["case_id"] for case in cases]
    assert len(ids) == len(set(ids))


def _run_case(case: dict) -> tuple[dict, dict, object]:
    items = evidence_from_case(case)
    baseline = case["baseline_answer"]
    answer = CandidateAnswer(
        text=baseline["text"],
        claimed_subject=baseline["claimed_subject"],
        claimed_value=baseline["claimed_value"],
        used_evidence_ids=tuple(baseline["used_evidence_ids"]),
    )
    context = CorrectionContext(
        tenant_id=case["tenant_id"],
        user_id=case["user_id"],
        fact_domain=case["fact_domain"],
        cancelled=case["context"]["cancelled"],
        budget_remaining=case["context"]["budget_remaining"],
        max_corrections=case["context"]["max_corrections"],
    )
    control = {
        "case_id": case["case_id"],
        "text": answer.text,
        "value": answer.claimed_value,
        "status": "unchanged",
        "attempts": 0,
        "calls": 0,
        "leaked": False,
        "used_ineligible": False,
        "behavior_match": False,
        "latency_ms": 1.0,
    }
    result = run_bounded_correction(answer, items, context)
    treatment = {
        "case_id": case["case_id"],
        "text": result.answer.text,
        "value": result.answer.claimed_value,
        "status": result.status,
        "attempts": result.attempts,
        "calls": result.calls,
        "leaked": result.leaked,
        "used_ineligible": result.used_ineligible,
        "behavior_match": behavior_match(case, result),
        "latency_ms": 1.1,
    }
    return control, treatment, result


def test_locked_dataset_behaviors_are_reproducible() -> None:
    """冲突、拒答、预算耗尽、取消和误改控制组在锁定样本上可复现。"""
    cases = build_cases()
    control_rows = []
    treatment_rows = []
    for case in cases:
        control, treatment, result = _run_case(case)
        assert result.calls <= MAX_CALLS_PER_CASE
        assert result.attempts <= case["context"]["max_corrections"]
        assert behavior_match(case, result)
        for marker in case["ineligible_content_must_not_appear"]:
            if case["expected"]["status"] != "cancelled":
                assert marker not in result.answer.text
        control_rows.append(control)
        treatment_rows.append(treatment)

    decision_summary = summarize_outcomes(cases, control_rows, treatment_rows, include_holdout=False)
    recorded = summarize_outcomes(cases, control_rows, treatment_rows, include_holdout=True)
    assert all(is_decision_case(case) or case["split"] == "holdout" for case in cases)
    assert decision_summary["case_count"] < recorded["case_count"]
    decision = decide_evidence_correction(decision_summary)
    assert decision["failed_gates"] == []
    assert decision["decision"] == "fixtures_passed"
    assert decision["production_decision"] == "keep_production_off"
    assert decision_summary["safety_violations"] == 0
    assert decision_summary["miscorrection_rate"] == 0


def test_decide_rejects_when_gates_fail() -> None:
    """主指标不足、误改过多、泄漏或延迟超限时不得写成采纳。"""
    base = {
        "control_error_rate": 0.50,
        "treatment_error_rate": 0.20,
        "relative_error_reduction": 0.60,
        "miscorrection_rate": 0.0,
        "safety_violations": 0,
        "bound_violations": 0,
        "behavior_pass_rate": 1.0,
        "latency_ms": {"control_p95": 100.0, "treatment_p95": 110.0},
    }
    passed = decide_evidence_correction(base)
    assert passed["decision"] == "fixtures_passed"

    short = dict(base, relative_error_reduction=0.10, treatment_error_rate=0.45)
    assert "error_reduction_shortfall" in decide_evidence_correction(short)["failed_gates"]

    mis = dict(base, miscorrection_rate=0.10)
    assert "miscorrection_limit" in decide_evidence_correction(mis)["failed_gates"]

    unsafe = dict(base, safety_violations=1)
    assert decide_evidence_correction(unsafe)["reason_code"] == "safety_regression"

    slow = dict(base, latency_ms={"control_p95": 100.0, "treatment_p95": 121.0})
    assert "latency_p95_regression" in decide_evidence_correction(slow)["failed_gates"]

    zero = dict(base, control_error_rate=0.0, treatment_error_rate=0.0, relative_error_reduction=None)
    assert "missing_primary_metric" in decide_evidence_correction(zero)["failed_gates"] or (
        "baseline_error_rate_zero" in decide_evidence_correction(zero)["failed_gates"]
    )
    assert decide_evidence_correction(short)["production_decision"] == "keep_production_off"


def test_decide_ignores_noisy_sub_millisecond_latency_ratio() -> None:
    """对照耗时低于 1ms 时，不用接近 0 的比值判定延迟回归。"""
    base = {
        "control_error_rate": 0.50,
        "treatment_error_rate": 0.20,
        "relative_error_reduction": 0.60,
        "miscorrection_rate": 0.0,
        "safety_violations": 0,
        "bound_violations": 0,
        "behavior_pass_rate": 1.0,
        "latency_ms": {"control_p95": 0.0002, "treatment_p95": 0.07},
    }
    assert decide_evidence_correction(base)["decision"] == "fixtures_passed"
    slow = dict(base, latency_ms={"control_p95": 0.0002, "treatment_p95": 50.1})
    assert "latency_p95_regression" in decide_evidence_correction(slow)["failed_gates"]


def test_runner_refuses_live_or_paid_modes() -> None:
    """未授权时不能启动 live 或付费运行。"""
    refuse_live_run("mock")
    for mode in ("live", "official", "paid", "real"):
        try:
            refuse_live_run(mode)
        except LiveRunRefusedError:
            continue
        raise AssertionError(f"{mode} 应被拒绝")
