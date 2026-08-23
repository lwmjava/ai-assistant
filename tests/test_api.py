"""Swagger / API 端点综合测试。

覆盖 FastAPI 自动生成的 OpenAPI 文档中所有已实现端点：
- GET  /                根路径
- GET  /api/health       健康检查
- POST /api/auth/login   登录
- POST /api/auth/refresh 刷新令牌
- GET  /api/auth/me      当前用户信息
- GET  /docs             Swagger UI
- GET  /openapi.json     OpenAPI 规范

测试场景包括：正常流程、认证失败、令牌过期/轮转、边界值等。
"""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import settings
from app.core.database import engine
from app.core.security import hash_password, Role
from app.main import app
from app.models.user import Tenant, User

# ── 模块级常量 ──────────────────────────────────────────
TEST_ADMIN_USERNAME = "swagger_test_admin"
TEST_ADMIN_PASSWORD = "Test@Pass123!"
TEST_ADMIN_EMAIL = "admin@test.local"


# ── Fixtures ────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_db() -> None:
    """每个测试前清空数据库，保证测试隔离。"""
    from sqlmodel import SQLModel

    from app.models import user  # noqa: F401  # 注册表元数据

    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)


@pytest.fixture
def client() -> TestClient:
    """返回 FastAPI TestClient。"""
    return TestClient(app)


@pytest.fixture
def admin_user() -> User:
    """在测试库中创建 system_admin 用户并返回。"""
    with Session(engine) as session:
        tenant = Tenant(name="test_tenant")
        session.add(tenant)
        session.commit()
        session.refresh(tenant)

        admin = User(
            tenant_id=tenant.id,
            username=TEST_ADMIN_USERNAME,
            email=TEST_ADMIN_EMAIL,
            hashed_password=hash_password(TEST_ADMIN_PASSWORD),
            role=Role.SYSTEM_ADMIN.value,
        )
        session.add(admin)
        session.commit()
        session.refresh(admin)
        return admin


@pytest.fixture
def auth_headers(client: TestClient, admin_user: User) -> dict[str, str]:
    """登录并返回携带 access_token 的 Authorization 头。"""
    resp = client.post(
        "/api/auth/login",
        json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD},
    )
    assert resp.status_code == 200, f"登录失败: {resp.json()}"
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def tokens(client: TestClient, admin_user: User) -> dict[str, str]:
    """登录并返回 access_token + refresh_token。"""
    resp = client.post(
        "/api/auth/login",
        json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD},
    )
    assert resp.status_code == 200
    return resp.json()


# ── 系统端点 ────────────────────────────────────────────


class TestRootEndpoint:
    """GET / — 根路径。"""

    def test_root_returns_app_info(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["app"] == settings.APP_NAME
        assert body["version"] == settings.APP_VERSION
        assert body["docs"] == "/docs"
        assert body["health"] == "/api/health"

    def test_root_content_type_is_json(self, client: TestClient) -> None:
        resp = client.get("/")
        assert "application/json" in resp.headers["content-type"]


# ── 健康检查 ────────────────────────────────────────────


class TestHealthEndpoint:
    """GET /api/health — 健康检查。"""

    def test_health_returns_ok(self, client: TestClient) -> None:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["app"] == settings.APP_NAME
        assert body["version"] == settings.APP_VERSION
        assert body["env"] == settings.ENV

    def test_health_returns_405_on_head(self, client: TestClient) -> None:
        """GET 路由默认不支持 HEAD 方法，返回 405。"""
        resp = client.head("/api/health")
        assert resp.status_code == 405


# ── 认证：登录 ──────────────────────────────────────────


class TestLogin:
    """POST /api/auth/login — 用户登录。"""

    def test_login_success_returns_tokens(self, client: TestClient, admin_user: User) -> None:
        resp = client.post(
            "/api/auth/login",
            json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert "refresh_token" in body
        assert body["token_type"] == "bearer"
        # 验证 token 是合法的 JWT 字符串（三段式 base64）
        for key in ("access_token", "refresh_token"):
            assert len(body[key].split(".")) == 3

    def test_login_invalid_username(self, client: TestClient) -> None:
        resp = client.post(
            "/api/auth/login",
            json={"username": "nonexistent_user", "password": TEST_ADMIN_PASSWORD},
        )
        assert resp.status_code == 401
        assert "用户名或密码错误" in resp.json()["detail"]

    def test_login_invalid_password(self, client: TestClient, admin_user: User) -> None:
        resp = client.post(
            "/api/auth/login",
            json={"username": TEST_ADMIN_USERNAME, "password": "WrongPassword!"},
        )
        assert resp.status_code == 401
        assert "用户名或密码错误" in resp.json()["detail"]

    def test_login_empty_username(self, client: TestClient) -> None:
        resp = client.post(
            "/api/auth/login",
            json={"username": "", "password": TEST_ADMIN_PASSWORD},
        )
        assert resp.status_code == 422  # Pydantic 校验失败

    def test_login_empty_password(self, client: TestClient) -> None:
        resp = client.post(
            "/api/auth/login",
            json={"username": TEST_ADMIN_USERNAME, "password": ""},
        )
        assert resp.status_code == 422

    def test_login_missing_fields(self, client: TestClient) -> None:
        resp = client.post("/api/auth/login", json={})
        assert resp.status_code == 422

    def test_login_wrong_content_type(self, client: TestClient) -> None:
        """表单提交而非 JSON 应返回 422。"""
        resp = client.post(
            "/api/auth/login",
            data={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD},
        )
        assert resp.status_code == 422


# ── 认证：当前用户 ──────────────────────────────────────


class TestMeEndpoint:
    """GET /api/auth/me — 当前用户信息。"""

    def test_me_returns_user_info(self, client: TestClient, auth_headers: dict) -> None:
        resp = client.get("/api/auth/me", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["username"] == TEST_ADMIN_USERNAME
        assert body["email"] == TEST_ADMIN_EMAIL
        assert body["role"] == Role.SYSTEM_ADMIN.value
        assert body["is_active"] is True
        assert "id" in body
        assert "tenant_id" in body
        # 确保不返回敏感字段
        assert "hashed_password" not in body
        assert "token_version" not in body

    def test_me_without_token_returns_401(self, client: TestClient) -> None:
        resp = client.get("/api/auth/me")
        assert resp.status_code == 401
        assert "未提供认证令牌" in resp.json()["detail"]

    def test_me_with_invalid_token_returns_401(self, client: TestClient) -> None:
        resp = client.get(
            "/api/auth/me",
            headers={"Authorization": "Bearer invalid.token.here"},
        )
        assert resp.status_code == 401

    def test_me_with_malformed_auth_header(self, client: TestClient) -> None:
        """非 Bearer 格式的 Authorization 头。"""
        resp = client.get(
            "/api/auth/me",
            headers={"Authorization": "Basic dGVzdDp0ZXN0"},
        )
        assert resp.status_code == 401

    def test_me_with_expired_token(self, client: TestClient) -> None:
        """使用一个故意构造的已过期 JWT 测试。"""
        # 已过期的 token（exp 在过去）
        expired_token = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiJ0ZXN0IiwidHlwZSI6ImFjY2VzcyIsImV4cCI6MTcwMDAwMDAwMH0."
            "fake_signature"
        )
        resp = client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert resp.status_code == 401


# ── 认证：刷新令牌 ──────────────────────────────────────


class TestRefreshToken:
    """POST /api/auth/refresh — 刷新令牌。"""

    def test_refresh_returns_new_tokens(self, client: TestClient, tokens: dict) -> None:
        resp = client.post(
            "/api/auth/refresh",
            json={"refresh_token": tokens["refresh_token"]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["token_type"] == "bearer"
        # access_token 必定不同（iat 时间戳不同）
        assert body["access_token"] != tokens["access_token"]
        # refresh_token 可能相同（同秒内生成且 token_version 未变时 payload 一致）
        # 但 access_token 一定轮转了，这是关键安全属性

    def test_refresh_with_access_token_fails(self, client: TestClient, tokens: dict) -> None:
        """用 access_token 冒充 refresh_token 应被拒绝。"""
        resp = client.post(
            "/api/auth/refresh",
            json={"refresh_token": tokens["access_token"]},
        )
        assert resp.status_code == 401

    def test_refresh_with_invalid_token(self, client: TestClient) -> None:
        resp = client.post(
            "/api/auth/refresh",
            json={"refresh_token": "invalid_token_string"},
        )
        assert resp.status_code == 401

    def test_refresh_missing_body(self, client: TestClient) -> None:
        resp = client.post("/api/auth/refresh", json={})
        assert resp.status_code == 422

    def test_refresh_token_rotation_chain(self, client: TestClient, tokens: dict) -> None:
        """连续刷新两次，验证 access_token 轮转正常工作。

        注意：当前实现中 refresh_token 不自动轮转（token_version 仅在
        登出/改密时递增），因此旧 refresh_token 仍然有效。
        """
        # 第一次刷新
        resp1 = client.post(
            "/api/auth/refresh",
            json={"refresh_token": tokens["refresh_token"]},
        )
        assert resp1.status_code == 200
        new_tokens = resp1.json()

        # access_token 已轮转
        assert new_tokens["access_token"] != tokens["access_token"]

        # 用新 access_token 可访问受保护资源
        me_resp = client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {new_tokens['access_token']}"},
        )
        assert me_resp.status_code == 200

        # 第二次刷新：用 refresh_token 再次刷新仍成功（token_version 未变）
        resp2 = client.post(
            "/api/auth/refresh",
            json={"refresh_token": new_tokens["refresh_token"]},
        )
        assert resp2.status_code == 200
        assert resp2.json()["access_token"] != new_tokens["access_token"]


# ── OpenAPI / Swagger 文档端点 ──────────────────────────


class TestOpenAPIDocs:
    """GET /docs, /redoc, /openapi.json — API 文档端点。"""

    def test_swagger_ui_accessible(self, client: TestClient) -> None:
        resp = client.get("/docs")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_redoc_accessible(self, client: TestClient) -> None:
        resp = client.get("/redoc")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_openapi_json_valid(self, client: TestClient) -> None:
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        body = resp.json()
        assert body["openapi"].startswith("3.")
        assert body["info"]["title"] == settings.APP_NAME
        assert body["info"]["version"] == settings.APP_VERSION
        # 验证所有路由均已注册
        paths = body["paths"]
        assert "/" in paths
        assert "/api/health" in paths
        assert "/api/auth/login" in paths
        assert "/api/auth/refresh" in paths
        assert "/api/auth/me" in paths

    def test_openapi_json_has_security_scheme(self, client: TestClient) -> None:
        """OpenAPI 规范应包含 Bearer 认证方案。"""
        resp = client.get("/openapi.json")
        body = resp.json()
        # FastAPI 的 HTTPBearer 会自动注册 security scheme
        components = body.get("components", {})
        security_schemes = components.get("securitySchemes", {})
        assert "HTTPBearer" in security_schemes


# ── 跨端点集成测试 ──────────────────────────────────────


class TestIntegrationFlow:
    """完整用户流程：登录 → 查看信息 → 刷新 → 查看信息。"""

    def test_full_auth_flow(self, client: TestClient, admin_user: User) -> None:
        # 1. 登录
        login_resp = client.post(
            "/api/auth/login",
            json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD},
        )
        assert login_resp.status_code == 200
        tokens = login_resp.json()
        access = tokens["access_token"]
        refresh = tokens["refresh_token"]

        # 2. 查看当前用户
        me_resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {access}"})
        assert me_resp.status_code == 200
        assert me_resp.json()["username"] == TEST_ADMIN_USERNAME

        # 3. 刷新令牌
        refresh_resp = client.post("/api/auth/refresh", json={"refresh_token": refresh})
        assert refresh_resp.status_code == 200
        new_access = refresh_resp.json()["access_token"]

        # 4. 用新令牌查看用户
        me_resp2 = client.get("/api/auth/me", headers={"Authorization": f"Bearer {new_access}"})
        assert me_resp2.status_code == 200
        assert me_resp2.json()["username"] == TEST_ADMIN_USERNAME

        # 5. 旧 access_token 仍然有效（未过期）
        me_resp3 = client.get("/api/auth/me", headers={"Authorization": f"Bearer {access}"})
        assert me_resp3.status_code == 200


# ── 边界与异常场景 ──────────────────────────────────────


class TestEdgeCases:
    """边界值与异常场景测试。"""

    def test_login_username_at_max_length(self, client: TestClient, admin_user: User) -> None:
        """64 字符用户名登录（边界值）。"""
        # 64 字符在 max_length 范围内，应正常校验
        long_username = "a" * 64
        resp = client.post(
            "/api/auth/login",
            json={"username": long_username, "password": TEST_ADMIN_PASSWORD},
        )
        # 用户不存在，返回 401（而非 422）
        assert resp.status_code == 401

    def test_login_password_at_max_length(self, client: TestClient, admin_user: User) -> None:
        """72 字符密码登录（bcrypt 上限边界值）。"""
        long_password = "P" * 72
        resp = client.post(
            "/api/auth/login",
            json={"username": TEST_ADMIN_USERNAME, "password": long_password},
        )
        assert resp.status_code == 401

    def test_health_returns_consistent_response(self, client: TestClient) -> None:
        """多次请求健康检查返回一致。"""
        resp1 = client.get("/api/health")
        resp2 = client.get("/api/health")
        assert resp1.json() == resp2.json()

    def test_root_returns_consistent_response(self, client: TestClient) -> None:
        """多次请求根路径返回一致。"""
        resp1 = client.get("/")
        resp2 = client.get("/")
        assert resp1.json() == resp2.json()