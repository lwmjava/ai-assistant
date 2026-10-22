"""应用冒烟测试。"""

import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.rag.embeddings.base import EmbeddingProvider
from app.rag.embeddings.factory import (
    embedding_availability,
    get_embedding_provider,
    set_embedding_override,
)
from app.rag.embeddings.mock import MockEmbeddingProvider
from app.rag.embeddings.openai_compatible import OpenAICompatibleEmbeddingProvider

client = TestClient(app)

_OFFICIAL_EVAL_REPORTS = (
    Path("evals/reports/rag-v0.1-baseline-20260919.json"),
    Path("evals/reports/rag-v0.1-baseline-20261010.json"),
)


class _UnknownEmbedding(EmbeddingProvider):
    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise AssertionError("health 不得调用 embed")


def test_root_ok() -> None:
    """根路径返回服务基本信息。"""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["app"] == "ai-assistant"
    assert "docs" in body


def test_health_ok() -> None:
    """数据库与本地向量库都可用时，健康检查返回连通结果。"""
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == "0.1.0"
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["vector_store"]["status"] == "ok"
    assert body["checks"]["vector_store"]["backend"] == "local"
    assert body["checks"]["llm"]["mode"] in {"real", "mock", "unavailable"}


def test_health_llm_mode_does_not_change_status(monkeypatch) -> None:
    """模型未就绪只是一个字段，不把健康检查打成失败。"""
    monkeypatch.setattr("app.api.routes.health.llm_availability", lambda: "mock")
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json()["checks"]["llm"]["mode"] == "mock"


@pytest.fixture
def _reset_embedding_override():
    set_embedding_override(None)
    yield
    set_embedding_override(None)


def test_health_reports_mock_embedding_when_dev_missing_key(
    monkeypatch, _reset_embedding_override
) -> None:
    """开发环境缺密钥时展示 Mock 身份，并标明不能当质量证据。"""
    monkeypatch.setattr(settings, "ENV", "development")
    monkeypatch.setattr(settings, "EMBEDDING_API_KEY", "")
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "openai")
    monkeypatch.setattr(settings, "EMBEDDING_MODEL", "text-embedding-v3")
    monkeypatch.setattr(settings, "EMBEDDING_DIM", 1024)
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    embedding = resp.json()["checks"]["embedding"]
    assert embedding["mode"] == "mock"
    assert embedding["provider"] == "mock"
    assert embedding["model"] == "mock-embedding"
    assert embedding["dim"] == 1024
    assert embedding["is_mock"] is True
    assert embedding["connectivity_verified"] is False
    assert embedding["quality_evidence"] is False


def test_health_embedding_does_not_change_status(
    monkeypatch, _reset_embedding_override
) -> None:
    """Embedding 只读展示不改变数据库/向量库连通性判定。"""
    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr(settings, "EMBEDDING_API_KEY", "")
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    embedding = body["checks"]["embedding"]
    assert embedding["mode"] == "unavailable"
    assert embedding["is_mock"] is False
    assert embedding["quality_evidence"] is False


def test_health_embedding_configured_without_constructing_provider(
    monkeypatch, _reset_embedding_override
) -> None:
    """有密钥时报告配置身份，不构造真实提供商、不联网。"""

    def _boom(*_a, **_k):
        raise AssertionError("不得构造真实嵌入提供商")

    monkeypatch.setattr(settings, "ENV", "development")
    monkeypatch.setattr(settings, "EMBEDDING_API_KEY", "synthetic-embedding-key")
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "openai")
    monkeypatch.setattr(settings, "EMBEDDING_MODEL", "text-embedding-v3")
    monkeypatch.setattr(settings, "EMBEDDING_DIM", 1024)
    monkeypatch.setattr(settings, "EMBEDDING_BASE_URL", "https://embed.example.test/v1")
    monkeypatch.setattr(OpenAICompatibleEmbeddingProvider, "__init__", _boom)
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    embedding = body["checks"]["embedding"]
    assert embedding["mode"] == "configured"
    assert embedding["provider"] == "openai"
    assert embedding["model"] == "text-embedding-v3"
    assert embedding["dim"] == 1024
    assert embedding["is_mock"] is False
    assert embedding["connectivity_verified"] is False
    assert embedding["quality_evidence"] is False
    dumped = resp.text
    assert "synthetic-embedding-key" not in dumped
    assert "embed.example.test" not in dumped


def test_health_embedding_reports_override_mock_dim(
    monkeypatch, _reset_embedding_override
) -> None:
    """注入的 Mock 报告实际维度，不拿配置维度冒充。"""
    monkeypatch.setattr(settings, "EMBEDDING_MODEL", "text-embedding-v3")
    monkeypatch.setattr(settings, "EMBEDDING_DIM", 1024)
    set_embedding_override(MockEmbeddingProvider(dim=8))
    embedding = embedding_availability()
    assert embedding["mode"] == "override"
    assert embedding["provider"] == "mock"
    assert embedding["model"] == "mock-embedding"
    assert embedding["dim"] == 8
    assert embedding["is_mock"] is True
    assert embedding["quality_evidence"] is False


def test_health_embedding_unknown_override_does_not_use_config_model(
    _reset_embedding_override,
) -> None:
    """未知注入提供商不得把配置模型名写成实际身份。"""
    set_embedding_override(_UnknownEmbedding())
    embedding = embedding_availability()
    assert embedding["mode"] == "override"
    assert embedding["provider"] == "unknown"
    assert embedding["model"] == "unknown"
    assert embedding["model"] != settings.EMBEDDING_MODEL
    assert embedding["is_mock"] is False
    assert embedding["quality_evidence"] is False


def test_health_does_not_relax_production_embedding_key_requirement(
    monkeypatch, _reset_embedding_override
) -> None:
    """生产缺密钥时工厂仍拒绝构造，health 只报告 unavailable。"""
    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr(settings, "EMBEDDING_API_KEY", "")
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "openai")
    with pytest.raises(RuntimeError, match="EMBEDDING_API_KEY"):
        get_embedding_provider()
    embedding = embedding_availability()
    assert embedding["mode"] == "unavailable"
    assert embedding["is_mock"] is False


def test_health_official_eval_reports_record_embedding_identity() -> None:
    """冻结官方检索报告必须能追溯 Embedding 提供商、模型与维度。"""
    for path in _OFFICIAL_EVAL_REPORTS:
        payload = json.loads(path.read_text(encoding="utf-8"))
        run_config = payload["run_config"]
        assert run_config["embedding_provider"]
        assert "Mock" not in run_config["embedding_provider"]
        assert run_config["embedding_model"]
        assert int(run_config["embedding_dim"]) > 0


def test_health_fails_when_database_unreachable(monkeypatch) -> None:
    """数据库不可达时，健康检查整体不是 ok。"""

    class _Closed:
        def __enter__(self):
            raise ConnectionError("unreachable")

        def __exit__(self, *_args: object) -> bool:
            return False

    monkeypatch.setattr("app.api.routes.health.Session", lambda *_a, **_k: _Closed())
    resp = client.get("/api/v1/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] != "ok"
    assert body["checks"]["database"]["status"] == "error"
    assert "unreachable" not in resp.text


def test_health_fails_when_vector_store_unreachable(monkeypatch) -> None:
    """当前向量库不可达时，健康检查整体不是 ok，数据库结果仍可单独成功。"""
    monkeypatch.setattr(
        "app.api.routes.health.settings.RAG_VECTOR_STORE",
        "milvus",
    )

    def _down() -> None:
        raise ConnectionError("milvus down")

    monkeypatch.setattr("app.api.routes.health._probe_milvus", _down)
    resp = client.get("/api/v1/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] != "ok"
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["vector_store"]["status"] == "error"
    assert body["checks"]["vector_store"]["backend"] == "milvus"
    assert "milvus down" not in resp.text


def test_auth_me_requires_token() -> None:
    """未携带令牌访问受保护接口应返回 401。"""
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 401


def test_login_invalid_credentials() -> None:
    """使用错误凭据登录应返回 401。"""
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "not_exist", "password": "wrong"},
    )
    assert resp.status_code == 401
