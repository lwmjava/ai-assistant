"""生效日过滤决策辅助的单元测试，不访问网络。"""

from tests.eval.effective_date_filter import decide_effective_date_filter


def _report(
    *,
    overall: float,
    tuning: float,
    validation: float,
    effective: float,
    violations: int = 0,
) -> dict:
    safety = {"violation_cases": violations}

    def recall(value: float) -> dict:
        return {"recall": {"@5": value}, "safety": safety}

    return {
        "overall": recall(overall),
        "decision_inputs": {"summary": recall(tuning)},
        "by_split": {"validation": recall(validation), "holdout": recall(1.0)},
        "by_category": {"effective_date": recall(effective)},
    }


def test_decide_improved_when_slices_rise_and_future_doc_is_hit() -> None:
    result = decide_effective_date_filter(
        _report(overall=0.9714, tuning=0.9643, validation=0.8333, effective=0.6667),
        _report(overall=1.0, tuning=1.0, validation=1.0, effective=1.0),
        rag031_expected_doc_hit=True,
    )
    assert result["decision"] == "improved"
    assert result["future_doc_recalled_by_query_date"] is True
    assert "validation" in result["slice_gains"]
    assert "effective_date" in result["slice_gains"]


def test_decide_not_improved_when_future_doc_still_missed() -> None:
    result = decide_effective_date_filter(
        _report(overall=0.9714, tuning=0.9643, validation=0.8333, effective=0.6667),
        _report(overall=0.9714, tuning=0.9643, validation=0.8333, effective=0.6667),
        rag031_expected_doc_hit=False,
    )
    assert result["decision"] == "not_improved"
    assert "rag_031_future_doc_not_retrieved" in result["failed_gates"]
    assert result["future_doc_recalled_by_query_date"] is False


def test_decide_rejects_overall_drop_or_violations() -> None:
    dropped = decide_effective_date_filter(
        _report(overall=0.9714, tuning=0.9643, validation=0.8333, effective=0.6667),
        _report(overall=0.95, tuning=0.9643, validation=1.0, effective=1.0),
        rag031_expected_doc_hit=True,
    )
    assert dropped["decision"] == "not_improved"
    assert "overall_recall@5_drop" in dropped["failed_gates"]

    unsafe = decide_effective_date_filter(
        _report(overall=0.9714, tuning=0.9643, validation=0.8333, effective=0.6667),
        _report(overall=1.0, tuning=1.0, validation=1.0, effective=1.0, violations=1),
        rag031_expected_doc_hit=True,
    )
    assert unsafe["decision"] == "not_improved"
    assert "safety_violations" in unsafe["failed_gates"]
