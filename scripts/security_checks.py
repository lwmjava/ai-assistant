"""Shared staged-secret and public dependency audit entry points."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path


class CheckError(RuntimeError):
    """A fixed public-safe scan failure code."""


def git(root: Path, *args: str, optional: bool = False) -> bytes | None:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=30)
    if result.returncode:
        if optional:
            return None
        raise CheckError("git_scan_input_unavailable")
    return result.stdout


def scan_blob(data: bytes, filename: str) -> dict[tuple[str, str], int]:
    from detect_secrets import SecretsCollection
    from detect_secrets.settings import default_settings

    with tempfile.TemporaryDirectory(prefix="secret-scan-") as temporary:
        path = Path(temporary) / Path(filename).name
        path.write_bytes(data)
        secrets = SecretsCollection()
        with default_settings() as settings:
            settings.disable_filters("detect_secrets.filters.common.is_ignored_due_to_verification_policies")
            secrets.scan_file(str(path))
        return {(secret.type, secret.secret_hash): secret.line_number for _, secret in secrets}


def scan_secrets(root: Path, *, staged: bool, base_ref: str | None) -> dict:
    if staged:
        base = "HEAD" if git(root, "rev-parse", "--verify", "HEAD", optional=True) else None
        return scan_revision(root, base=base, target=None)
    if base_ref and base_ref != "0" * 40:
        if not re.fullmatch(r"[0-9a-fA-F]{40}", base_ref):
            raise CheckError("invalid_scan_base")
        git(root, "cat-file", "-e", base_ref + "^{commit}")
        base = base_ref
    else:
        parent = git(root, "rev-parse", "--verify", "HEAD^", optional=True)
        base = parent.decode().strip() if parent else None
    revisions = git(root, "rev-list", "--reverse", base + "..HEAD") if base else b"HEAD"
    assert revisions is not None
    combined: dict = {"status": "passed", "files_checked": 0, "findings": []}
    for revision in revisions.decode().splitlines():
        parents = git(root, "rev-list", "--parents", "-n", "1", revision)
        assert parents is not None
        parent_ids = parents.decode().split()[1:]
        result = scan_revision(root, base=parent_ids[0] if parent_ids else None, target=revision)
        combined["files_checked"] += result["files_checked"]
        combined["findings"].extend(dict(item, commit=revision) for item in result["findings"])
    if combined["findings"]:
        combined["status"] = "failed"
    return combined


def scan_revision(root: Path, *, base: str | None, target: str | None) -> dict:
    staged = target is None
    if staged:
        args = ["diff", "--cached"]
    else:
        assert target is not None
        args = ["diff", base, target] if base else ["diff-tree", "--root", "-r", "--no-commit-id", target]
    if staged and base is not None:
        args.append(base)
    args += ["--no-ext-diff", "--no-textconv", "--name-status", "-z", "--find-renames", "--diff-filter=ACMRT"]
    raw = git(root, *args)
    assert raw is not None
    fields = raw.decode("utf-8").split("\0")
    findings = []
    checked = 0
    offset = 0
    while offset < len(fields) - 1:
        status, filename = fields[offset:offset + 2]
        offset += 2
        old_name = filename
        if status.startswith("R"):
            filename = fields[offset]
            offset += 1
        names = [Path(filename).name, Path(old_name).name]
        if any(name == ".env" or name.startswith(".env.") and not name.endswith(".example") for name in names):
            findings.append({"file": filename, "type": "forbidden_environment_file", "line": 0})
            continue
        current = git(root, "show", (":" if staged else target + ":") + filename)
        previous = git(root, "show", base + ":" + old_name) if base and not status.startswith("A") else b""
        assert current is not None and previous is not None
        known = scan_blob(previous, old_name)
        observed = scan_blob(current, filename)
        checked += 1
        findings.extend({"file": filename, "type": kind, "line": line}
                        for (kind, fingerprint), line in observed.items() if (kind, fingerprint) not in known)
    return {"status": "failed" if findings else "passed", "files_checked": checked, "findings": findings}


def audit_output(root: Path, output: str | None, name: str) -> Path:
    directory = (root / "data/validation").resolve()
    if not directory.is_relative_to(root.resolve()):
        raise CheckError("audit_output_outside_validation")
    path = Path(output).resolve() if output else directory / (name + ".json")
    if not path.is_relative_to(directory):
        raise CheckError("audit_output_outside_validation")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    return path


def python_requirements(root: Path) -> str:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    dependencies = list(project["dependencies"])
    for extra in ("dev", "langgraph"):
        dependencies.extend(project["optional-dependencies"][extra])
    return "-r " + (root / "requirements.txt").as_posix() + "\n" + "\n".join(dict.fromkeys(dependencies)) + "\n"


def audit_python(root: Path, output: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="dependency-audit-") as temporary:
        manifest = Path(temporary) / "requirements.txt"
        manifest.write_text(python_requirements(root), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-m", "pip_audit", "-r", str(manifest), "--strict", "--format", "json",
             "--output", str(output), "--progress-spinner", "off"],
            cwd=root, capture_output=True, timeout=300,
            env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"),
        )
    if not output.is_file():
        raise CheckError("python_audit_incomplete")
    report = json.loads(output.read_text(encoding="utf-8"))
    dependencies = report.get("dependencies")
    if not isinstance(dependencies, list) or not dependencies or any("skip_reason" in item for item in dependencies):
        raise CheckError("python_audit_incomplete")
    count = sum(len(item["vulns"]) for item in dependencies)
    if result.returncode not in (0, 1) or bool(result.returncode) != bool(count):
        raise CheckError("python_audit_incomplete")
    return {"status": "failed" if count else "passed", "vulnerabilities": count, "report": str(output)}


def audit_npm(root: Path, output: Path) -> dict:
    npm = shutil.which("npm")
    if npm is None:
        raise CheckError("npm_not_installed")
    executable = [npm]
    if os.name == "nt" or Path(npm).suffix.lower() in {".cmd", ".bat"}:
        node = shutil.which("node.exe" if os.name == "nt" else "node")
        cli = Path(npm).parent / "node_modules/npm/bin/npm-cli.js"
        if node is None or not cli.is_file():
            raise CheckError("npm_cli_not_available")
        executable = [node, str(cli)]
    result = subprocess.run([*executable, "audit", "--json", "--package-lock-only", "--ignore-scripts"],
                            cwd=root / "frontend", capture_output=True, timeout=180)
    report = json.loads(result.stdout)
    if "error" in report:
        raise CheckError("npm_audit_incomplete")
    count = report["metadata"]["vulnerabilities"]["total"]
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise CheckError("npm_audit_incomplete")
    if result.returncode not in (0, 1) or bool(result.returncode) != bool(count):
        raise CheckError("npm_audit_incomplete")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "failed" if count else "passed", "vulnerabilities": count, "report": str(output)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check", choices=("secrets", "python-audit", "npm-audit"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--staged", action="store_true")
    parser.add_argument("--base-ref")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.check == "secrets":
            result = scan_secrets(root, staged=args.staged, base_ref=args.base_ref)
        else:
            output = audit_output(root, args.output, args.check)
            result = audit_python(root, output) if args.check == "python-audit" else audit_npm(root, output)
        print(json.dumps(result, ensure_ascii=True))
        return int(result["status"] != "passed")
    except Exception as exc:
        code = str(exc) if isinstance(exc, CheckError) else "security_check_incomplete"
        print(json.dumps({"status": "error", "code": code}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
