"""Evaluator adversarial tests, runnable with --noconftest to avoid shared DB."""

from types import SimpleNamespace

from evals.chunk_structure_integrity.evaluate import adapt_chunks, evaluate_chunks, load_cases


def record(source, start=0, end=None):
    end = len(source) if end is None else end
    return {
        "text": source[start:end],
        "source_spans": [{"start": start, "end": end, "chunk_start": 0, "chunk_end": end - start}],
    }


def test_dataset_synthetic_tiers_and_annotation_validity():
    cases = load_cases()
    assert len({case["id"] for case in cases}) == len(cases)
    assert {case["tier"] for case in cases} == {"Smoke", "Adversarial"}
    for case in cases:
        result = evaluate_chunks(case["text"], [record(case["text"])], case["protected"])
        assert result["coverage"] == 1
        assert result["broken_units"] == 0


def test_evaluator_detects_source_loss_reordering_and_structure_breakage():
    source = "abcdefgh"
    chunks = [record(source, 4, 8), record(source, 0, 3)]
    result = evaluate_chunks(source, chunks, [{"start": 2, "end": 6}])
    assert result["missing_chars"] == 1
    assert result["out_of_order"] == 1
    assert result["structure_breakage_rate"] == 1


def test_evaluator_rejects_forged_ranges_and_altered_original():
    chunk = record("abc")
    chunk["text"] = "aXc"
    result = evaluate_chunks("abc", [chunk], [{"start": 0, "end": 3}])
    assert result["invalid_spans"] == 1
    assert result["coverage"] == 0
    assert result["broken_units"] == 1


def test_derived_header_not_counted_as_source_coverage():
    chunk = {
        "text": "headrow",
        "source_spans": [{"start": 4, "end": 7, "chunk_start": 4, "chunk_end": 7}],
        "derived_spans": [{"chunk_start": 0, "chunk_end": 4, "kind": "repeated_header"}],
    }
    result = evaluate_chunks("headrow", [chunk], [{"start": 4, "end": 7}])
    assert result["covered_chars"] == 3
    assert result["derived_context_chars"] == 4
    assert result["broken_units"] == 0
    chunk["source_spans"].append({"start": 0, "end": 4, "chunk_start": 0, "chunk_end": 4})
    assert evaluate_chunks("headrow", [chunk], [])["invalid_spans"] == 1


def test_overlapping_original_ranges_preserve_union_coverage():
    result = evaluate_chunks("abcdef", [record("abcdef", 0, 4), record("abcdef", 2, 6)], [])
    assert result["covered_chars"] == 6
    assert result["out_of_order"] == 0
    assert result["length_distribution"]["sorted_chars"] == [4, 4]


def test_metadata_adapter_keeps_table_header_derived_and_marks_inference():
    chunks = [SimpleNamespace(text="headrow", metadata={
        "source_start": 4, "source_end": 7, "derived_prefix": "head",
    })]
    records, inferred = adapt_chunks("headrow", chunks)
    assert inferred == 0
    result = evaluate_chunks("headrow", records, [])
    assert result["covered_chars"] == 3
    assert result["derived_context_chars"] == 4
    records, inferred = adapt_chunks("a b a", [
        SimpleNamespace(text="a", metadata={}),
        SimpleNamespace(text="a", metadata={}),
    ])
    assert inferred == 2
    assert [r["source_spans"][0]["start"] for r in records] == [0, 4]
