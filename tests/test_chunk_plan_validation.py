"""Persisted plans are complete supported contracts, not arbitrary defaults."""

import copy
import json

import pytest

from app.rag.chunking.base import ChunkParams
from app.rag.chunking.factory import resolve_strategy_with_decision
from app.rag.service import RAGService


def valid_plan():
    return RAGService._build_chunk_plan(
        resolve_strategy_with_decision("synthetic", "fixed_chars"),
        ChunkParams(chunk_size=20, chunk_overlap=0),
    )


@pytest.mark.parametrize("mutation", [
    "missing_version", "missing_params", "unknown_version", "unknown_strategy",
    "wrong_params_type", "missing_size", "unknown_key", "server_policy",
    "size_boolean", "size_zero", "size_string", "negative_overlap",
    "unknown_child", "child_unknown_key", "child_wrong_type", "child_zero",
])
def test_invalid_present_plan_is_rejected(mutation):
    plan = copy.deepcopy(valid_plan())
    p = plan["chunk_params"]
    if mutation == "missing_version":
        plan.pop("version")
    elif mutation == "missing_params":
        plan.pop("chunk_params")
    elif mutation == "unknown_version":
        plan["version"] = "unknown-v999"
    elif mutation == "unknown_strategy":
        plan["strategy"] = "not_registered"
    elif mutation == "wrong_params_type":
        plan["chunk_params"] = 7
    elif mutation == "missing_size":
        p.pop("chunk_size")
    elif mutation == "unknown_key":
        p["not_a_parameter"] = 1
    elif mutation == "server_policy":
        p["input_policy"] = {}
    elif mutation == "size_boolean":
        p["chunk_size"] = True
    elif mutation == "size_zero":
        p["chunk_size"] = 0
    elif mutation == "size_string":
        p["chunk_size"] = "20"
    elif mutation == "negative_overlap":
        p["chunk_overlap"] = -1
    elif mutation == "unknown_child":
        p["child_strategy"] = "not_registered"
    elif mutation == "child_unknown_key":
        p["child_params"] = {"not_a_parameter": 1}
    elif mutation == "child_wrong_type":
        p["child_params"] = {"chunk_size": "20"}
    elif mutation == "child_zero":
        p["child_params"] = {"chunk_size": 0}
    with pytest.raises(ValueError, match="chunk_plan_invalid"):
        RAGService._load_chunk_plan(json.dumps(plan))


def test_only_missing_plan_uses_legacy_path():
    assert RAGService._load_chunk_plan(None) is None
    for raw in ("", "null", "[]", "{broken}"):
        with pytest.raises(ValueError, match="chunk_plan_invalid"):
            RAGService._load_chunk_plan(raw)


def test_partial_child_overrides_are_valid_and_preserved():
    plan = valid_plan()
    plan["strategy"] = "parent_child"
    plan["chunk_params"]["child_params"] = {"chunk_size": 10, "chunk_overlap": 0}
    assert RAGService._load_chunk_plan(json.dumps(plan)) == plan
