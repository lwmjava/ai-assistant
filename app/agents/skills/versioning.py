"""Bounded canonical content identity and explicit persisted release validation.

Content identity is not an authorization credential. The release gate binds an
explicit directory to its ledger; runtime registration does not imply a release.
"""

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from app.agents.skills.base import SkillManifest, SkillMode, SkillTrigger, TriggerType

MAX_MANIFEST_BYTES = 131_072
MAX_LEDGER_BYTES = 1_048_576


def _text(value: Any, limit: int, *, empty: bool = True) -> str:
    if not isinstance(value, str) or (not empty and not value):
        raise ValueError("skill_invalid_text")
    try:
        if len(value.encode("utf-8")) > limit:
            raise ValueError("skill_input_too_large")
    except UnicodeError:
        raise ValueError("skill_invalid_encoding") from None
    return value


def _version(value: Any) -> tuple[int, int, int]:
    text = _text(value, 32, empty=False)
    if re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*))?", text) is None:
        raise ValueError("skill_invalid_version")
    parts = [int(part) for part in text.split(".")]
    return parts[0], parts[1], parts[2] if len(parts) == 3 else 0


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > 128:
        raise ValueError("skill_invalid_collection")
    return [_text(item, 256, empty=False) for item in value]


def _payload(manifest: SkillManifest) -> dict[str, Any]:
    if not isinstance(manifest, SkillManifest):
        raise ValueError("skill_invalid_manifest")
    name = _text(manifest.name, 64, empty=False)
    if re.fullmatch(r"[a-z0-9_-]+", name) is None:
        raise ValueError("skill_invalid_name")
    _version(manifest.version)
    if not isinstance(manifest.mode, SkillMode) or not isinstance(manifest.trigger, SkillTrigger):
        raise ValueError("skill_invalid_mode_or_trigger")
    trigger = manifest.trigger
    if not isinstance(trigger.type, TriggerType):
        raise ValueError("skill_invalid_trigger")
    confidence = trigger.min_confidence
    if (isinstance(confidence, bool) or not isinstance(confidence, int | float)
            or not 0 <= confidence <= 1 or not math.isfinite(confidence)):
        raise ValueError("skill_invalid_confidence")
    regex = _text(trigger.regex, 8192)
    try:
        re.compile(regex)
    except (re.error, RecursionError, OverflowError):
        raise ValueError("skill_invalid_regex") from None
    if type(manifest.enabled) is not bool:
        raise ValueError("skill_invalid_enabled")
    if _text(manifest.origin, 32) not in ("builtin", "private", "global"):
        raise ValueError("skill_invalid_origin")
    _text(manifest.source_path, 8192)
    return {
        "schema": "skill-content-canonical-v1",
        "name": name,
        "description": _text(manifest.description, 8192),
        "mode": manifest.mode.value,
        "trigger": {
            "type": trigger.type.value, "keywords": _strings(trigger.keywords),
            "regex": regex, "min_confidence": float(confidence) if confidence else 0.0,
        },
        "system_prompt": _text(manifest.system_prompt, 65536),
        "tools": _strings(manifest.tools), "models": _strings(manifest.models),
        "enabled": manifest.enabled,
    }


def _canonical(manifest: SkillManifest) -> bytes:
    encoded = json.dumps(_payload(manifest), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise ValueError("skill_input_too_large")
    return encoded


def validate_manifest(manifest: SkillManifest) -> None:
    """Reject invalid direct manifests using fixed, non-sensitive error codes."""
    _canonical(manifest)


def content_revision(manifest: SkillManifest) -> str:
    """SHA256 of canonical-v1 behavior, excluding release, origin and path."""
    return hashlib.sha256(_canonical(manifest)).hexdigest()


def effective_version(manifest: SkillManifest) -> str:
    return f"{manifest.version}#sha256:{content_revision(manifest)}"


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("skill_ledger_duplicate_key")
        result[key] = value
    return result


def _ledger_skills(ledger: Any) -> dict[str, list[dict[str, Any]]]:
    if (not isinstance(ledger, dict) or set(ledger) != {"schema_version", "skills"}
            or type(ledger["schema_version"]) is not int or ledger["schema_version"] != 1
            or not isinstance(ledger["skills"], dict) or len(ledger["skills"]) > 256):
        raise ValueError("skill_invalid_ledger")
    result: dict[str, list[dict[str, Any]]] = {}
    for name, entries in ledger["skills"].items():
        if re.fullmatch(r"[a-z0-9_-]+", _text(name, 64, empty=False)) is None:
            raise ValueError("skill_invalid_ledger")
        if not isinstance(entries, list) or not entries or len(entries) > 1000:
            raise ValueError("skill_invalid_ledger")
        last = None
        last_revision = None
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"version", "content_revision", "summary"}:
                raise ValueError("skill_invalid_ledger")
            current = _version(entry["version"])
            if last is not None and current <= last:
                raise ValueError("skill_ledger_version_order")
            last = current
            if re.fullmatch(r"[0-9a-f]{64}", _text(entry["content_revision"], 64)) is None:
                raise ValueError("skill_ledger_invalid_revision")
            if last_revision == entry["content_revision"]:
                raise ValueError("skill_ledger_content_unchanged")
            last_revision = entry["content_revision"]
            _text(entry["summary"], 1024, empty=False)
        result[name] = entries
    return result


def validate_release_ledger(
    directory: Path, ledger_path: Path, previous_ledger: dict[str, Any] | None = None,
) -> None:
    """Validate actual YAML (including disabled skills) and append-only history.

    Historical integrity requires a trusted previous ledger or Git review.
    Neither file nor a content hash constitutes a tamper-proof audit log.
    """
    from app.agents.skills.loader import load_skill_from_yaml

    try:
        directory = Path(directory)
        ledger_path = Path(ledger_path)
        if not directory.is_dir() or ledger_path.resolve().parent != directory.resolve():
            raise ValueError("skill_ledger_directory_mismatch")
        if ledger_path.stat().st_size > MAX_LEDGER_BYTES:
            raise ValueError("skill_ledger_too_large")
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
        skills = _ledger_skills(ledger)
        if previous_ledger is not None:
            previous = _ledger_skills(previous_ledger)
            for name, entries in previous.items():
                if name not in skills or skills[name][:len(entries)] != entries:
                    raise ValueError("skill_ledger_history_rewritten")
        files = sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml"))
        if len(files) > 256:
            raise ValueError("skill_ledger_too_large")
        actual: dict[str, SkillManifest] = {}
        for file in files:
            if file.stat().st_size > MAX_MANIFEST_BYTES:
                raise ValueError("skill_input_too_large")
            manifest = load_skill_from_yaml(file.read_text(encoding="utf-8"))
            validate_manifest(manifest)
            if manifest.name in actual:
                raise ValueError("skill_ledger_duplicate_skill")
            actual[manifest.name] = manifest
        if set(actual) != set(skills):
            raise ValueError("skill_ledger_skill_set_mismatch")
        for name, manifest in actual.items():
            latest = skills[name][-1]
            if latest["version"] != manifest.version or latest["content_revision"] != content_revision(manifest):
                raise ValueError("skill_ledger_current_mismatch")
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("skill_ledger_read_failed") from None
