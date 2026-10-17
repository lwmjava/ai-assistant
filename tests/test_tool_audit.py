"""PAGT-002 审计绑定与 file_ops 写路径 L2 门控。"""

import asyncio
import json

import pytest
from sqlmodel import Session, delete, select

from app.agents.tools.base import ToolCall, ToolRegistry
from app.agents.tools.builtin import default_tools
from app.audit import AuditLog, reset_audit_logger
from app.core import log_context
from app.core.config import get_settings
from app.core.database import engine
from app.core.security import Role
from app.models.user import Tenant, User


@pytest.fixture(autouse=True)
def created(monkeypatch):
    log_context.clear()
    reset_audit_logger()
    settings = get_settings()
    monkeypatch.setattr(settings, "FILE_OPS_ENABLED", False, raising=False)
    monkeypatch.setattr(settings, "FILE_OPS_ALLOWED_ROOTS", "{}", raising=False)
    monkeypatch.delenv("FILE_OPS_WRITE_ENABLED", raising=False)
    # 主动清理上一轮次残留的 user/tenant/tool_call 审计，避免跨轮次污染
    with Session(engine) as session:
        session.exec(delete(User).where(User.username.like("pagt002-%")))
        session.exec(delete(Tenant).where(Tenant.name.like("pagt002-%")))
        session.exec(delete(AuditLog).where(AuditLog.action == "tool_call"))
        session.commit()
    live: list[User] = []
    yield live
    log_context.clear()
    reset_audit_logger()
    with Session(engine) as session:
        for row in live:
            user = session.get(User, row.id)
            if user:
                session.delete(user)
            tenant = session.get(Tenant, row.tenant_id)
            if tenant:
                session.delete(tenant)
        session.commit()


def _audit_rows() -> list[AuditLog]:
    with Session(engine) as session:
        return list(session.exec(select(AuditLog).where(AuditLog.action == "tool_call")))


def _cleanup(rows: list[AuditLog]) -> None:
    with Session(engine) as session:
        for row in rows:
            got = session.get(AuditLog, row.id)
            if got:
                session.delete(got)
        session.commit()


def _make_user(role: Role, username: str, created: list[User] | None = None) -> User:
    with Session(engine) as session:
        tenant = Tenant(name=f"pagt002-{username}")
        session.add(tenant)
        session.commit()
        session.refresh(tenant)
        user = User(
            tenant_id=tenant.id,
            username=username,
            hashed_password="x",
            role=role.value,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        if created is not None:
            created.append(user)
        return user


def _file_root(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "FILE_OPS_ENABLED", True, raising=False)
    monkeypatch.setattr(
        get_settings(), "FILE_OPS_ALLOWED_ROOTS", json.dumps({"public": str(tmp_path)}), raising=False
    )


def test_tenant_bound_audit_record_isolation(monkeypatch, created) -> None:
    u1 = _make_user(Role.MEMBER, "pagt002-tenant-a", created)
    u2 = _make_user(Role.MEMBER, "pagt002-tenant-b", created)
    registry = ToolRegistry(default_tools())

    log_context.set_user(u1.id, u1.tenant_id)
    asyncio.run(registry.run(ToolCall(name="calculator", arguments={"expression": "1+1"})))
    log_context.set_user(u2.id, u2.tenant_id)
    asyncio.run(registry.run(ToolCall(name="calculator", arguments={"expression": "2+2"})))

    rows = [r for r in _audit_rows() if r.resource_id == "calculator"]
    tenants = {r.tenant_id for r in rows}
    assert tenants == {u1.tenant_id, u2.tenant_id}
    users = {r.user_id for r in rows}
    assert users == {u1.id, u2.id}
    _cleanup(_audit_rows())


def test_write_tool_not_registered_when_switch_off(monkeypatch, tmp_path) -> None:
    _file_root(monkeypatch, tmp_path)
    # 未设置 FILE_OPS_WRITE_ENABLED → 写工具不注册
    names = {t.name for t in default_tools()}
    assert "file_ops_write" not in names


def test_write_denied_for_anonymous_even_when_switch_on(monkeypatch, tmp_path) -> None:
    _file_root(monkeypatch, tmp_path)
    monkeypatch.setenv("FILE_OPS_WRITE_ENABLED", "true")
    from app.agents.tools import file_ops as file_module

    tool = file_module.file_ops_write_tool()
    assert tool is not None
    # ContextVar 空（匿名/dev 模式）→ 注册表门控保守拒绝
    registry = ToolRegistry([tool])
    args = {"root": "public", "path": "note.txt", "content": "hi\n"}
    out = asyncio.run(registry.run(ToolCall(name="file_ops_write", arguments=args)))
    assert "拒绝" in out
    assert not (tmp_path / "note.txt").exists()
    rows = [r for r in _audit_rows() if r.resource_id == "file_ops_write"]
    assert rows and "allowed" in (rows[0].details or "")
    _cleanup(_audit_rows())


def test_write_denied_for_member_role(monkeypatch, tmp_path, created) -> None:
    _file_root(monkeypatch, tmp_path)
    monkeypatch.setenv("FILE_OPS_WRITE_ENABLED", "true")
    from app.agents.tools import file_ops as file_module

    member = _make_user(Role.MEMBER, "pagt002-wmember", created)
    log_context.set_user(member.id, member.tenant_id)
    tool = file_module.file_ops_write_tool()
    assert tool is not None
    registry = ToolRegistry([tool])
    args = {"root": "public", "path": "note.txt", "content": "hi\n"}
    out = asyncio.run(registry.run(ToolCall(name="file_ops_write", arguments=args)))
    assert "拒绝" in out
    assert not (tmp_path / "note.txt").exists()
    _cleanup(_audit_rows())


def test_write_allowed_for_admin_and_create_only_no_overwrite(monkeypatch, tmp_path, created) -> None:
    _file_root(monkeypatch, tmp_path)
    monkeypatch.setenv("FILE_OPS_WRITE_ENABLED", "true")
    from app.agents.tools import file_ops as file_module

    admin = _make_user(Role.TENANT_ADMIN, "pagt002-wadmin", created)
    log_context.set_user(admin.id, admin.tenant_id)
    tool = file_module.file_ops_write_tool()
    assert tool is not None
    registry = ToolRegistry([tool])
    args1 = {"root": "public", "path": "note.txt", "content": "first\n"}
    out1 = asyncio.run(registry.run(ToolCall(name="file_ops_write", arguments=args1)))
    assert '"status": "ok"' in out1, out1
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "first\n"
    # 第二次同路径写 → conflict，不覆盖
    args2 = {"root": "public", "path": "note.txt", "content": "second\n"}
    out2 = asyncio.run(registry.run(ToolCall(name="file_ops_write", arguments=args2)))
    assert "conflict" in out2
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "first\n"
    _cleanup(_audit_rows())


def test_write_invalid_path_rejected_no_file(monkeypatch, tmp_path, created) -> None:
    _file_root(monkeypatch, tmp_path)
    monkeypatch.setenv("FILE_OPS_WRITE_ENABLED", "true")
    from app.agents.tools import file_ops as file_module

    admin = _make_user(Role.TENANT_ADMIN, "pagt002-wpath", created)
    log_context.set_user(admin.id, admin.tenant_id)
    tool = file_module.file_ops_write_tool()
    registry = ToolRegistry([tool])
    args = {"root": "public", "path": "../escape.txt", "content": "x\n"}
    out = asyncio.run(registry.run(ToolCall(name="file_ops_write", arguments=args)))
    assert "error" in out
    assert not (tmp_path / "escape.txt").exists()
    _cleanup(_audit_rows())


def test_write_oversize_leaves_no_empty_shell_and_later_legal_write_succeeds(monkeypatch, tmp_path, created) -> None:
    """P2-1：超限写先校验长度不落盘，不留 0 字节空壳；同路径合法写仍可成功。"""
    _file_root(monkeypatch, tmp_path)
    monkeypatch.setenv("FILE_OPS_WRITE_ENABLED", "true")
    from app.agents.tools import file_ops as file_module

    admin = _make_user(Role.TENANT_ADMIN, "pagt002-wbig", created)
    log_context.set_user(admin.id, admin.tenant_id)
    tool = file_module.file_ops_write_tool()
    registry = ToolRegistry([tool])

    # 超限写：500 字符、max_bytes=10
    big = {"root": "public", "path": "big.txt", "content": "x" * 500, "max_bytes": 10}
    out_big = asyncio.run(registry.run(ToolCall(name="file_ops_write", arguments=big)))
    assert "content_too_large" in out_big, out_big
    assert not (tmp_path / "big.txt").exists(), "超限写不得残留 0 字节空壳"

    # 同路径合法写仍应成功（空壳未占坑）
    ok = {"root": "public", "path": "big.txt", "content": "hello\n"}
    out_ok = asyncio.run(registry.run(ToolCall(name="file_ops_write", arguments=ok)))
    assert '"status": "ok"' in out_ok, out_ok
    assert (tmp_path / "big.txt").read_text(encoding="utf-8") == "hello\n"
    _cleanup(_audit_rows())
