"""源文件存储层测试。

覆盖：保存源文件、按路径读回、删除文件，以及不安全扩展名的兜底处理。
存储根目录通过 monkeypatch 重定向到临时目录，避免污染真实 data/ 目录。
"""

from pathlib import Path

import pytest


def _patch_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """将存储根目录重定向到临时目录。"""
    import app.rag.document_storage as storage_mod

    monkeypatch.setattr(storage_mod, "_PROJECT_ROOT", tmp_path)


def test_save_read_delete_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_root(monkeypatch, tmp_path)
    from app.rag.document_storage import (
        delete_source_file,
        read_source_file,
        save_source_file,
    )

    content = b"%PDF-1.4 fake pdf bytes"
    rel_path = save_source_file("tenant-1", content, "手册.pdf")
    assert rel_path.startswith("data/knowledge/tenant-1/")

    assert read_source_file(rel_path) == content
    assert delete_source_file(rel_path) is True

    with pytest.raises(FileNotFoundError):
        read_source_file(rel_path)


def test_save_creates_tenant_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_root(monkeypatch, tmp_path)
    from app.rag.document_storage import save_source_file

    save_source_file("tenant-2", b"hello", "note.txt")
    assert (tmp_path / "data" / "knowledge" / "tenant-2").is_dir()


def test_save_sanitizes_unsafe_extension(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_root(monkeypatch, tmp_path)
    from app.rag.document_storage import save_source_file

    rel_path = save_source_file("tenant-3", b"x", "malicious.exe")
    assert rel_path.endswith(".bin")


def test_delete_nonexistent_returns_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_root(monkeypatch, tmp_path)
    from app.rag.document_storage import delete_source_file

    assert delete_source_file("data/knowledge/tenant-9/invalid-unused.bin") is False
