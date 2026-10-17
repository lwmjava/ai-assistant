"""Actual persisted auto entry, with fresh-session replay and quality metrics."""

from evals.chunk_structure_integrity.evaluate import evaluate_chunks


async def test_auto_entry_quality_report_and_replay():
    from evals.chunk_auto_routing.run import run

    report = await run()
    assert len(report["rows"]) == 32
    assert {row["input_mode"] for row in report["rows"]} == {"text", "blocks", "bbox", "parser"}
    for row in report["rows"]:
        assert row["replay_equal"], row
        assert row["plan_equal"], row
        assert row["candidate_metrics"] == row["replay_metrics"], row
        assert row["candidate_metrics"] == row["same_entry_control_metrics"], row
        assert row["candidate_metrics"]["out_of_order"] == 0, row
        assert row["candidate_metrics"]["invalid_spans"] == 0, row
        assert row["candidate_metrics"]["coverage"] == 1.0, row
        assert row["candidate_metrics"]["broken_units"] == 0, row
        assert row["candidate_metrics"]["length_distribution"]["count"] > 0, row


def test_shared_quality_oracle_rejects_loss_disorder_and_broken_unit():
    source = "abcd"
    records = [
        {"text": "cd", "source_spans": [{"start": 2, "end": 4, "chunk_start": 0, "chunk_end": 2}]},
        {"text": "a", "source_spans": [{"start": 0, "end": 1, "chunk_start": 0, "chunk_end": 1}]},
    ]
    metrics = evaluate_chunks(source, records, [{"start": 0, "end": 4}])
    assert metrics["coverage"] == 0.75
    assert metrics["missing_chars"] == 1
    assert metrics["out_of_order"] == 1
    assert metrics["broken_units"] == 1
