"""锁定 ADR-0002：本阶段正式 VectorStore 为 Local。"""

from app.core.config import settings


def test_official_vectorstore_default_is_local() -> None:
    assert settings.RAG_VECTOR_STORE == "local"
