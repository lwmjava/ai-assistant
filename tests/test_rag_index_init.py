"""检索器初始化与索引故障隔离的合成回归。"""

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.agents.pipeline import AgentPipeline, AgentState
from app.core.config import settings
from app.llm.mock import MockLLMProvider
from app.models.rag import Document, DocumentChunk
from app.rag.embeddings.mock import MockEmbeddingProvider
from app.rag.index_registry import IndexUnavailableError
from app.rag.retrieval_status import RetrievalStatus
from app.rag.service import RAGService


@pytest.mark.parametrize("backend", ["native", "langchain"])
async def test_legacy_index_failure_is_deferred_to_retrieval(tmp_path, monkeypatch, backend):
    monkeypatch.setattr(settings, "RAG_BACKEND", backend)
    monkeypatch.setattr(settings, "RAG_VECTOR_STORE", "local")
    engine = create_engine(f"sqlite:///{tmp_path / 'isolated.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        doc = Document(tenant_id="tenant", user_id="owner", title="synthetic")
        session.add(doc)
        session.flush()
        chunk = DocumentChunk(tenant_id="tenant", document_id=doc.id,
                              content="PRIVATE_LEGACY_SENTINEL", embedding="[1,0]")
        session.add(chunk)
        session.commit()
        rag = RAGService(session, "tenant", embedding_provider=MockEmbeddingProvider(dim=64))
        retriever = rag.make_retriever()
        # 构建成功不等于允许读取身份未知的向量。
        with pytest.raises(IndexUnavailableError):
            rag._read_index_id()
        assert await retriever.retrieve("中国的首都在哪里", "") == ""
        assert retriever.last_outcome.status is RetrievalStatus.UNAVAILABLE
        assert retriever.last_hits == []
        assert retriever.last_outcome.error_type == "IndexUnavailableError"
        state = AgentState(user_input="中国的首都在哪里")
        pipeline = AgentPipeline(MockLLMProvider(), retriever=retriever)
        events = [event async for event in pipeline.run_stream(state)]
        assert any(event.type == "done" for event in events)
        assert not any(event.type == "error" for event in events)
        assert state.retrieval_status == "unavailable"
        assert "PRIVATE_LEGACY_SENTINEL" not in state.answer
        session.rollback()
        session.refresh(chunk)
        assert chunk.index_id is None
    engine.dispose()


async def test_empty_index_remains_no_hit(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "RAG_BACKEND", "native")
    monkeypatch.setattr(settings, "RAG_VECTOR_STORE", "local")
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        rag = RAGService(session, "empty", embedding_provider=MockEmbeddingProvider(dim=64))
        retriever = rag.make_retriever()
        assert await retriever.retrieve("首都", "") == ""
        assert retriever.last_outcome.status is RetrievalStatus.NO_HIT
    engine.dispose()
