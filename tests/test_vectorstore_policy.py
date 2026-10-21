"""向量库默认选择与未知配置。"""

import pytest

from app.core.config import Settings, settings
from app.rag.vectorstore.factory import get_vector_store


def test_official_vectorstore_default_is_milvus() -> None:
    assert Settings.model_fields["RAG_VECTOR_STORE"].default == "milvus"
    # 隔离夹具会把运行中的配置改成 local，不能拿这个运行值当作默认值。
    assert settings.RAG_VECTOR_STORE == "local"


def test_unknown_vector_store_does_not_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RAG_VECTOR_STORE", "chroma")
    with pytest.raises(ValueError, match="milvus"):
        get_vector_store(None)  # type: ignore[arg-type]


def test_milvus_setting_selects_milvus_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RAG_VECTOR_STORE", "milvus")
    store = get_vector_store(None)  # type: ignore[arg-type]
    assert type(store).__name__ == "MilvusVectorStore"
