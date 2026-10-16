"""业务 API 只暴露版本化入口，前端回退保留 API 的 JSON 404。"""

import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app


def test_openapi_contains_only_versioned_business_paths():
    paths = app.openapi()["paths"]
    assert paths
    assert all(path.startswith("/api/v1/") for path in paths)
    assert "/" not in paths
    assert "/api/v1/health" in paths


def test_versioned_health_succeeds_and_legacy_health_has_no_alias():
    client = TestClient(app)
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    legacy = client.get("/api/health", follow_redirects=False)
    assert legacy.status_code == 404
    assert legacy.headers["content-type"].startswith("application/json")


def test_spa_does_not_intercept_bare_or_unknown_api_paths(tmp_path, monkeypatch):
    # 将同一入口源码放在隔离树中，仅改变静态文件所在位置，不启动应用生命周期。
    root = Path(__file__).resolve().parents[1]
    module_path = tmp_path / "app" / "main.py"
    module_path.parent.mkdir()
    module_path.write_bytes((root / "app" / "main.py").read_bytes())
    frontend = tmp_path / "frontend" / "dist"
    (frontend / "assets").mkdir(parents=True)
    (frontend / "index.html").write_text("<html>isolated frontend</html>", encoding="utf-8")
    monkeypatch.setattr(settings, "SERVE_FRONTEND", True)
    spec = importlib.util.spec_from_file_location("isolated_versioned_main", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    client = TestClient(module.app)
    assert client.get("/chat").status_code == 200
    for path in ("/api", "/api/health", "/api/unknown", "/api/v1/unknown"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")
