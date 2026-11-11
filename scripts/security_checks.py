"""Shared staged-secret, public dependency audit, and hardcoded URL scan entry points."""

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
from urllib.parse import urlparse, urlunparse


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


DEFAULT_URL_ALLOWLIST = Path("scripts/hardcoded_url_allowlist.json")
URL_PATTERN = re.compile(r"(?i)\b(?:https?|wss?)://[^\s\"'`<>\\)]+")
TRAILING_URL_CHARS = ".,;:)]}'\""
SCAN_EXTENSIONS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".vue",
    ".css",
    ".html",
    ".yml",
    ".yaml",
    ".json",
    ".toml",
}
SKIP_DIRECTORIES = {
    ".git",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".venv",
    "venv",
}
MAX_SCAN_BYTES = 1_000_000
BUILTIN_EXACT_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "example.com",
    "example.net",
    "example.org",
    "example.invalid",
    "www.w3.org",
    "schema.org",
    "json-schema.org",
}
BUILTIN_HOST_SUFFIXES = (
    ".localhost",
    ".example.com",
    ".example.net",
    ".example.org",
    ".example",
    ".invalid",
    ".test",
)


def is_environment_file(path: Path) -> bool:
    name = path.name
    return name == ".env" or name.startswith(".env.") and not name.endswith(".example")


def normalize_url_token(token: str) -> str:
    cleaned = token.strip()
    while cleaned and cleaned[-1] in TRAILING_URL_CHARS:
        cleaned = cleaned[:-1]
    return cleaned


def display_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname
    if host is None:
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path or "", "", "", ""))
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{host}:{parsed.port}" if parsed.port else host
    return urlunparse((parsed.scheme, netloc, parsed.path or "", "", "", ""))


def is_placeholder_host(host: str) -> bool:
    lowered = host.lower()
    return lowered == "host" or any(char in host for char in "{}<>$")


def host_allowed(host: str, allowed_hosts: set[str]) -> bool:
    lowered = host.lower().rstrip(".")
    if lowered in BUILTIN_EXACT_HOSTS or lowered in allowed_hosts:
        return True
    if any(lowered.endswith(suffix) for suffix in BUILTIN_HOST_SUFFIXES):
        return True
    for candidate in allowed_hosts:
        if "." in candidate and (lowered == candidate or lowered.endswith("." + candidate)):
            return True
    return False


def url_prefix_allowed(url: str, prefixes: tuple[str, ...]) -> bool:
    displayed = display_url(url)
    return any(displayed.startswith(prefix) or url.startswith(prefix) for prefix in prefixes)


def load_url_allowlist(path: Path) -> tuple[set[str], tuple[str, ...]]:
    if not path.is_file():
        raise CheckError("url_allowlist_unavailable")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        raise CheckError("url_allowlist_unavailable") from exc
    if not isinstance(raw, dict):
        raise CheckError("url_allowlist_unavailable")
    hosts = raw.get("hosts", [])
    prefixes = raw.get("url_prefixes", [])
    if not isinstance(hosts, list) or not isinstance(prefixes, list):
        raise CheckError("url_allowlist_unavailable")
    if any(not isinstance(item, str) or not item.strip() for item in [*hosts, *prefixes]):
        raise CheckError("url_allowlist_unavailable")
    return {item.strip().lower().rstrip(".") for item in hosts}, tuple(item.strip() for item in prefixes)


def iter_scan_paths(root: Path) -> list[Path]:
    discovered: set[Path] = set()
    for relative in ("app", "frontend/src"):
        base = root / relative
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if (
                path.is_file()
                and path.suffix.lower() in SCAN_EXTENSIONS
                and not any(part in SKIP_DIRECTORIES for part in path.parts)
                and not is_environment_file(path)
            ):
                discovered.add(path)
    for pattern in ("docker-compose*.yml", "docker-compose*.yaml", "compose*.yml", "compose*.yaml"):
        discovered.update(path for path in root.glob(pattern) if path.is_file() and not is_environment_file(path))
    deploy = root / "deploy"
    if deploy.is_dir():
        for path in deploy.rglob("*"):
            if (
                path.is_file()
                and path.suffix.lower() in SCAN_EXTENSIONS
                and not any(part in SKIP_DIRECTORIES for part in path.parts)
                and not is_environment_file(path)
            ):
                discovered.add(path)
    return sorted(discovered, key=lambda item: item.as_posix().lower())


def extract_urls(text: str) -> list[tuple[int, str, str]]:
    found: list[tuple[int, str, str]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        for match in URL_PATTERN.findall(line):
            url = normalize_url_token(match)
            parsed = urlparse(url)
            host = parsed.hostname
            if not host or is_placeholder_host(host):
                continue
            found.append((line_number, url, host.lower().rstrip(".")))
    return found


def scan_urls(root: Path, *, allowlist: Path | None = None, output: Path | None = None) -> dict:
    allowlist_path = allowlist if allowlist is not None else root / DEFAULT_URL_ALLOWLIST
    allowed_hosts, prefixes = load_url_allowlist(allowlist_path)
    findings: list[dict[str, object]] = []
    checked = 0
    for path in iter_scan_paths(root):
        if path.stat().st_size > MAX_SCAN_BYTES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeError:
            continue
        checked += 1
        relative = path.relative_to(root).as_posix()
        for line_number, url, host in extract_urls(text):
            if host_allowed(host, allowed_hosts) or url_prefix_allowed(url, prefixes):
                continue
            findings.append({
                "file": relative,
                "line": line_number,
                "host": host,
                "url": display_url(url),
            })
    report = {
        "status": "failed" if findings else "passed",
        "files_checked": checked,
        "findings": findings,
        "allowlist": allowlist_path.as_posix(),
    }
    if output is not None:
        output.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
        report["report"] = str(output)
    return report


def selftest_urls() -> dict:
    with tempfile.TemporaryDirectory(prefix="url-scan-selftest-") as temporary:
        root = Path(temporary)
        (root / "app").mkdir()
        (root / "frontend" / "src").mkdir(parents=True)
        (root / "deploy").mkdir()
        (root / "scripts").mkdir()
        allowlist = root / DEFAULT_URL_ALLOWLIST
        allowlist.write_text(
            json.dumps({"hosts": ["example.com"], "url_prefixes": ["https://allowed.docs.example/"]}),
            encoding="utf-8",
        )
        (root / "app" / "allowed.py").write_text(
            'DOCS = "https://example.com/guide"\n'
            'LOCAL = "http://localhost:8080/health"\n'
            'PREFIX = "https://allowed.docs.example/reference"\n'
            'TEMPLATE = "https://{raw}/path"\n',
            encoding="utf-8",
        )
        (root / "app" / ".env").write_text("INTERNAL_URL=http://10.0.0.8/secret\n", encoding="utf-8")
        (root / "app" / "blocked.py").write_text(
            'INTRANET = "http://192.168.10.25/admin?token=should-redact"\n'
            'PRODUCTION = "https://billing.corp.internal/v1"\n',
            encoding="utf-8",
        )
        (root / "frontend" / "src" / "hook.ts").write_text(
            'export const hook = "https://example.com/hook";\n',
            encoding="utf-8",
        )
        result = scan_urls(root, allowlist=allowlist)
        hosts = {item["host"] for item in result["findings"]}
        files = {item["file"] for item in result["findings"]}
        displayed = {item["url"] for item in result["findings"]}
        if result["status"] != "failed":
            raise CheckError("url_selftest_failed")
        if hosts != {"192.168.10.25", "billing.corp.internal"}:
            raise CheckError("url_selftest_failed")
        if files != {"app/blocked.py"}:
            raise CheckError("url_selftest_failed")
        if any("token=" in value or "should-redact" in value for value in displayed):
            raise CheckError("url_selftest_failed")
        if "http://192.168.10.25/admin" not in displayed:
            raise CheckError("url_selftest_failed")
        (root / "app" / "blocked.py").write_text("print(0)\n", encoding="utf-8")
        clean = scan_urls(root, allowlist=allowlist)
        if clean["status"] != "passed" or clean["findings"]:
            raise CheckError("url_selftest_failed")
    return {"status": "passed", "check": "urls-selftest"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check", choices=("secrets", "python-audit", "npm-audit", "urls"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--staged", action="store_true")
    parser.add_argument("--base-ref")
    parser.add_argument("--output")
    parser.add_argument("--allowlist", type=Path)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.check == "secrets":
            result = scan_secrets(root, staged=args.staged, base_ref=args.base_ref)
        elif args.check == "urls":
            if args.selftest:
                result = selftest_urls()
            else:
                output = audit_output(root, args.output, "hardcoded-url-scan") if args.output else None
                result = scan_urls(root, allowlist=args.allowlist, output=output)
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
