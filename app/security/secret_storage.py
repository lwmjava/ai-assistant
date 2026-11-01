"""Authenticated, purpose-bound encryption for persisted provider credentials."""
from __future__ import annotations

import json

from cryptography.fernet import Fernet, InvalidToken

PREFIX = "enc:fernet:v1:"
MASTER_KEY_ENV = "APP_SECRET_ENCRYPTION_KEY"
API_KEY_FIELDS = (
    "LLM_API_KEY", "LLM_FALLBACK_API_KEY", "LLM_INTENT_API_KEY", "LLM_RERANK_API_KEY",
    "EMBEDDING_API_KEY", "RAG_OCR_API_KEY", "SEARCH_DASHSCOPE_API_KEY", "SEARCH_ZHIPU_API_KEY",
    "SEARCH_BOCHA_API_KEY", "SEARCH_BAIDU_API_KEY",
)


class SecretStorageError(RuntimeError):
    """A credential could not be safely loaded; messages never contain input values."""


def _cipher(key: str) -> Fernet:
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError, UnicodeError):
        raise SecretStorageError("Secret encryption key is missing or invalid") from None


def encrypt_secret(field: str, value: str, key: str) -> str:
    if field not in API_KEY_FIELDS:
        raise SecretStorageError("Unsupported credential field")
    payload = json.dumps({"version": 1, "field": field, "value": value}).encode("utf-8")
    return PREFIX + _cipher(key).encrypt(payload).decode("ascii")


def decrypt_secret(field: str, value: str, key: str) -> str:
    if field not in API_KEY_FIELDS or not value.startswith(PREFIX):
        raise SecretStorageError("Unsupported encrypted credential format")
    cipher = _cipher(key)
    try:
        payload = json.loads(cipher.decrypt(value[len(PREFIX):].encode("ascii")))
        if (not isinstance(payload, dict) or set(payload) != {"version", "field", "value"}
                or payload["version"] != 1 or payload["field"] != field
                or not isinstance(payload["value"], str)):
            raise ValueError
        return payload["value"]
    except (InvalidToken, ValueError, TypeError, UnicodeError, KeyError):
        raise SecretStorageError("Encrypted credential authentication failed") from None
