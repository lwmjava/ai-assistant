"""ENV-001 example contract; never imports application or reads the real .env."""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import logging
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from dotenv import dotenv_values
from dotenv.parser import parse_stream
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "app/core/config.py"
EXAMPLE = ROOT / ".env.example"
ASSIGN = re.compile(r"^\s*([A-Za-z_][A-Za-z_0-9]*)\s*=")
LABEL = re.compile(r"^# \[(选填|条件必填)\] ([A-Z_0-9]+)(?:：.*)?$")


def source_fields() -> tuple[ast.Module, list[str]]:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Settings")
    fields = [node.target.id for node in cls.body
              if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)]
    return tree, fields


def isolated_settings() -> type[BaseSettings]:
    tree, fields = source_fields()
    jitter = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == "RETRY_BACKOFF_JITTER_RATIO"
                          for target in node.targets))
    nodes = [node for node in tree.body if
             (isinstance(node, ast.FunctionDef) and node.name == "retrieval_retry_effectively_disabled")
             or (isinstance(node, ast.ClassDef) and node.name == "Settings")]
    namespace = {"BaseSettings": BaseSettings, "SettingsConfigDict": SettingsConfigDict,
                 "Field": Field, "field_validator": field_validator, "model_validator": model_validator,
                 "_ENV_FILE": ROOT / "data/env001-disabled.example",
                 "RETRY_BACKOFF_JITTER_RATIO": ast.literal_eval(jitter),
                 "logger": logging.getLogger("env001-isolated")}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), namespace)
    cls = namespace["Settings"]
    cls.model_config = {**cls.model_config, "env_file": None}
    assert set(cls.model_fields) == set(fields)
    return cls


def allowed_example(path: Path) -> bool:
    resolved = path.resolve()
    return not path.is_symlink() and (resolved == EXAMPLE.resolve() or (
        resolved.is_relative_to(ROOT / "data") and resolved.suffix == ".example"
        and any(part.startswith("env001-") for part in resolved.relative_to(ROOT / "data").parts)))


def check_example(path: Path) -> dict:
    if not allowed_example(path):
        raise ValueError("example_path_out_of_scope")
    _, fields = source_fields()
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    keys = []
    labeled = set()
    for index, line in enumerate(lines):
        match = ASSIGN.match(line)
        if match:
            name = match.group(1)
            keys.append(name)
            label = LABEL.fullmatch(lines[index - 1]) if index else None
            if label and label.group(2) == name:
                labeled.add(name)
    syntax_errors = [binding.original.line for binding in parse_stream(io.StringIO("\n".join(lines)))
                     if binding.error]
    values = dotenv_values(path, interpolate=False) if not syntax_errors else {}
    key_set = set(keys)
    secret_names = [key for key in fields if key.endswith("_API_KEY") or key in {"JWT_SECRET_KEY", "MILVUS_TOKEN"}]
    result = {"fields": len(fields), "keys": len(keys), "matched": len(set(fields) & key_set),
              "missing": sorted(set(fields) - key_set), "extra": sorted(key_set - set(fields)),
              "duplicates": sorted(key for key, count in Counter(keys).items() if count > 1),
              "unlabeled": sorted(key_set - labeled), "invalid_dotenv_keys": sorted(key_set ^ set(values)),
              "nonempty_secret_fields": sorted(key for key in secret_names if values.get(key)),
              "syntax_error_lines": syntax_errors,
              "legacy_initial_admin_password_present": bool(values.get("INITIAL_ADMIN_PASSWORD")),
              "legacy_password_governance": "ENV-002 pending; this check is not production-safety acceptance",
              "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
              "example_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    result["passed"] = not any(result[key] for key in (
        "missing", "extra", "duplicates", "unlabeled", "invalid_dotenv_keys", "nonempty_secret_fields",
        "syntax_error_lines"))
    return result


def load_example(path: Path, overrides: dict[str, str] | None = None) -> BaseModel:
    if not allowed_example(path):
        raise ValueError("example_path_out_of_scope")
    cls = isolated_settings()
    # No process configuration or forbidden dotenv file is inherited by this instance.
    with patch.dict(os.environ, overrides or {}, clear=True):
        return cls(_env_file=path, _env_file_encoding="utf-8")


def self_test() -> list[str]:
    original = EXAMPLE.read_bytes()
    model = load_example(EXAMPLE)
    cls = isolated_settings()
    with patch.dict(os.environ, {}, clear=True):
        defaults = cls(_env_file=None)
    checks = []
    assert len(type(model).model_fields) == 181
    assert model.model_dump()["RAG_REFUSE_MESSAGE"] == defaults.model_dump()["RAG_REFUSE_MESSAGE"]
    assert load_example(EXAMPLE, {"PORT": "8123"}).model_dump()["PORT"] == 8123
    checks.extend(["real_settings_load_181", "unicode_default_roundtrip", "process_override_priority"])
    assert check_example(EXAMPLE)["passed"]
    checks.append("complete_labeled_keys")
    with tempfile.TemporaryDirectory(prefix="env001-fixtures-", dir=ROOT / "data") as folder:
        target = Path(folder) / "changed.example"
        text = original.decode("utf-8-sig").replace("\r\n", "\n")
        for name, mutation in {
            "missing": re.sub(r"^MEMORY_ENABLED=.*\n", "", text, flags=re.M),
            "extra": text + "\n# [选填] UNKNOWN_FIELD\nUNKNOWN_FIELD=true\n",
            "duplicate": text + "\n# [选填] PORT\nPORT=8000\n",
            "unlabeled": text.replace("# [选填] DEBUG\n", ""),
            "nonempty_secret": text.replace("LLM_API_KEY=\n", "LLM_API_KEY=synthetic-only\n"),
            "syntax": text + "\nNOT VALID DOTENV SYNTAX\n",
        }.items():
            target.write_text(mutation, encoding="utf-8")
            assert not check_example(target)["passed"], name
            checks.append(name + "_rejected")
        for value in ("nan", "inf", "1e309", "false"):
            try:
                load_example(EXAMPLE, {"CACHE_TTL_SECONDS": value})
            except ValidationError:
                continue
            raise AssertionError("invalid_budget_accepted")
        checks.append("nonfinite_and_invalid_numeric_rejected")
        try:
            load_example(EXAMPLE, {"MEMORY_ENABLED": "not-a-boolean"})
        except ValidationError:
            checks.append("invalid_boolean_rejected")
        else:
            raise AssertionError("invalid_boolean_accepted")
    try:
        check_example(ROOT / ".env")
    except ValueError:
        checks.append("private_dotenv_path_rejected_before_read")
    else:
        raise AssertionError("private_dotenv_access")
    assert EXAMPLE.read_bytes() == original
    assert check_example(EXAMPLE) == check_example(EXAMPLE)
    checks.append("repeat_read_no_mutation")
    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--example", type=Path, default=EXAMPLE)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    result = check_example(args.example)
    if result["passed"]:
        load_example(args.example)
    if args.self_test:
        result["checks"] = self_test()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
