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
    """健康检查返回 ok 状态。"""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == "0.1.0"


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
