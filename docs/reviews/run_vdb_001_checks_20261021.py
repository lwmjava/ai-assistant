"""Run isolated offline checks and record their exact commands and exit codes."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage")
    parser.add_argument("check", choices=["pytest", "ruff", "mypy"])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    evidence = root / "docs/reviews"
    prefix = f"vdb-001-{args.stage}-{args.check}"
    env = dict(os.environ)
    env.update({"PYTHONIOENCODING": "utf-8", "ENV": "development",
                "DATABASE_URL": f"sqlite:///./data/{prefix}.db",
                "RUFF_CACHE_DIR": str(root / "data" / f"{prefix}-ruff-cache")})
    command = [sys.executable, "-m", args.check]
    if args.check == "pytest":
        command += [f"--basetemp=data/pytest-tmp/{prefix}",
                    f"--junitxml=docs/reviews/{prefix}.xml"]
    elif args.check == "ruff":
        command += ["check", "."]
    else:
        command += ["app/"]
    started = time.monotonic()
    log = evidence / f"{prefix}.log"
    with log.open("w", encoding="utf-8") as stream:
        result = subprocess.run(command, cwd=root, env=env, stdout=stream, stderr=subprocess.STDOUT)
    record = {"command": command, "exit_code": result.returncode,
              "elapsed_seconds": round(time.monotonic() - started, 2),
              "environment": {key: env[key] for key in ("ENV", "DATABASE_URL", "RUFF_CACHE_DIR")},
              "interpreter": sys.executable, "log": str(log),
              "scope": "offline check; conftest blocks workspace credentials and external network"}
    (evidence / f"{prefix}.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record))
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
