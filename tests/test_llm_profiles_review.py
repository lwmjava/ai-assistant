"""Independent task/profile contracts; description must never execute a provider."""

from dataclasses import FrozenInstanceError

import httpx
import pytest

from app.core.config import settings
from app.llm import factory
from app.rag.embeddings import factory as embedding_factory
from app.rag.embeddings.base import EmbeddingProvider


@pytest.fixture
def profiles(monkeypatch):
    factory.set_llm_provider_override(None)
    values = {
        "LLM_PROVIDER": "openai", "LLM_BASE_URL": "https://chat.review.invalid/v1",
        "LLM_API_KEY": "review-chat-credential", "LLM_DEFAULT_MODEL": "review-chat",
        "LLM_INTENT_PROVIDER": "openai", "LLM_INTENT_BASE_URL": "https://intent.review.invalid/v1",
        "LLM_INTENT_API_KEY": "review-intent-credential", "LLM_INTENT_MODEL": "review-intent",
        "LLM_FALLBACK_PROVIDER": "openai", "LLM_FALLBACK_BASE_URL": "https://fallback.review.invalid/v1",
        "LLM_FALLBACK_API_KEY": "review-fallback-credential", "LLM_FALLBACK_MODEL": "review-fallback",
        "LLM_CHAT_FALLBACK_CHAIN": "chat,fallback", "LLM_INTENT_FALLBACK_CHAIN": "intent,chat,fallback",
        "LLM_RERANK_PROVIDER": "", "LLM_RERANK_BASE_URL": "", "LLM_RERANK_API_KEY": "",
        "LLM_RERANK_MODEL": "", "EMBEDDING_PROVIDER": "openai",
        "EMBEDDING_BASE_URL": "https://embedding.review.invalid/v1", "EMBEDDING_MODEL": "review-embedding",
        "EMBEDDING_API_KEY": "review-embedding-credential", "EMBEDDING_DIM": 73,
        "EMBEDDING_TIMEOUT_SECONDS": 12.0,
    }
    for name, value in values.items():
        monkeypatch.setattr(settings, name, value)
    yield values
    factory.set_llm_provider_override(None)


def test_four_profiles_are_independent_immutable_snapshots_without_secret_repr(profiles):
    resolved = {task: factory.get_task_profile(task) for task in ("chat", "intent", "embedding", "rerank")}
    assert resolved["chat"].model == "review-chat"
    assert resolved["intent"].model == "review-intent"
    assert resolved["chat"].base_url != resolved["intent"].base_url
    assert resolved["embedding"].dim == 73
    assert resolved["embedding"].timeout == 12.0
    assert resolved["rerank"].enabled is False
    for task, profile in resolved.items():
        assert profile.task == task
        with pytest.raises(FrozenInstanceError):
            profile.model = "mutated"
        for value in profiles.values():
            if isinstance(value, str) and "credential" in value:
                assert value not in repr(profile)


def test_embedding_description_agrees_with_existing_real_embedding_factory_without_execution(monkeypatch, profiles):
    monkeypatch.setattr(embedding_factory, "_override", None)
    profile = factory.get_task_profile("embedding")
    provider = embedding_factory.get_embedding_provider()
    assert isinstance(provider, EmbeddingProvider)
    assert provider.model == profile.model
    assert provider.dim == profile.dim
    assert provider.base_url == profile.base_url
    assert provider.api_key == profile.api_key
    assert provider.timeout == profile.timeout

    def no_generation(*args, **kwargs):
        pytest.fail("embedding/rerank description must not construct a generation client")

    monkeypatch.setattr(factory, "OpenAICompatibleProvider", no_generation)
    assert factory.get_task_profile("embedding").model == profile.model
    assert factory.get_task_profile("rerank").enabled is False
    for task in ("embedding", "rerank"):
        with pytest.raises(ValueError):
            factory.get_llm_provider(task)


def test_configured_rerank_description_never_calls_chat_embedding_or_http(monkeypatch, profiles):
    monkeypatch.setattr(settings, "LLM_RERANK_PROVIDER", "review-provider")
    monkeypatch.setattr(settings, "LLM_RERANK_BASE_URL", "https://rerank.review.invalid/v1")
    monkeypatch.setattr(settings, "LLM_RERANK_MODEL", "review-rerank")
    monkeypatch.setattr(settings, "LLM_RERANK_API_KEY", "review-rerank-credential")

    def forbidden(*args, **kwargs):
        pytest.fail("config registration must have no inference or retrieval side effect")

    monkeypatch.setattr(factory, "OpenAICompatibleProvider", forbidden)
    monkeypatch.setattr(embedding_factory, "get_embedding_provider", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "post", forbidden)
    profile = factory.get_task_profile("rerank")
    assert profile.model == "review-rerank"
    assert profile.api_key == "review-rerank-credential"
    assert "review-rerank-credential" not in repr(profile)
    with pytest.raises(ValueError):
        factory.get_llm_provider("rerank")


@pytest.mark.parametrize("task", ["", "unknown", "fallback", "chatting", "embedding/completions"])
def test_unknown_task_is_rejected_by_both_interfaces(profiles, task):
    with pytest.raises(ValueError):
        factory.get_task_profile(task)
    with pytest.raises(ValueError):
        factory.get_llm_provider(task)


@pytest.mark.parametrize("chain", ["chat,typo,fallback", "embedding,chat", "rerank", "other,chat"])
def test_unknown_or_incompatible_generation_profile_is_not_silently_skipped(monkeypatch, profiles, chain):
    monkeypatch.setattr(settings, "LLM_CHAT_FALLBACK_CHAIN", chain)
    with pytest.raises(ValueError):
        factory.get_llm_provider("chat")


def test_profile_snapshot_and_credential_rotation_are_explicit(monkeypatch, profiles):
    old = factory.get_task_profile("embedding")
    monkeypatch.setattr(settings, "EMBEDDING_API_KEY", "review-rotated-credential")
    monkeypatch.setattr(settings, "EMBEDDING_MODEL", "review-next-embedding")
    current = factory.get_task_profile("embedding")
    assert old.api_key == "review-embedding-credential"
    assert current.api_key == "review-rotated-credential"
    assert old.model == "review-embedding"
    assert current.model == "review-next-embedding"
    assert "review-rotated-credential" not in repr(current)
