"""配置独占创建及失败清理的独立反例。"""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.core import bootstrap_config


def test_concurrent_initializers_create_exactly_one_complete_config(tmp_path):
    destination = tmp_path / "concurrent.env"
    barrier = Barrier(2)

    def initialize():
        barrier.wait(timeout=10)
        try:
            bootstrap_config.initialize_config(destination)
            return True
        except FileExistsError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: initialize(), range(2)))
    assert sorted(outcomes) == [False, True]
    lines = destination.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("JWT_SECRET_KEY=")
    assert len(lines[0].split("=", 1)[1]) >= 64


@pytest.mark.skipif(os.name == "nt", reason="替换打开文件的身份保护需在 Linux 文件系统验证")
def test_write_failure_does_not_delete_replacement_file(tmp_path, monkeypatch):
    destination = tmp_path / "replaced.env"
    replacement = b"replacement must survive\n"

    def replace_then_fail(_descriptor):
        moved = destination.with_suffix(".original")
        destination.rename(moved)
        destination.write_bytes(replacement)
        raise OSError("synthetic flush failure after replacement")

    monkeypatch.setattr(bootstrap_config.os, "fsync", replace_then_fail)
    with pytest.raises(OSError):
        bootstrap_config.initialize_config(destination)
    assert destination.read_bytes() == replacement


def test_stream_open_failure_closes_descriptor_and_removes_only_new_file(tmp_path, monkeypatch):
    destination = tmp_path / "stream-failure.env"
    opened = []

    def fail_stream(descriptor, *args, **kwargs):
        opened.append(descriptor)
        raise OSError("synthetic stream creation failure")

    monkeypatch.setattr(bootstrap_config.os, "fdopen", fail_stream)
    with pytest.raises(OSError):
        bootstrap_config.initialize_config(destination)
    assert len(opened) == 1
    with pytest.raises(OSError):
        bootstrap_config.os.fstat(opened[0])
    assert not destination.exists()
