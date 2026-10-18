"""在 Linux 子进程中验证 CLI；临时 Docker 替身不连接真实 daemon。"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    if sys.platform != "linux":
        raise SystemExit("This smoke requires Linux")
    root = Path(__file__).resolve().parents[1]
    temporary = Path(tempfile.mkdtemp(prefix="cli-linux-"))
    docker = temporary / "docker"
    docker.write_text(
        f"#!{sys.executable}\n"
        "import os,sys,time\n"
        "mode=os.environ.get('CLI_SMOKE_MODE','')\n"
        "if mode=='timeout': time.sleep(10)\n"
        "if mode=='failure': print('private-synthetic-value',file=sys.stderr); sys.exit(17)\n"
        "print('linux-cli-marker')\n"
        "print('private-synthetic-value')\n",
        encoding="utf-8",
    )
    docker.chmod(0o700)
    compose = temporary / "compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    env_file = temporary / "synthetic.env"
    env_file.write_text("POSTGRES_PASSWORD=$BASE\n", encoding="utf-8")
    environment = {**os.environ, "PATH": str(temporary), "BASE": "private-synthetic-value"}
    args = ["--project-name", "linux-smoke", "--env-file", str(env_file), "--compose-file", str(compose)]
    checks = []
    for command, mode, expected in [
        ("start", "", 0), ("stop", "", 0), ("status", "", 0), ("logs", "", 0),
        ("start", "failure", 17), ("start", "timeout", 124),
    ]:
        result = subprocess.run(
            [sys.executable, "-m", "app.cli", command, *args, "--timeout", "1"],
            cwd=root, env={**environment, "CLI_SMOKE_MODE": mode},
            capture_output=True, text=True, timeout=5,
        )
        assert result.returncode == expected, (command, mode, result.returncode)
        assert "private-synthetic-value" not in result.stdout + result.stderr
        if command in {"status", "logs"}:
            assert "linux-cli-marker" in result.stdout
        checks.append({"command": command, "mode": mode, "exit_code": result.returncode})
    print(json.dumps({"platform": sys.platform, "python": sys.version.split()[0], "checks": checks,
                      "docker": "synthetic executable; no daemon or production data", "result": "passed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
