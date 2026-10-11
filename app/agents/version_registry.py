"""Offline source/model registration. Run this file directly; never loads Settings/.env."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import re
import subprocess
import sys
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = "docs/ai-prompts/prompt-model-registry.json"
FIELDS = (
    "LLM_PROVIDER",
    "LLM_DEFAULT_MODEL",
    "LLM_TEMPERATURE",
    "LLM_MAX_TOKENS",
    "LLM_TIMEOUT",
    "LLM_FALLBACK_PROVIDER",
    "LLM_FALLBACK_MODEL",
    "LLM_INTENT_PROVIDER",
    "LLM_INTENT_MODEL",
    "LLM_CHAT_FALLBACK_CHAIN",
    "LLM_INTENT_FALLBACK_CHAIN",
    "LLM_CAPABILITY_GUARD_ENABLED",
    "LLM_CAPABILITY_DECLARED",
    "LLM_BUDGET_SAFETY_MARGIN",
    "LLM_OUTPUT_RESERVE_TOKENS",
    "EMBEDDING_PROVIDER",
    "EMBEDDING_MODEL",
    "EMBEDDING_DIM",
    "EMBEDDING_BATCH_SIZE",
    "EMBEDDING_TIMEOUT_SECONDS",
    "EMBEDDING_INDEX_VERSION",
    "EMBEDDING_NORMALIZATION",
    "EMBEDDING_METRIC",
)
ENDPOINTS = (
    "LLM_BASE_URL",
    "LLM_FALLBACK_BASE_URL",
    "LLM_INTENT_BASE_URL",
    "EMBEDDING_BASE_URL",
    "LLM_CAPABILITY_DECLARED_SOURCE",
)
SECRET_FIELDS = {"LLM_API_KEY", "LLM_FALLBACK_API_KEY", "LLM_INTENT_API_KEY", "EMBEDDING_API_KEY", "DATABASE_URL"}


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def source_inventory(root: Path) -> dict[str, str]:
    """Conservative superset: adding any application source requires registration."""
    paths = list((root / "app").rglob("*.py"))
    for suffix in ("*.yaml", "*.yml"):
        paths.extend((root / "app/agents/skills/builtin").rglob(suffix))
    result = {}
    for path in sorted(paths):
        relative = path.relative_to(root).as_posix()
        if relative == "app/agents/version_registry.py":
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("source outside repository")
        content = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        result[relative] = hashlib.sha256(content).hexdigest()
    if "app/agents/prompts.py" not in result or "app/core/config.py" not in result:
        raise ValueError("required source missing")
    return result


def defaults(root: Path) -> dict[str, Any]:
    path = root / "app/core/config.py"
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("source outside repository")
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    values = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            for field in node.body:
                if isinstance(field, ast.AnnAssign) and isinstance(field.target, ast.Name):
                    if field.target.id in (*FIELDS, *ENDPOINTS):
                        if field.value is None:
                            raise ValueError("model field default missing")
                        values[field.target.id] = ast.literal_eval(field.value)
    if set(values) != set((*FIELDS, *ENDPOINTS)):
        raise ValueError("model defaults missing or unsupported")
    return values


def model_snapshot(values: Mapping[str, Any], reference: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(values, Mapping) or set(values) - set((*FIELDS, *ENDPOINTS)) - SECRET_FIELDS:
        raise ValueError("unknown model configuration field")
    fields = {}
    for name in FIELDS:
        value = values.get(name, reference[name])
        expected = type(reference[name])
        if expected is float:
            valid = type(value) in (float, int) and math.isfinite(value)
        else:
            valid = type(value) is expected
        if not valid:
            raise ValueError("invalid model field: " + name)
        if expected in (float, int):
            if value < 0 or (name not in ("LLM_TEMPERATURE", "LLM_BUDGET_SAFETY_MARGIN") and value == 0):
                raise ValueError("invalid model numeric range")
            if expected is float:
                value = float(value)
        fields[name] = value
    identities = {}
    for name in ENDPOINTS:
        value = values.get(name, reference[name])
        if not isinstance(value, str):
            raise ValueError("invalid endpoint identity")
        identities[name] = hashlib.sha256(value.encode("utf-8")).hexdigest()
    payload = {"fields": fields, "endpoint_identities": identities}
    return {**payload, "version": digest(payload)}


def make_record(
    root: Path,
    *,
    version: str,
    source_commit: str,
    effective_at: str,
    rollback: str,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    reference = defaults(root)
    sources = source_inventory(root)
    record = {
        "version": version,
        "source_commit": source_commit,
        "effective_at": effective_at,
        "rollback": rollback,
        "sources": sources,
        "source_version": digest(sources),
        "model": model_snapshot(config or {}, reference),
        "config_basis": "explicit-config" if config is not None else "source-defaults",
    }
    validate_record(record, reference)
    verify_source_commit(root, source_commit)
    return record


def verify_source_commit(root: Path, commit: str) -> None:
    """The Git ref is the change baseline; source hashes identify the actual snapshot."""
    if (root / ".git").exists():
        result = subprocess.run(
            ["git", "-C", str(root), "cat-file", "-t", commit], capture_output=True, text=True, timeout=10, check=False
        )
        if result.returncode or result.stdout.strip() != "commit":
            raise ValueError("source baseline is not an existing commit")


def _keys(value: Any, required: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("invalid registry structure")


def _sha(value: Any, length: int = 64) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{" + str(length) + "}", value):
        raise ValueError("invalid source identity")


def validate_record(record: Any, reference: Mapping[str, Any]) -> None:
    _keys(
        record,
        {"version", "source_commit", "effective_at", "rollback", "sources", "source_version", "model", "config_basis"},
    )
    if not isinstance(record["version"], str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", record["version"]
    ):
        raise ValueError("invalid version")
    _sha(record["source_commit"], 40)
    if not isinstance(record["effective_at"], str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", record["effective_at"]
    ):
        raise ValueError("effective_at must be UTC")
    effective = datetime.fromisoformat(record["effective_at"].replace("Z", "+00:00"))
    if effective.tzinfo is None or "T" not in record["effective_at"]:
        raise ValueError("effective_at must include UTC time")
    if not isinstance(record["rollback"], str) or not record["rollback"].strip():
        raise ValueError("rollback missing")
    if record["config_basis"] not in ("source-defaults", "explicit-config"):
        raise ValueError("invalid configuration basis")
    sources = record["sources"]
    if not isinstance(sources, dict) or not sources:
        raise ValueError("sources missing")
    for name, sha in sources.items():
        if not isinstance(name, str) or "\\" in name or not name.startswith("app/") or ".." in name.split("/"):
            raise ValueError("invalid source path")
        _sha(sha)
    if record["source_version"] != digest(sources):
        raise ValueError("source fingerprint mismatch")
    model = record["model"]
    _keys(model, {"fields", "endpoint_identities", "version"})
    _keys(model["fields"], set(FIELDS))
    _keys(model["endpoint_identities"], set(ENDPOINTS))
    # Reuse strict scalar/finite validation without introducing raw endpoint values.
    model_snapshot(model["fields"], reference)
    for sha in model["endpoint_identities"].values():
        _sha(sha)
    if model["version"] != digest({"fields": model["fields"], "endpoint_identities": model["endpoint_identities"]}):
        raise ValueError("model fingerprint mismatch")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    def reject_constant(_: str) -> Any:
        raise ValueError("nonfinite JSON number")

    return json.loads(
        path.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object, parse_constant=reject_constant
    )


def read_registry(root: Path, path: Path) -> dict[str, Any]:
    registry = load_json(path)
    _keys(registry, {"schema_version", "current", "versions"})
    if type(registry["schema_version"]) is not int or registry["schema_version"] != 1:
        raise ValueError("unsupported schema")
    if not isinstance(registry["versions"], list) or not registry["versions"]:
        raise ValueError("versions missing")
    versions = set()
    for record in registry["versions"]:
        validate_record(record, defaults(root))
        verify_source_commit(root, record["source_commit"])
        if record["version"] in versions:
            raise ValueError("duplicate version")
        versions.add(record["version"])
    if not isinstance(registry["current"], str) or registry["current"] not in versions:
        raise ValueError("current version missing")
    return registry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "snapshot", "export-config"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--version")
    parser.add_argument("--source-commit")
    parser.add_argument("--effective-at")
    parser.add_argument("--rollback")
    parser.add_argument("--config", type=Path)
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        if args.command == "snapshot":
            if not all((args.version, args.source_commit, args.effective_at, args.rollback)):
                raise ValueError("snapshot requires version/source-commit/effective-at/rollback")
            config = load_json(args.config) if args.config else None
            if config is not None and not isinstance(config, dict):
                raise ValueError("configuration must be an object")
            output = make_record(
                root,
                version=args.version,
                source_commit=args.source_commit,
                effective_at=args.effective_at,
                rollback=args.rollback,
                config=config,
            )
        else:
            registry = read_registry(root, args.registry or root / REGISTRY)
            version = args.version or registry["current"]
            matches = [item for item in registry["versions"] if item["version"] == version]
            if not matches:
                raise ValueError("unknown version")
            record = matches[0]
            if args.command == "export-config":
                output = record["model"]["fields"]
            else:
                if source_inventory(root) != record["sources"]:
                    raise ValueError("source drift: register change or restore selected sources")
                candidate = (
                    model_snapshot(load_json(args.config), defaults(root))
                    if args.config
                    else model_snapshot({}, defaults(root))
                )
                if candidate != record["model"]:
                    raise ValueError("model drift: supply matching explicit configuration/secret-managed endpoints")
                output = {
                    "status": "ok",
                    "version": version,
                    "source_version": record["source_version"],
                    "model_version": record["model"]["version"],
                    "config_basis": record["config_basis"],
                }
        print(json.dumps(output, indent=2, ensure_ascii=True, allow_nan=False))
        return 0
    except (OSError, ValueError, TypeError, SyntaxError, KeyError, OverflowError, subprocess.SubprocessError):
        # Never echo user configuration, raw JSON, paths, endpoint, or parser exceptions.
        print("registry operation failed: invalid/missing metadata, configuration, or source drift", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
