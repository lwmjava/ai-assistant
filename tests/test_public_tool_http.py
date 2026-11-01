"""Public fetches validate addresses at every hop without real network calls."""

import json
import socket

import httpx
import pytest

from app.agents.tools.builtin import web_fetch


@pytest.fixture
def network(monkeypatch):
    requests = []
    routes = {}
    addresses = {"public.example": ["93.184.216.34"], "mixed.example": ["93.184.216.34", "127.0.0.1"]}

    def resolve(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
                for ip in addresses.get(host, [host])]

    def handler(request):
        requests.append(request)
        return routes.get(request.headers.get("host"), lambda _: httpx.Response(200, text="public text"))(request)

    client = httpx.AsyncClient
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))
    return requests, routes


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://169.254.169.254/latest", "http://[::1]/",
                                     "http://mixed.example/",
                                     "http://user:password@public.example/",  # pragma: allowlist secret
                                     ])
async def test_private_or_credential_targets_never_connect(network, url):
    requests, _ = network
    result = await web_fetch({"url": url})
    assert getattr(result, "status", "ok") in {"invalid", "denied", "error"}
    assert requests == []
    assert "password" not in result


async def test_redirect_to_private_target_is_not_followed(network):
    requests, routes = network
    routes["public.example"] = lambda _: httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
    result = await web_fetch({"url": "https://public.example/"})
    assert getattr(result, "status", "ok") != "ok"
    assert len(requests) == 1


async def test_public_connection_is_pinned_and_tls_hostname_preserved(network):
    requests, _ = network
    result = await web_fetch({"url": "https://public.example/", "max_chars": 6})
    assert "public" in result
    assert len(requests) == 1
    assert requests[0].url.host == "93.184.216.34"
    assert requests[0].headers["host"] == "public.example"
    assert requests[0].extensions["sni_hostname"] == "public.example"


async def test_public_redirect_is_verified_and_followed(network):
    requests, routes = network
    routes["public.example"] = lambda request: (
        httpx.Response(302, headers={"location": "/next"}) if request.url.path == "/"
        else httpx.Response(200, text="redirected"))
    result = await web_fetch({"url": "https://public.example/"})
    assert "redirected" in result and len(requests) == 2
    assert all(request.url.host == "93.184.216.34" for request in requests)


async def test_redirect_loop_stops_at_limit(network):
    requests, routes = network
    routes["public.example"] = lambda _: httpx.Response(302, headers={"location": "/again"})
    result = await web_fetch({"url": "https://public.example/"})
    assert result.status == "invalid" and len(requests) == 4


async def test_redirect_dns_rebinding_is_rejected(network, monkeypatch):
    requests, routes = network
    resolutions = 0

    def resolve(*args, **kwargs):
        nonlocal resolutions
        resolutions += 1
        address = "93.184.216.34" if resolutions == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    routes["public.example"] = lambda _: httpx.Response(302, headers={"location": "/next"})
    result = await web_fetch({"url": "https://public.example/"})
    assert result.status == "invalid" and len(requests) == 1


async def test_large_body_is_read_as_bounded_prefix(network):
    requests, routes = network
    routes["public.example"] = lambda _: httpx.Response(200, text="x" * 500000)
    result = await web_fetch({"url": "https://public.example/", "max_chars": 20})
    page = json.loads(result)
    assert page["text"] == "x" * 20 and page["characters"] == 20 and len(requests) == 1
    assert page["download_truncated"] and not page["complete"]


async def test_compressed_body_is_rejected_before_read(network):
    _, routes = network
    reads = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            reads.append(True)
            yield b"untrusted compressed bytes"

    routes["public.example"] = lambda _: httpx.Response(
        200, headers={"content-encoding": "gzip"}, stream=Body())
    result = await web_fetch({"url": "https://public.example/"})
    assert result.status == "invalid" and not reads


async def test_real_transport_uses_verified_ip_and_original_tls_name(monkeypatch):
    import ssl

    from httpcore._backends.anyio import AnyIOBackend

    from app.security.public_http import fetch_public

    events = []

    class Stream:
        async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            events.append(("tls", server_hostname))
            assert ssl_context.check_hostname and ssl_context.verify_mode == ssl.CERT_REQUIRED
            return self

        async def read(self, max_bytes, timeout=None):
            return b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"

        async def write(self, buffer, timeout=None):
            events.append(("write", bytes(buffer)))

        async def aclose(self):
            pass

        def get_extra_info(self, name):
            return None

    async def connect(self, host, port, **kwargs):
        events.append(("connect", host, port))
        return Stream()

    monkeypatch.setattr(AnyIOBackend, "connect_tcp", connect)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])
    assert await fetch_public("https://public.example/", 20) == (200, "ok")
    assert ("connect", "93.184.216.34", 443) in events
    assert ("tls", "public.example") in events
    assert any(event[0] == "write" and b"Host: public.example" in event[1] for event in events)
