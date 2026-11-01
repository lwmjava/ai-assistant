"""Fetch bounded public HTTP resources using verified connection addresses."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from app.agents.tools.parameter_policy import ParameterRejectedError

MAX_REDIRECTS = 3
MAX_RESPONSE_BYTES = 262144


def public_url(value: str) -> httpx.URL:
    """Validate syntax without resolving or connecting to a destination."""
    if not isinstance(value, str) or not value or len(value) > 2048 or any(ord(c) <= 32 for c in value):
        raise ParameterRejectedError("invalid_url")
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ParameterRejectedError("invalid_url")
        if parsed.port not in {None, 80, 443} or "\\" in value or "%" in parsed.hostname:
            raise ParameterRejectedError("invalid_url")
        url = httpx.URL(value)
        host = url.host.rstrip(".").lower()
        if not host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            raise ParameterRejectedError("private_destination")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if "." not in host:
                raise ParameterRejectedError("invalid_url") from None
        else:
            _public_ip(address)
        return url
    except (ValueError, httpx.InvalidURL) as exc:
        if isinstance(exc, ParameterRejectedError):
            raise
        raise ParameterRejectedError("invalid_url") from exc


def _public_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if (
        not address.is_global
        or address.is_multicast
        or address.is_reserved
        or isinstance(address, ipaddress.IPv6Address)
        and (address.sixtofour or address.teredo)
    ):
        raise ParameterRejectedError("private_destination")


def fetch_parameters(arguments: dict) -> None:
    """Validate the public fetch capability's declared inputs."""
    if arguments.get("url"):
        public_url(arguments["url"])
    maximum = arguments.get("max_chars", 2000)
    if type(maximum) is not int or not 1 <= maximum <= 20000:
        raise ParameterRejectedError("invalid_arguments")


async def resolve_public(url: httpx.URL) -> str:
    """Reject mixed public/private answers and pin one verified address."""
    try:
        literal = ipaddress.ip_address(url.host)
    except ValueError:
        answers = await asyncio.to_thread(
            socket.getaddrinfo, url.host, url.port or (443 if url.scheme == "https" else 80), 0, socket.SOCK_STREAM
        )
        if not answers or len(answers) > 64:
            raise ParameterRejectedError("invalid_destination")
        addresses = []
        for answer in answers:
            address = ipaddress.ip_address(answer[4][0])
            _public_ip(address)
            addresses.append(str(address))
        return addresses[0]
    else:
        _public_ip(literal)
        return str(literal)


@dataclass(frozen=True)
class FetchedPage:
    """Bounded response bytes and transport completeness, before text parsing."""

    status_code: int
    content: bytes
    content_type: str = "text/plain"
    encoding: str = "utf-8"
    download_truncated: bool = False


async def fetch_page(value: str) -> FetchedPage:
    """Validate each redirect and retain the original TLS server name."""
    url = public_url(value)
    async with asyncio.timeout(10):
        for hop in range(MAX_REDIRECTS + 1):
            address = await resolve_public(url)
            target = url.copy_with(host=address)
            # A new client per hop prevents cross-origin cookie/auth reuse.
            async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
                async with client.stream(
                    "GET",
                    target,
                    headers={"Host": url.netloc.decode("ascii"), "Accept-Encoding": "identity"},
                    extensions={"sni_hostname": url.host},
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location or hop == MAX_REDIRECTS:
                            raise ParameterRejectedError("redirect_limit")
                        next_url = public_url(urljoin(str(url), location))
                        if url.scheme == "https" and next_url.scheme != "https":
                            raise ParameterRejectedError("invalid_redirect")
                        url = next_url
                        continue
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise ParameterRejectedError("unsupported_encoding")
                    payload = bytearray()
                    truncated = False
                    async for chunk in response.aiter_bytes(chunk_size=16384):
                        remaining = MAX_RESPONSE_BYTES - len(payload)
                        payload.extend(chunk[:remaining])
                        if len(chunk) > remaining:
                            truncated = True
                            break
                    return FetchedPage(
                        status_code=response.status_code,
                        content=bytes(payload),
                        content_type=response.headers.get("content-type", ""),
                        encoding=response.charset_encoding or "utf-8",
                        download_truncated=truncated,
                    )
    raise ParameterRejectedError("redirect_limit")


async def fetch_public(value: str, maximum: int) -> tuple[int, str]:
    """Retain the bounded raw-text tuple interface for existing callers."""
    page = await fetch_page(value)
    return page.status_code, page.content.decode(page.encoding, errors="replace")[:maximum]
