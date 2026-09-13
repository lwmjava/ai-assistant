"""嵌入模型层测试。"""

from __future__ import annotations

import pytest


def _make_provider(dim: int = 1024, batch_size: int = 10):
    from app.rag.embeddings.openai_compatible import OpenAICompatibleEmbeddingProvider

    return OpenAICompatibleEmbeddingProvider(
        base_url="https://embed.example.com/v1",
        api_key="key",
        model="text-embedding-v3",
        dim=dim,
        batch_size=batch_size,
    )


class _FakeResponse:
    def __init__(self, vectors: list[list[float]]) -> None:
        self._vectors = vectors
        self.is_error = False
        self.status_code = 200
        self.text = ""

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
    def __init__(self, response: _FakeResponse, captured: list | None = None, **_: object) -> None:
        self._response = response
        self._captured = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, *args: object, **kwargs: object) -> _FakeResponse:
        payload = kwargs.get("json") or {}
        texts = list(payload.get("input") or [])
        if self._captured is not None:
            self._captured.append(texts)
        dim = len(self._response._vectors[0]) if self._response._vectors else 0
        if len(self._response._vectors) == len(texts):
            return self._response
        return _FakeResponse([[float(i + 1)] * dim for i, _ in enumerate(texts)])


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


@pytest.mark.asyncio
async def test_embed_splits_requests_by_batch_size(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    captured: list[list[str]] = []
    provider = _make_provider(dim=2, batch_size=10)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **kw: _FakeAsyncClient(
            _FakeResponse([[1.0, 0.0]]), captured=captured, **kw
        ),
    )
    texts = [f"chunk-{i}" for i in range(14)]
    vectors = await provider.embed(texts)
    assert len(vectors) == 14
    assert [len(batch) for batch in captured] == [10, 4]
    assert captured[0] == texts[:10]
    assert captured[1] == texts[10:]
