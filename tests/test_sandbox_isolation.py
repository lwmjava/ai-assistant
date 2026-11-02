"""Adversarial language-boundary cases and real Linux kernel isolation probes."""

import asyncio
import ctypes
import json
import os
import platform
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from app.agents.tools.sandbox.base import KillReason, SandboxConfig
from app.agents.tools.sandbox.linux_isolation import install_linux_isolation, network_filter
from app.agents.tools.sandbox.proc import process_alive, run_script
from app.agents.tools.sandbox.runtime import child_environment
from app.agents.tools.sandbox.sandbox import CodeSandbox


def evaluate_filter(rows, *, arch, number):
    """Interpret the small classic-BPF instruction subset used by this filter."""
    accumulator = 0
    pc = 0
    while pc < len(rows):
        code, yes, no, value = rows[pc]
        if code == 0x20:
            accumulator = arch if value == 4 else number
        elif code == 0x15:
            pc += yes if accumulator == value else no
        elif code == 0x35:
            pc += yes if accumulator >= value else no
        elif code == 0x06:
            return value
        else:
            raise AssertionError(code)
        pc += 1
    raise AssertionError("filter has no return")


@pytest.mark.parametrize("machine,arch,syscalls", [
    ("x86_64", 0xC000003E, [41, 42, 53, 288, 299, 307, 425, 426, 427, 435, 438]),
    ("aarch64", 0xC00000B7, [198, 199, 203, 242, 243, 269, 425, 426, 427, 435, 438]),
])
def test_filter_denies_network_alternate_abi_and_async_io(machine, arch, syscalls):
    rows = network_filter(machine)
    for number in [*syscalls, 512, 0x40000029, 0xFFFFFFFF]:
        assert evaluate_filter(rows, arch=arch, number=number) == 0x00050001
    assert evaluate_filter(rows, arch=0x40000003, number=102) == 0x80000000
    assert evaluate_filter(rows, arch=arch, number=1) == 0x7FFF0000


def test_unknown_machine_rejected():
    with pytest.raises(RuntimeError, match="sandbox_isolation_setup_failed"):
        network_filter("unknown")


@pytest.mark.parametrize("uids", [(0, 0, 0), (1000, 1000, 0), (0, 1000, 1000)])
def test_root_or_saved_root_rejected_before_kernel_calls(monkeypatch, uids):
    monkeypatch.setattr(os, "getresuid", lambda: uids, raising=False)
    with pytest.raises(RuntimeError, match="sandbox_isolation_setup_failed"):
        install_linux_isolation()


@pytest.mark.parametrize("failure", ["capget", "capability", "no_new_privs", "seccomp"])
def test_kernel_setup_errors_fail_closed(monkeypatch, failure):
    monkeypatch.setattr(os, "getresuid", lambda: (1000, 1000, 1000), raising=False)
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")

    def capget(header, data):
        if failure == "capability":
            # First uint32 in the data array is the effective capability mask.
            ctypes.cast(data, ctypes.POINTER(ctypes.c_uint32))[0] = 1
        return -1 if failure == "capget" else 0

    calls = []

    def prctl(option, *args):
        calls.append(option)
        return -1 if (failure == "no_new_privs" and option == 38
                      or failure == "seccomp" and option == 22) else 0

    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **kw: SimpleNamespace(capget=capget, prctl=prctl))
    with pytest.raises(RuntimeError, match="sandbox_isolation_setup_failed"):
        install_linux_isolation()
    if failure in {"capget", "capability"}:
        assert calls == []
    elif failure == "no_new_privs":
        assert calls == [38]
    else:
        assert calls == [38, 22]


@pytest.mark.parametrize("code", [
    "print((1).__class__.__base__.__subclasses__())",
    "print(getattr(1, '_' + '_class__'))",
    "getter = getattr\nprint(getter(1, '__class__'))",
    "print(vars())",
    "print('{0.__class__}'.format(1))",
    "print('{x.__class__}'.format_map({'x': 1}))",
    "g = (x for x in [1])\nprint(g.gi_frame.f_builtins)",
    "print(__builtins__)",
    "__sandbox_builtins__ = 1",
    "from . import math",
    "from math import *",
    "import math as __sandbox_builtins__",
    "class Evil:\n    def __getattribute__(self, name):\n        return 1",
])
def test_reflection_rejected_before_process_launch(code):
    result = asyncio.run(CodeSandbox().execute(code))
    assert result.killed_by == KillReason.AST_REJECTED
    assert result.child_pid == 0


@pytest.mark.parametrize("module", ["os", "socket", "ssl", "posix", "_io", "sqlite3", "code"])
def test_config_cannot_expand_trusted_imports(module):
    result = asyncio.run(CodeSandbox().execute(
        f"import {module}", SandboxConfig(allowed_imports=[module])))
    assert result.killed_by == KillReason.AST_REJECTED
    assert result.child_pid == 0


@pytest.mark.parametrize("code", [
    "import json\nprint(json.codecs.open(PATH).read())",
    "import statistics\nprint(statistics.sys.modules['os'].open(PATH, 0))",
    "import statistics\nprint(statistics.random._os.open(PATH, 0))",
    ("import functools, statistics\ndef f():\n    pass\n"
     "functools.update_wrapper(f, statistics.mean, updated=('__globals__',))\n"
     "print(sys.modules['os'].open(PATH, 0))"),
])
def test_stdlib_transitive_capabilities_not_exposed(tmp_path, code):
    secret = tmp_path / "sentinel.txt"
    secret.write_text("SENTINEL_FILE_CONTENT", encoding="utf-8")
    result = asyncio.run(CodeSandbox().execute(code.replace("PATH", repr(str(secret)))))
    assert result.exit_code != 0
    assert "SENTINEL_FILE_CONTENT" not in result.stdout + result.stderr


def test_cancel_waits_for_real_process_termination(monkeypatch):
    import app.agents.tools.sandbox.sandbox as implementation

    started = threading.Event()
    completed = threading.Event()
    outcomes = []
    original = implementation.run_script

    def track(*args, **kwargs):
        started.set()
        try:
            outcome = original(*args, **kwargs)
            outcomes.append(outcome)
            return outcome
        finally:
            completed.set()

    monkeypatch.setattr(implementation, "run_script", track)

    async def scenario():
        task = asyncio.create_task(CodeSandbox().execute(
            "while True:\n    pass", SandboxConfig(timeout_seconds=20)))
        deadline = time.monotonic() + 5
        while not started.is_set():
            assert time.monotonic() < deadline
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=3)
        assert completed.is_set()

    asyncio.run(scenario())
    assert outcomes and outcomes[0].pid > 0
    assert not process_alive(outcomes[0].pid)


@pytest.mark.parametrize("code,expected", [
    ("import math\nprint(math.sqrt(81))", "9.0"),
    ("from statistics import mean\nprint(mean([1, 2, 3]))", "2"),
    ("import json\nprint(json.dumps([1, 2]))", "[1, 2]"),
    ("import datetime\nprint(datetime.date(2026, 1, 2).isoformat())", "2026-01-02"),
])
def test_trusted_imports_remain_usable(code, expected):
    result = asyncio.run(CodeSandbox().execute(code))
    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == expected


@pytest.mark.skipif(sys.platform != "linux", reason="requires real Linux seccomp")
def test_kernel_blocks_network_even_without_ast(tmp_path):
    script = tmp_path / "kernel_probe.py"
    script.write_text('''
import ctypes, errno, json, os, platform, socket
status = open('/proc/self/status').read()
assert os.getuid() != 0 and os.geteuid() != 0
assert 'NoNewPrivs:\\t1' in status
assert 'CapEff:\\t0000000000000000' in status
for family in (socket.AF_INET, socket.AF_INET6, socket.AF_UNIX):
    try:
        socket.socket(family, socket.SOCK_STREAM)
    except OSError as exc:
        assert exc.errno == errno.EPERM, exc
    else:
        raise AssertionError('network allowed')
libc = ctypes.CDLL(None, use_errno=True)
# io_uring_setup must not provide an alternate networking path.
assert libc.syscall(425, 1, 0) == -1
assert ctypes.get_errno() == errno.EPERM
connect = 42 if platform.machine() == 'x86_64' else 203
assert libc.syscall(connect, -1, 0, 0) == -1
assert ctypes.get_errno() == errno.EPERM
assert libc.syscall(0x40000029, 2, 1, 0) == -1
assert ctypes.get_errno() == errno.EPERM
print(json.dumps({'network': 'denied', 'uid': os.getuid()}))
''', encoding="utf-8")
    result = run_script(str(script), cwd=str(tmp_path),
                        env=child_environment(None, windows=False), timeout=10,
                        config=SandboxConfig())
    if os.getuid() == 0:
        assert result.exit_code != 0
        assert not result.stdout
        assert "sandbox_isolation_setup_failed" in result.stderr
    else:
        assert result.exit_code == 0, result.stderr
        assert json.loads(result.stdout)["network"] == "denied"
