"""python -m evals.chunk_auto_routing.run --output <report.json>"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from sqlmodel import Session, SQLModel, col, create_engine, select

from app.rag.chunking.base import Chunk, ChunkParams
from app.rag.chunking.factory import get_chunking_strategy
from app.rag.chunking.routing import route, signals_from_parsed
from app.rag.cleaning import clean_document
from app.rag.document_parsers.base import ParsedBlock, ParsedDocument
from app.rag.embeddings.mock import MockEmbeddingProvider
from evals.chunk_structure_integrity.evaluate import adapt_chunks, evaluate_chunks, load_cases


def _metrics(source, chunks, protected):
    records, inferred = adapt_chunks(source, chunks)
    return evaluate_chunks(source, records, protected), inferred


def _persisted_chunks(session, document_id):
    from app.models.rag import DocumentChunk

    rows = session.exec(select(DocumentChunk).where(
        col(DocumentChunk.document_id) == document_id,
    ).order_by(col(DocumentChunk.chunk_index))).all()
    return [Chunk(text=r.content, index=r.chunk_index,
                  metadata=json.loads(r.chunk_metadata or "{}")) for r in rows]


async def run():
    from app.models.rag import Document
    from app.rag.service import RAGService

    params = ChunkParams(chunk_size=40, chunk_overlap=0)
    provider = MockEmbeddingProvider(dim=8)
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    rows = []
    frozen_path = Path("evals/chunk_structure_integrity/candidate.json")
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    dataset = Path("evals/chunk_structure_integrity/cases.json")
    dataset_hash = hashlib.sha256(dataset.read_bytes()).hexdigest()
    if frozen["dataset_sha256"] != dataset_hash or frozen["params"] != {
        "chunk_size": 40, "chunk_overlap": 0,
    }:
        raise ValueError("Frozen RAG-021 comparison dataset/parameter mismatch")
    try:
        for case in load_cases():
            for mode in ("text", "blocks", "bbox", "parser"):
                blocks = [] if mode == "text" else [ParsedBlock(
                    type="paragraph", text=case["text"], order=0, page=1,
                    bbox=(0, 0, 100, 100) if mode == "bbox" else None,
                )]
                parsed = ParsedDocument(text=case["text"], title=case["id"],
                                        source=case["id"] + mode,
                                        extension="txt", content_type="text/plain", blocks=blocks)
                if mode == "parser":
                    from app.rag.document_parsers.text import TextDocumentParser

                    parsed = TextDocumentParser().extract(
                        case["text"].encode("utf-8"), case["id"] + ".md", "text/plain",
                    )
                effective = clean_document(parsed).document
                # Identical RAG-021 dataset/parameters; canonical newline source
                # is explicit and never claimed to be original parser offsets.
                protected = []
                for unit in case["protected"]:
                    text = unit["text"].replace("\r\n", "\n").replace("\r", "\n")
                    cursor = 0
                    for _ in range(unit.get("occurrence", 0) + 1):
                        start = effective.text.find(text, cursor)
                        if start < 0:
                            raise ValueError("Protected unit missing after conservative cleaning")
                        cursor = start + len(text)
                    protected.append({"start": start, "end": cursor})
                decision = route(signals_from_parsed(effective), requested="auto", configured_default="structured")
                strategy = get_chunking_strategy(decision.strategy, embedding=provider)
                rag021_chunks = await strategy.split(case["text"], params=params)
                baseline, baseline_inferred = _metrics(case["text"], rag021_chunks, case["protected"])
                if decision.strategy in {"format_aware", "layout_aware"} and effective.blocks:
                    control_chunks = await strategy.split_blocks(effective.blocks, params=params)
                else:
                    control_chunks = await strategy.split(effective.text, params=params)
                control, control_inferred = _metrics(effective.text, control_chunks, protected)
                with Session(engine) as session:
                    service = RAGService(session, tenant_id="synthetic-eval", embedding_provider=provider)
                    doc = await service.ingest_parsed_document(
                        parsed, user_id="synthetic-user", strategy="auto",
                        chunk_params={"chunk_size": 40, "chunk_overlap": 0},
                    )
                    document_id = doc.id
                    session.commit()
                with Session(engine) as session:
                    doc = session.get(Document, document_id)
                    before = _persisted_chunks(session, document_id)
                    plan_before = doc.chunk_plan
                    candidate, candidate_inferred = _metrics(effective.text, before, protected)
                    service = RAGService(session, tenant_id="synthetic-eval", embedding_provider=provider)
                    await service.reindex_document_in_place(
                        doc, parsed, content_hash="synthetic-same-source",
                    )
                    session.commit()
                with Session(engine) as session:
                    doc = session.get(Document, document_id)
                    after = _persisted_chunks(session, document_id)
                    replay, replay_inferred = _metrics(effective.text, after, protected)
                    def signature(chunks):
                        return [(c.text, c.metadata.get("source_start"), c.metadata.get("source_end")) for c in chunks]

                    replay_equal = signature(before) == signature(after)
                    plan_equal = json.loads(plan_before) == json.loads(doc.chunk_plan)
                rows.append({"case_id": case["id"], "tier": case["tier"], "input_mode": mode,
                             "strategy": decision.strategy, "reason": decision.reason,
                             "original_chars": len(case["text"]), "effective_chars": len(effective.text),
                             "rag021_split_original_metrics": baseline,
                             "frozen_rag021_metrics": next(
                                 x["metrics"] for x in frozen["rows"]
                                 if x["case_id"] == case["id"] and x["strategy"] == decision.strategy
                                 and x["layer"] == "chunks"
                             ),
                             "same_entry_control_metrics": control, "candidate_metrics": candidate,
                             "replay_metrics": replay, "plan_equal": plan_equal, "replay_equal": replay_equal,
                             "inferred_source_chunks": {"rag021": baseline_inferred, "control": control_inferred,
                                                        "candidate": candidate_inferred, "replay": replay_inferred}})
    finally:
        engine.dispose()
    code_paths = sorted(Path("app/rag/chunking").rglob("*.py")) + [
        Path("app/rag/service.py"), Path(__file__),
        Path("evals/chunk_structure_integrity/evaluate.py"),
        Path("app/rag/document_parsers/text.py"), Path("app/rag/cleaning.py"),
        Path("app/models/rag.py"), Path("app/rag/embeddings/mock.py"),
    ]
    return {"version": "auto-routing-service-v0.1", "metric_version": "structure-integrity-v0.1",
            "dataset_sha256": dataset_hash,
            "code_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in code_paths},
            "frozen_rag021_report_sha256": hashlib.sha256(frozen_path.read_bytes()).hexdigest(),
            "params": {"chunk_size": 40, "chunk_overlap": 0},
            "embedding": {"provider": "MockEmbeddingProvider", "model": "synthetic", "dimensions": 8},
            "provenance": "AI-authored Smoke/Adversarial; not Gold",
            "scope": (
                "Local in-memory SQLite; actual TextDocumentParser.extract on synthetic UTF-8 Markdown bytes "
                "and synthetic ParsedDocument blocks/bbox. "
                "No real Office/PDF parser, network or retrieval quality claims"
            ),
            "comparison_limits": (
                "RAG-021 split(text) on original source is diagnostic only for blocks/bbox. "
                "Candidate/control/replay use conservative cleaned source. "
                "Inferred spans are exact-match diagnostics, not persisted provenance. "
                "Single-block bbox tests do not prove multi-column reconstruction. "
                "Text parser cases retain authoritative parsed.text rather than stripped line-block source. "
                "Missing whitespace and broken units are retained, not rounded to success."
            ),
            "rows": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(run())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
