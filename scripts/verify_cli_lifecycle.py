"""在独立项目中验证 Compose 命令，不挂载应用数据或调用模型。"""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    project = "cli-smoke-" + uuid.uuid4().hex[:12]
    directory = ROOT / "data" / project
    directory.mkdir(parents=True)
    compose = directory / "compose.yml"
    env_file = directory / "synthetic.env"
    env_file.write_text("POSTGRES_PASSWORD=synthetic-cli-password\n", encoding="utf-8")
    compose.write_text(
        "services:\n  worker:\n    image: python:3.12-slim\n    pull_policy: never\n"
        "    network_mode: none\n    read_only: true\n    cap_drop: [ALL]\n"
        "    security_opt: [no-new-privileges:true]\n    pids_limit: 32\n"
        "    command: [python, '-u', '-c', \"import time; print('lifecycle-ready'); "
        "print('password=synthetic-cli-password'); time.sleep(1800)\"]\n",
        encoding="utf-8",
    )
    options = ["--project-name", project, "--compose-file", str(compose), "--env-file", str(env_file)]
    environment = {key: value for key, value in os.environ.items() if not key.startswith("COMPOSE_")}
    environment["PYTHONUTF8"] = "1"
    evidence: dict = {"project": project, "steps": [], "model_calls": 0, "volumes": 0}

    def cli(command: str):
        result = subprocess.run(
            [sys.executable, "-m", "app.cli", command, *options, "--timeout", "45"],
            cwd=ROOT, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=55,
        )
        evidence["steps"].append({"command": command, "exit_code": result.returncode})
        assert result.returncode == 0, f"{command}: exit {result.returncode}"
        assert "synthetic-cli-password" not in result.stdout + result.stderr
        return result.stdout

    docker = ["docker", "compose", "--file", str(compose), "--env-file", str(env_file), "--project-name", project]

    def identity():
        result = subprocess.run(
            [*docker, "ps", "--quiet", "--all"], capture_output=True, text=True, timeout=15, check=True,
            env=environment,
        )
        return result.stdout.strip()

    try:
        cli("start")
        assert "running" in cli("status").lower() or "up" in cli("status").lower()
        first_id = identity()
        assert first_id
        logs = cli("logs")
        assert "lifecycle-ready" in logs
        assert "敏感内容" in logs
        cli("start")
        assert identity() == first_id
        evidence["repeat_start_same_container"] = True
        cli("stop")
        assert "exited" in cli("status").lower()
        cli("stop")
        evidence["repeat_stop_ok"] = True
        evidence["result"] = "passed"
    finally:
        cleanup = subprocess.run(
            [*docker, "down", "--timeout", "5"], capture_output=True, timeout=25, env=environment,
        )
        evidence["cleanup_exit"] = cleanup.returncode
        evidence["cleanup_scope"] = "random synthetic project only; no volume deletion"
        (directory / "report.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(json.dumps(evidence, ensure_ascii=False))
    return 0 if evidence.get("result") == "passed" and evidence["cleanup_exit"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
