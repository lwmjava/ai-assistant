"""Actual text-parser line blocks must not destroy source structure."""

import json

import pytest
from sqlmodel import Session, SQLModel, col, create_engine, select

from app.models.rag import Document, DocumentChunk
from app.rag.document_parsers.text import TextDocumentParser
from app.rag.embeddings.mock import MockEmbeddingProvider
from app.rag.service import RAGService


@pytest.mark.parametrize("strategy", ["auto", "format_aware"])
async def test_real_text_parser_preserves_fenced_indent_on_upload_and_replay(strategy):
    text = "```python\ndef f():\n    return 42\n```\n"
    parsed = TextDocumentParser().extract(text.encode(), "source.md", "text/plain")
    assert "    return 42" not in "\n".join(block.text for block in parsed.blocks)
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    provider = MockEmbeddingProvider(dim=4)
    with Session(engine) as session:
        document = await RAGService(session, "audit", provider).ingest_parsed_document(
            parsed, user_id="audit", strategy=strategy, chunk_params={"chunk_size": 40, "chunk_overlap": 0},
        )
        document_id = document.id
        plan = json.loads(document.chunk_plan)
        assert plan["strategy"] == ("structured" if strategy == "auto" else strategy)
    for replay in (True, False):
        with Session(engine) as session:
            chunks = session.exec(select(DocumentChunk).where(
                col(DocumentChunk.document_id) == document_id,
            )).all()
            assert len(chunks) == 1
            assert chunks[0].content == text
            if replay:
                document = session.get(Document, document_id)
                await RAGService(session, "audit", provider).reindex_document_in_place(
                    document, parsed, content_hash="synthetic-same-text",
                )
    engine.dispose()
