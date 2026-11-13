"""查询改写单元测试：只覆盖确定性规则，不访问网络。"""

from datetime import UTC, datetime

from app.rag.query_rewrite import rewrite_query

AS_OF = datetime(2026, 11, 10, tzinfo=UTC)


def test_future_year_question_appends_preview_terms() -> None:
    result = rewrite_query("2027 年标准套餐打算卖多少钱？", as_of=AS_OF)
    assert result.expanded
    assert result.text.startswith("2027 年标准套餐打算卖多少钱？")
    assert "2027-01-01" in result.text
    assert "价格调整预告" in result.text
    assert "预告价格" in result.text


def test_present_day_price_question_does_not_open_preview() -> None:
    result = rewrite_query("今天标准套餐月费是 249 吗？今天是 2026-09-13。", as_of=AS_OF)
    assert "价格调整预告" not in result.text
    assert "预告价格" not in result.text
    assert "尚未生效" not in result.text
    assert result.text.startswith("今天标准套餐月费是 249 吗？今天是 2026-09-13。")
    if result.expanded:
        assert "现行" in result.text or "当前收费标准" in result.text


def test_empty_query_is_unchanged() -> None:
    result = rewrite_query("  ", as_of=AS_OF)
    assert result.text == ""
    assert result.expanded is False


def test_rewrite_is_idempotent_on_already_expanded_text() -> None:
    first = rewrite_query("2027 年标准套餐打算卖多少钱？", as_of=AS_OF)
    second = rewrite_query(first.text, as_of=AS_OF)
    assert second.text == first.text
    assert second.expanded is False


def test_decide_requires_slice_gain_without_main_drop() -> None:
    from tests.eval.query_rewrite import decide_query_rewrite

    def _report(*, overall: float, tuning: float, validation: float, effective: float, violations: int = 0) -> dict:
        safety = {"violation_cases": violations}

        def recall(value: float) -> dict:
            return {"recall": {"@5": value}, "safety": safety}
        return {
            "overall": recall(overall),
            "decision_inputs": {"summary": recall(tuning)},
            "by_split": {"validation": recall(validation), "holdout": recall(1.0)},
            "by_category": {"effective_date": recall(effective)},
        }

    improved = decide_query_rewrite(
        _report(overall=0.97, tuning=0.96, validation=0.8333, effective=0.6667),
        _report(overall=0.97, tuning=0.96, validation=1.0, effective=1.0),
    )
    assert improved["decision"] == "improved"
    assert "validation" in improved["slice_gains"]

    unchanged = decide_query_rewrite(
        _report(overall=0.97, tuning=0.96, validation=0.8333, effective=0.6667),
        _report(overall=0.97, tuning=0.96, validation=0.8333, effective=0.6667),
    )
    assert unchanged["decision"] == "not_improved"

    dropped = decide_query_rewrite(
        _report(overall=0.97, tuning=0.96, validation=0.8333, effective=0.6667),
        _report(overall=0.95, tuning=0.96, validation=1.0, effective=1.0),
    )
    assert dropped["decision"] == "not_improved"
    assert "overall_recall@5_drop" in dropped["failed_gates"]
