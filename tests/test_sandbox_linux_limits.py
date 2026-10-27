"""Real Linux kernel limits exercised through the production process launcher."""

import errno
import json
import os
import signal
import sys

import pytest

from app.agents.tools.sandbox.base import SandboxConfig
from app.agents.tools.sandbox.proc import process_alive, run_script
from app.agents.tools.sandbox.runtime import child_environment

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="requires real Linux resource limits")


def launch(tmp_path, code, **limits):
    script = tmp_path / "probe.py"
    script.write_text(code, encoding="utf-8")
    config = SandboxConfig(**({"max_memory_mb": 64, "max_disk_mb": 1, "max_cpu_seconds": 2} | limits))
    result = run_script(str(script), cwd=str(tmp_path),
                        env=child_environment(None, windows=False), timeout=6, config=config)
    assert result.pid > 0 and not process_alive(result.pid)
    assert not result.isolation_error
    return result


def test_linux_effective_limits_match_configuration(tmp_path):
    result = launch(tmp_path, """
import json, resource
print(json.dumps({name: resource.getrlimit(getattr(resource, name)) for name in
                 ['RLIMIT_CPU','RLIMIT_AS','RLIMIT_FSIZE','RLIMIT_NPROC']}))
""")
    assert result.exit_code == 0 and not result.timed_out
    assert json.loads(result.stdout) == {
        "RLIMIT_CPU": [2, 2], "RLIMIT_AS": [64 * 1024**2] * 2,
        "RLIMIT_FSIZE": [1024**2] * 2, "RLIMIT_NPROC": [0, 0],
    }


def test_linux_cpu_limit_terminates_before_wall_timeout(tmp_path):
    result = launch(tmp_path, "while True:\n    pass\n")
    assert result.exit_code == -signal.SIGKILL
    assert not result.timed_out


def test_linux_address_space_limit_prevents_large_allocation(tmp_path):
    result = launch(tmp_path, "payload = bytearray(128 * 1024 * 1024)\nprint('UNBOUNDED')\n")
    assert result.exit_code != 0 and not result.timed_out
    assert "MemoryError" in result.stderr
    assert "UNBOUNDED" not in result.stdout


def test_linux_file_size_limit_stops_write_at_boundary(tmp_path):
    result = launch(tmp_path, """
import json
try:
    with open('bounded.bin', 'wb', buffering=0) as handle:
        handle.write(b'x' * (2 * 1024 * 1024))
        handle.write(b'x')
except OSError as exc:
    print(json.dumps({'errno': exc.errno}))
""")
    assert result.exit_code == 0 and not result.timed_out
    assert json.loads(result.stdout)["errno"] == errno.EFBIG
    assert (tmp_path / "bounded.bin").stat().st_size == 1024**2


def test_linux_nonroot_process_limit_rejects_fork(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("RLIMIT_NPROC is not enforced for root; run as an unprivileged Linux UID")
    result = launch(tmp_path, """
import errno, os
try:
    child = os.fork()
except OSError as exc:
    print(exc.errno)
else:
    if child == 0:
        os._exit(0)
    os.waitpid(child, 0)
    print('UNBOUNDED')
""")
    assert result.exit_code == 0 and not result.timed_out
    assert result.stdout.strip() == str(errno.EAGAIN)


@pytest.mark.parametrize("setting", ["max_cpu_seconds", "max_memory_mb", "max_disk_mb"])
@pytest.mark.parametrize("value", [-1, 0])
def test_linux_nonpositive_limits_cannot_run_without_restrictions(tmp_path, setting, value):
    result = launch(tmp_path, "print('UNRESTRICTED')", **{setting: value})
    assert result.exit_code != 0 and not result.timed_out
    assert "UNRESTRICTED" not in result.stdout
    assert result.stderr.strip() == "sandbox_resource_limit_setup_failed"
