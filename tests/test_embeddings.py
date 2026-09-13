"""嵌入模型层测试。"""

from __future__ import annotations

import pytest


def _make_provider(dim: int = 1024):
    from app.rag.embeddings.openai_compatible import OpenAICompatibleEmbeddingProvider

    return OpenAICompatibleEmbeddingProvider(
        base_url="https://embed.example.com/v1",
        api_key="key",
        model="text-embedding-v3",
        dim=dim,
    )


class _FakeResponse:
    def __init__(self, vectors: list[list[float]]) -> None:
        self._vectors = vectors

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "data": [
                {"index": idx, "embedding": vec}
                for idx, vec in enumerate(self._vectors)
            ]
        }


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse, **_: object) -> None:
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, *args: object, **kwargs: object) -> _FakeResponse:
        return self._response


@pytest.mark.asyncio
async def test_embed_accepts_matching_dimension(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    provider = _make_provider(dim=4)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **kw: _FakeAsyncClient(_FakeResponse([[1.0, 2.0, 3.0, 4.0]]), **kw),
    )
    vectors = await provider.embed(["你好"])
    assert vectors == [[1.0, 2.0, 3.0, 4.0]]


@pytest.mark.asyncio
async def test_embed_rejects_dimension_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from app.rag.embeddings.base import EmbeddingDimensionError

    provider = _make_provider(dim=1024)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **kw: _FakeAsyncClient(_FakeResponse([[1.0, 2.0, 3.0]]), **kw),
    )
    with pytest.raises(EmbeddingDimensionError, match="维度 3 与配置 EMBEDDING_DIM=1024"):
        await provider.embed(["你好"])
