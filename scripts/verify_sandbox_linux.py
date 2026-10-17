"""Verify actual Linux child exit and process-group termination without external dependencies."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import sys
import tempfile
import time
import types
from pathlib import Path


def main() -> int:
    if sys.platform != "linux":
        raise RuntimeError("This acceptance runner requires Linux")
    root = Path(__file__).resolve().parents[1]
    # Load the standard-library sandbox modules without application startup or credentials.
    for name in ("app", "app.agents", "app.agents.tools", "app.agents.tools.sandbox"):
        package = types.ModuleType(name)
        package.__path__ = [str(root.joinpath(*name.split(".")))]
        sys.modules[name] = package
    proc = importlib.import_module("app.agents.tools.sandbox.proc")
    source = root / "app/agents/tools/sandbox/proc.py"
    report = {"platform": sys.platform, "python": sys.version.split()[0],
              "source_sha256": hashlib.sha256(source.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
              "checks": []}
    child = os.fork()
    if child == 0:
        os._exit(0)
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = Path(f"/proc/{child}/stat").read_text().rsplit(")", 1)[1].split()[0]
            if status == "Z":
                break
            time.sleep(0.01)
        else:
            raise TimeoutError("Child did not become a zombie")
        os.kill(child, 0)
        report["checks"].append({"name": "zombie_is_not_running", "proc_state": status,
                                 "kill_zero_present": True, "passed": not proc.process_alive(child)})
    finally:
        os.waitpid(child, 0)
    report["checks"].append({"name": "current_process_is_running", "passed": proc.process_alive(os.getpid())})
    report["checks"].append({"name": "reaped_child_is_not_running", "passed": not proc.process_alive(child)})
    with tempfile.TemporaryDirectory(prefix="sandbox-process-acceptance-") as directory:
        parent = Path(directory) / "parent.py"
        parent.write_text(
            "import subprocess, sys, time\n"
            "from pathlib import Path\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "Path(sys.argv[1]).write_text(str(child.pid))\n"
            "time.sleep(60)\n"
        )
        for iteration in range(20):
            pid_file = Path(directory) / f"child-{iteration}.pid"
            parent_pid, child_pid = proc.terminate_contained_tree(
                [sys.executable, str(parent), str(pid_file)], cwd=directory)
            report["checks"].append({"name": f"terminate_tree_{iteration}",
                                     "passed": not proc.process_alive(parent_pid)
                                     and not proc.process_alive(child_pid)})
    report["passed"] = all(item["passed"] for item in report["checks"])
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
