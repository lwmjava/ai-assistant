"""Resource-bound file arguments and create-only failure behavior."""

import asyncio
import importlib
import json
import os
import sys

import pytest

from app.agents.tools.base import ToolCall, ToolRegistry
from app.agents.tools.parameter_policy import ParameterRejectedError
from app.core.config import get_settings

files = importlib.import_module("app.agents.tools.file_ops")


@pytest.fixture
def public_root(monkeypatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(settings, "FILE_OPS_ENABLED", True)
    monkeypatch.setattr(settings, "FILE_OPS_ALLOWED_ROOTS", json.dumps({"public": str(tmp_path)}))
    monkeypatch.setattr(settings, "FILE_OPS_MAX_BYTES", 32)
    monkeypatch.setenv("FILE_OPS_WRITE_ENABLED", "true")
    (tmp_path / "notice.txt").write_text("public", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("path", ["../outside.txt", "C:/outside.txt", "notice.txt:stream", "folder/../notice.txt"])
def test_read_guard_rejects_path_before_tool(public_root, path):
    tool = files.file_ops_tool()
    assert tool is not None and tool.argument_guard is not None
    with pytest.raises(ParameterRejectedError):
        asyncio.run(tool.argument_guard({"root": "public", "path": path}))


def test_write_guard_bounds_encoded_bytes(public_root):
    tool = files.file_ops_write_tool()
    assert tool is not None and tool.argument_guard is not None
    with pytest.raises(ParameterRejectedError):
        asyncio.run(tool.argument_guard({"root": "public", "path": "new.txt", "content": "汉" * 12}))
    assert not (public_root / "new.txt").exists()


def test_write_checks_opened_object_before_content(public_root, monkeypatch):
    monkeypatch.setattr(files, "_handle_path", lambda fd: public_root.parent / "outside.txt")
    with pytest.raises(ValueError):
        files._write(public_root, "new.txt", "public", 32)
    assert not (public_root / "new.txt").exists()


def test_partial_write_is_completed(public_root, monkeypatch):
    original = os.write
    monkeypatch.setattr(files.os, "write", lambda fd, data: original(fd, data[:2]))
    files._write(public_root, "new.txt", "公开内容", 32)
    assert (public_root / "new.txt").read_text(encoding="utf-8") == "公开内容"


def test_write_failure_removes_only_created_file(public_root, monkeypatch):
    original = os.write
    calls = 0

    def fail_after_partial(fd, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            return original(fd, data[:1])
        raise OSError("synthetic I/O failure")

    monkeypatch.setattr(files.os, "write", fail_after_partial)
    with pytest.raises(OSError):
        files._write(public_root, "new.txt", "public", 32)
    assert not (public_root / "new.txt").exists()
    assert (public_root / "notice.txt").read_text(encoding="utf-8") == "public"


def test_existing_file_stays_unchanged(public_root):
    with pytest.raises(FileExistsError):
        files._write(public_root, "notice.txt", "replacement", 32)
    assert (public_root / "notice.txt").read_text(encoding="utf-8") == "public"


def test_registry_rejects_path_before_func(public_root):
    tool = files.file_ops_tool()
    calls = []
    audit = []

    async def forbidden(arguments):
        calls.append(arguments)
        return "should not run"

    async def record(*args):
        audit.append(args)

    tool.func = forbidden
    registry = ToolRegistry([tool])
    registry._record_audit = record
    result = asyncio.run(registry.run(ToolCall("file_ops", {"root": "public", "path": "../outside.txt"})))
    assert result.status == "invalid"
    assert result.error_code == "invalid_arguments"
    assert not calls
    assert len(audit) == 1 and audit[0][4] is False


def test_directory_replacement_during_open_never_writes_elsewhere(public_root, monkeypatch):
    original = os.open
    moved = public_root.with_name(public_root.name + "-moved")
    attempts = []

    def replace_directory(*args, **kwargs):
        fd = original(*args, **kwargs)
        if not attempts:
            attempts.append(True)
            if sys.platform == "win32":
                with pytest.raises(PermissionError):
                    os.replace(public_root, moved)
            else:
                os.replace(public_root, moved)
        return fd

    monkeypatch.setattr(files.os, "open", replace_directory)
    if sys.platform == "win32":
        files._write(public_root, "new.txt", "public", 32)
        assert (public_root / "new.txt").read_text() == "public"
        assert not moved.exists()
    else:
        with pytest.raises(ValueError):
            files._write(public_root, "new.txt", "public", 32)
        assert not (moved / "new.txt").exists()
        assert (moved / "notice.txt").read_text() == "public"


def test_detected_hardlink_race_erases_payload_from_created_inode(public_root, monkeypatch):
    original = os.write
    outside = public_root.with_name(public_root.name + "-outside")
    outside.mkdir()
    leaked = outside / "leaked.txt"

    def add_alias_before_write(fd, data):
        os.link(public_root / "new.txt", leaked)
        return original(fd, data)

    monkeypatch.setattr(files.os, "write", add_alias_before_write)
    with pytest.raises(ValueError, match="file_changed"):
        files._write(public_root, "new.txt", "public payload", 32)
    assert not (public_root / "new.txt").exists()
    assert leaked.read_bytes() == b""
    assert (public_root / "notice.txt").read_text() == "public"


@pytest.mark.skipif(sys.platform == "win32", reason="Windows directory locks prohibit this relocation")
def test_detected_directory_move_erases_payload_before_cleanup(public_root, monkeypatch):
    original = os.write
    moved = public_root.with_name(public_root.name + "-moved")
    aliases = public_root.with_name(public_root.name + "-aliases")
    aliases.mkdir()
    leaked = aliases / "leaked.txt"

    def move_before_write(fd, data):
        os.link(public_root / "new.txt", leaked)
        os.replace(public_root, moved)
        return original(fd, data)

    monkeypatch.setattr(files.os, "write", move_before_write)
    with pytest.raises(ValueError, match="file_changed"):
        files._write(public_root, "new.txt", "public payload", 32)
    assert not (moved / "new.txt").exists()
    assert leaked.read_bytes() == b""
    assert (moved / "notice.txt").read_text() == "public"
