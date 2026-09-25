"""应用冒烟测试。"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root_ok() -> None:
    """根路径返回服务基本信息。"""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["app"] == "ai-assistant"
    assert "docs" in body


def test_health_ok() -> None:
    """数据库与本地向量库都可用时，健康检查返回连通结果。"""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == "0.1.0"
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["vector_store"]["status"] == "ok"
    assert body["checks"]["vector_store"]["backend"] == "local"


def test_health_fails_when_database_unreachable(monkeypatch) -> None:
    """数据库不可达时，健康检查整体不是 ok。"""

    class _Closed:
        def __enter__(self):
            raise ConnectionError("unreachable")

        def __exit__(self, *_args: object) -> bool:
            return False

    monkeypatch.setattr("app.api.routes.health.Session", lambda *_a, **_k: _Closed())
    resp = client.get("/api/health")
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
    resp = client.get("/api/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] != "ok"
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["vector_store"]["status"] == "error"
    assert body["checks"]["vector_store"]["backend"] == "milvus"
    assert "milvus down" not in resp.text


def test_auth_me_requires_token() -> None:
    """未携带令牌访问受保护接口应返回 401。"""
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_login_invalid_credentials() -> None:
    """使用错误凭据登录应返回 401。"""
    resp = client.post(
        "/api/auth/login",
        json={"username": "not_exist", "password": "wrong"},
    )
    assert resp.status_code == 401
