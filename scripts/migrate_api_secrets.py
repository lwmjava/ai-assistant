"""Create an encrypted dotenv copy without modifying the source file."""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv.parser import parse_stream

from app.security.secret_storage import (
    API_KEY_FIELDS,
    MASTER_KEY_ENV,
    SecretStorageError,
    decrypt_secret,
    encrypt_secret,
)


def migrate(source: Path, target: Path) -> None:
    if source.is_symlink() or target.exists() or target.is_symlink():
        raise SecretStorageError("Source must be regular and target must not exist")
    if not source.is_file() or source.stat().st_size > 1024 * 1024:
        raise SecretStorageError("Invalid source configuration")
    key = os.environ.get(MASTER_KEY_ENV, "")
    with source.open(encoding="utf-8") as stream:
        bindings = list(parse_stream(stream))
    seen: set[str] = set()
    output: list[str] = []
    for binding in bindings:
        if binding.error:
            raise SecretStorageError("Invalid dotenv syntax")
        name = binding.key
        if name is None:
            output.append(binding.original.string)
            continue
        normalized = name.upper()
        if normalized in seen or normalized == MASTER_KEY_ENV:
            raise SecretStorageError("Duplicate key or colocated encryption key")
        seen.add(normalized)
        value = binding.value
        if value is not None and ("${" in value or "\n" in value or "\r" in value):
            raise SecretStorageError("Interpolation and multiline values are unsupported")
        if normalized in API_KEY_FIELDS and value:
            if value.startswith("enc:"):
                decrypt_secret(normalized, value, key)
                token = value
            else:
                token = encrypt_secret(normalized, value, key)
            output.append(f"{name}={token}\n")
        else:
            output.append(binding.original.string)
    fd, temporary = tempfile.mkstemp(prefix=".encrypted-", dir=target.parent)
    staging = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write("".join(output))
            stream.flush()
            os.fsync(stream.fileno())
        # Publish only a complete file; link refuses an existing target atomically.
        os.link(staging, target)
    finally:
        staging.unlink(missing_ok=True)



def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--target", required=True, type=Path)
    args = parser.parse_args()
    try:
        migrate(args.source, args.target)
    except (SecretStorageError, OSError, UnicodeError):
        print("Credential migration failed; no credential values are displayed", file=sys.stderr)
        return 1
    print("Encrypted copy created; securely retire the plaintext source and backups after verification")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
