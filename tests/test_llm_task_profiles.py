"""四类任务配置保持接口隔离，不将嵌入或重排伪装成生成调用。"""

import secrets

import pytest

from app.core.config import Settings, settings
from app.llm import factory


def test_rerank_has_independent_configuration_fields():
    assert {"LLM_RERANK_PROVIDER", "LLM_RERANK_BASE_URL", "LLM_RERANK_MODEL", "LLM_RERANK_API_KEY"}.issubset(
        Settings.model_fields
    )


def test_four_task_profiles_map_embedding_without_secret_repr(monkeypatch):
    resolve = getattr(factory, "get_task_profile", None)
    assert callable(resolve), "四用途应具有真实配置解析入口"
    secret = secrets.token_urlsafe(24)
    monkeypatch.setattr(settings, "EMBEDDING_MODEL", "embedding-profile-model")
    monkeypatch.setattr(settings, "EMBEDDING_API_KEY", secret)
    monkeypatch.setattr(settings, "EMBEDDING_DIM", 321)
    profiles = {task: resolve(task) for task in ("chat", "intent", "embedding", "rerank")}
    assert profiles["embedding"].model == "embedding-profile-model"
    assert profiles["embedding"].dim == 321
    assert profiles["embedding"].api_key == secret
    assert secret not in repr(profiles["embedding"])
    with pytest.raises(ValueError):
        resolve("unknown-purpose")
    for task in ("embedding", "rerank"):
        with pytest.raises(ValueError):
            factory.get_llm_provider(task)


def test_unknown_fallback_profile_is_not_silently_skipped(monkeypatch):
    monkeypatch.setattr(settings, "LLM_CHAT_FALLBACK_CHAIN", "mistyped-profile,fallback")
    with pytest.raises(ValueError, match="未知配置档"):
        factory.get_llm_provider("chat")


@pytest.mark.parametrize("name,value", [
    ("LLM_CIRCUIT_FAILURE_THRESHOLD", 0),
    ("LLM_CIRCUIT_FAILURE_THRESHOLD", True),
    ("LLM_CIRCUIT_COOLDOWN_SECONDS", float("inf")),
    ("LLM_CIRCUIT_COOLDOWN_SECONDS", False),
])
def test_invalid_circuit_configuration_is_rejected(name, value):
    with pytest.raises(ValueError):
        Settings(_env_file=None, **{name: value})
