"""验证离线回归边界，不向外部主机发起任何解析或连接。"""

import socket

import pytest

from app.core.config import settings


def test_host_credentials_are_not_inherited() -> None:
    assert settings.LLM_API_KEY == ""
    assert settings.LLM_INTENT_API_KEY == ""
    assert settings.LLM_FALLBACK_API_KEY == ""
    assert settings.EMBEDDING_API_KEY == ""


def test_external_dns_and_connect_are_rejected_before_io() -> None:
    with pytest.raises(AssertionError, match="external DNS"):
        socket.getaddrinfo("offline-denied.invalid", 443)
    with socket.socket() as sock, pytest.raises(AssertionError, match="non-loopback"):
        sock.connect(("192.0.2.1", 443))


def test_loopback_lookup_remains_available() -> None:
    assert socket.getaddrinfo("127.0.0.1", 8000)
