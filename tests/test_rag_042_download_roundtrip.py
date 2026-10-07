"""RAG-042：源文件下载的端到端往返证据。

验收条款「下载原始文件成功」的自动化部分：上传一份真实源文件 → 走受控下载端点
`GET /api/rag/documents/{id}/download` → 断言 200、`Content-Disposition` 里的
文件名与上传的文件名一致、下载到的字节与上传字节**完全相同**。

另补两条服务端证据，用来支撑前端那两条文案：
- 同租户他人上传的文档：控制面不放行 → 404（「现有管理权限不变」）；
- 源文件被清理 → 404「源文件不存在」（对应前端的中文提示）。

本文件只读后端既有行为，不修改任何 `app/` 代码。

运行说明（本机当前共享测试库有 schema 漂移，见实现说明 §5.3）：
    DATABASE_URL="sqlite:///./data/test_rag042.db" python -m pytest \
        tests/test_rag_042_download_roundtrip.py -q --basetemp=data/pytest-tmp/rag042

注意：上传内容每次都必须唯一。后端对重复内容会命中已有文档并丢弃新落盘的源文件，
复用固定内容会让第二次运行拿到上一轮的 storage_path，从而 404。
"""

from __future__ import annotations

import io
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.security import Role
from app.main import app
from app.models.user import User

_FILENAME = "refund-policy.txt"


def _raw() -> bytes:
    """每次运行都不同的上传内容，避免命中后端的内容去重。"""
    body = (
        "退款政策：签收后七日内可申请无理由退款。\n"
        f"第二行（唯一标记 {uuid.uuid4().hex}）：配件与包装需完整。\n"
    )
    return body.encode("utf-8")


def _user(user_id: str, role: Role) -> User:
    return User(
        id=user_id,
        tenant_id="rag042-tenant",
        username=user_id,
        hashed_password="",
        role=role.value,
        token_version=0,
        is_active=True,
    )


@pytest.fixture()
def client():
    """以租户管理员身份访问（具备 knowledge_bases 读 + 控制面权限）。"""
    app.dependency_overrides[get_current_user] = lambda: _user("rag042-admin", Role.TENANT_ADMIN)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _patch_storage_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """把源文件落盘/解析的根目录都重定向到临时目录，避免污染仓库 data/knowledge。"""
    import app.rag.document_storage as storage_mod

    monkeypatch.setattr(storage_mod, "_PROJECT_ROOT", tmp_path)


def _upload(client: TestClient, raw: bytes) -> dict:
    resp = client.post(
        "/api/rag/documents/upload",
        files={"file": (_FILENAME, io.BytesIO(raw), "text/plain")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_download_returns_uploaded_bytes_and_filename(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """上传的每一个字节都要原样回来，文件名取自后端保存的 source。"""
    _patch_storage_root(monkeypatch, tmp_path)
    raw = _raw()
    doc = _upload(client, raw)
    assert doc["source"] == _FILENAME, "后端应把上传文件名记为 source"
    assert doc["chunk_count"] >= 1, "上传应至少产出一个分块"

    resp = client.get(f"/api/rag/documents/{doc['id']}/download")

    assert resp.status_code == 200, resp.text
    assert resp.content == raw, "下载到的字节与上传字节不一致"
    disposition = resp.headers.get("content-disposition", "")
    assert _FILENAME in disposition, f"Content-Disposition 未带上原文件名：{disposition}"


def test_other_member_cannot_download_others_source_file(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """同租户的他人文档：控制面不放行，表现为 404（不泄漏文档是否存在）。"""
    _patch_storage_root(monkeypatch, tmp_path)
    doc = _upload(client, _raw())

    app.dependency_overrides[get_current_user] = lambda: _user("rag042-member", Role.MEMBER)
    try:
        resp = client.get(f"/api/rag/documents/{doc['id']}/download")
    finally:
        app.dependency_overrides[get_current_user] = lambda: _user(
            "rag042-admin", Role.TENANT_ADMIN
        )

    assert resp.status_code == 404, resp.text
    detail = resp.json()["detail"]
    assert "无权" in detail or "不存在" in detail


def test_missing_source_file_returns_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """源文件被清理后下载应明确报 404（对应前端「源文件不存在」文案）。"""
    _patch_storage_root(monkeypatch, tmp_path)
    doc = _upload(client, _raw())
    stored = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert stored, "上传后应有源文件落盘"
    for path in stored:
        path.unlink()

    resp = client.get(f"/api/rag/documents/{doc['id']}/download")

    assert resp.status_code == 404
    assert "源文件不存在" in resp.json()["detail"]
