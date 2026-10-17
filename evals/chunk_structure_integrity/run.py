"""Run synthetic structure diagnostics without DB, network, or real embeddings.

Usage: python -m evals.chunk_structure_integrity.run --output <report.json>
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from app.rag.embeddings.mock import MockEmbeddingProvider
from evals.chunk_structure_integrity import VERSION
from evals.chunk_structure_integrity.evaluate import adapt_chunks, evaluate_chunks, load_cases


async def run(offline_hard_limit: int | None = None) -> dict:
    """Actual registered strategy results; parent/child are separate layers."""
    from app.rag.chunking.base import ChunkParams
    from app.rag.chunking.factory import build_registry

    registry = build_registry()
    params = ChunkParams(chunk_size=40, chunk_overlap=0)
    if offline_hard_limit is not None:
        from app.rag.embeddings.base import EmbeddingInputPolicy

        # Test oracle counts characters exactly. This is explicitly not a
        # tokenizer or a certified production model limit.
        params.input_policy = EmbeddingInputPolicy(
            max_input_tokens=offline_hard_limit, counter=len,
            counting_method="synthetic-char-oracle", source=VERSION,
        )
    rows = []
    for name in registry.names():
        for case in load_cases():
            strategy = registry.build(name, embedding=MockEmbeddingProvider(dim=8))
            chunks = await strategy.split(
                case["text"], params=params
            )
            layers = {"chunks": chunks}
            if name == "parent_child":
                layers = {
                    "parents": [c for c in chunks if c.metadata.get("kind") == "parent"],
                    "children": [c for c in chunks if c.metadata.get("kind") == "child"],
                }
            for layer, members in layers.items():
                records, inferred = adapt_chunks(case["text"], members)
                metrics = evaluate_chunks(case["text"], records, case["protected"])
                if layer == "children":
                    parents = {p.metadata.get("parent_key"): p for p in layers["parents"]}
                    violations = 0
                    unverified = 0
                    for child in members:
                        parent = parents.get(child.parent_id)
                        if parent is None:
                            violations += 1
                            continue
                        if not all(
                            "source_start" in c.metadata and "source_end" in c.metadata
                            for c in (parent, child)
                        ):
                            unverified += 1
                            continue
                        violations += not (
                            parent.metadata["source_start"] <= child.metadata["source_start"]
                            <= child.metadata["source_end"] <= parent.metadata["source_end"]
                        )
                    metrics["parent_containment_violations"] = violations
                    metrics["parent_containment_unverified"] = unverified
                rows.append({
                    "case_id": case["id"], "tier": case["tier"], "strategy": name,
                    "layer": layer, "inferred_source_chunks": inferred, "metrics": metrics,
                })
    return {
        "version": VERSION,
        "dataset_sha256": hashlib.sha256(
            Path(__file__).with_name("cases.json").read_bytes()
        ).hexdigest(),
        "provenance": "AI-authored synthetic Smoke/Adversarial; not Gold",
        "params": {"chunk_size": 40, "chunk_overlap": 0},
        "offline_test_char_limit": offline_hard_limit,
        "scope": "Structural diagnostics only; not retrieval quality or production input certification",
        "embedding": "Local MockEmbeddingProvider(dim=8); deterministic chain only, no retrieval quality claim",
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline-ref", help="Read only Git revision of chunking package")
    parser.add_argument("--offline-hard-limit", type=int, help="Synthetic character-count test oracle only")
    args = parser.parse_args()
    if args.baseline_ref and args.offline_hard_limit is not None:
        parser.error("Legacy baseline has no input-policy contract")
    with tempfile.TemporaryDirectory(prefix="rag-structure-baseline-") as temporary:
        revision = None
        if args.baseline_ref:
            revision = subprocess.check_output(
                ["git", "rev-parse", "--verify", args.baseline_ref + "^{commit}"], text=True
            ).strip()
            paths = subprocess.check_output(
                ["git", "ls-tree", "-r", "--name-only", revision, "app/rag/chunking"],
                text=True,
            ).splitlines()
            for name in paths:
                if not name.endswith(".py"):
                    continue
                target = Path(temporary) / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(subprocess.check_output(["git", "show", f"{revision}:{name}"]))
            import app.rag.chunking as package

            package.__path__ = [str(Path(temporary) / "app/rag/chunking")]
            for name in list(sys.modules):
                if name.startswith("app.rag.chunking."):
                    del sys.modules[name]
            # Structured strategy delegates to ingestion; baseline must use its
            # matching implementation rather than the concurrently edited file.
            ingestion_path = Path(temporary) / "ingestion.py"
            ingestion_path.write_bytes(subprocess.check_output(
                ["git", "show", f"{revision}:app/rag/ingestion.py"]
            ))
            spec = importlib.util.spec_from_file_location("app.rag.ingestion", ingestion_path)
            if spec is None or spec.loader is None:
                raise RuntimeError("Cannot load baseline ingestion")
            module = importlib.util.module_from_spec(spec)
            sys.modules["app.rag.ingestion"] = module
            spec.loader.exec_module(module)
        report = asyncio.run(run(args.offline_hard_limit))
        report["chunking_revision"] = revision or "working-tree"
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")


if __name__ == "__main__":
    main()
