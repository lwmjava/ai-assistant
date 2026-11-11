"""Static and isolated checks for the HTTPS reverse proxy and streaming settings."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from http.client import HTTPSConnection
from pathlib import Path

HEARTBEAT_SECONDS = 15.0
SSE_TIMEOUT_SECONDS = 60
TIMEOUT_RATIO = 4
SSE_PATHS = ("/api/v1/chat/stream", "/api/v1/runs/[^/]+/events")
REQUIRED_HEADERS = {
    "strict-transport-security": "max-age=31536000; includeSubDomains",
    "x-content-type-options": "nosniff",
}
CSP_TOKENS = (
    "default-src 'self'",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "connect-src 'self'",
)
DRILL_PROJECT = "assistant-proxy-sse-drill"


def repo_root(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit.resolve()
    return Path(__file__).resolve().parents[1]


def read_heartbeat(root: Path) -> float:
    text = (root / "app/services/active_runs.py").read_text(encoding="utf-8")
    match = re.search(r"^HEARTBEAT_SECONDS\s*=\s*([0-9.]+)\s*$", text, re.M)
    if not match:
        raise AssertionError("application heartbeat constant is missing")
    return float(match.group(1))


def _seconds(value: str) -> int:
    match = re.fullmatch(r"(\d+)s?", value.strip())
    if not match:
        raise AssertionError("cannot parse timeout value: " + value)
    return int(match.group(1))


def check_static(root: Path) -> list[str]:
    errors: list[str] = []
    nginx = root / "deploy/nginx/nginx.conf"
    sse = root / "deploy/nginx/sse_proxy.conf"
    overlay = root / "deploy/compose.proxy.yml"
    for path in (nginx, sse, overlay):
        if not path.is_file():
            errors.append("missing file: " + str(path.relative_to(root)))
    if errors:
        return errors

    try:
        heartbeat = read_heartbeat(root)
    except (OSError, AssertionError) as exc:
        errors.append(str(exc))
        heartbeat = HEARTBEAT_SECONDS
    if heartbeat != HEARTBEAT_SECONDS:
        errors.append(
            f"application heartbeat is {heartbeat}s, expected {HEARTBEAT_SECONDS}s"
        )
    expected_timeout = int(HEARTBEAT_SECONDS * TIMEOUT_RATIO)
    if expected_timeout != SSE_TIMEOUT_SECONDS:
        errors.append("internal timeout alignment constants are inconsistent")

    sse_text = sse.read_text(encoding="utf-8")
    nginx_text = nginx.read_text(encoding="utf-8")
    if "proxy_buffering off" not in sse_text:
        errors.append("streaming location must disable proxy buffering")
    if "proxy_cache off" not in sse_text:
        errors.append("streaming location must disable proxy cache")
    if "gzip off" not in sse_text:
        errors.append("streaming location must disable gzip")
    if "Last-Event-ID" not in sse_text:
        errors.append("streaming location must forward Last-Event-ID")
    if "proxy_pass http://app" not in sse_text:
        errors.append("streaming location must proxy to the app upstream")
    read_timeout = re.search(r"proxy_read_timeout\s+(\S+);", sse_text)
    send_timeout = re.search(r"proxy_send_timeout\s+(\S+);", sse_text)
    if not read_timeout or not send_timeout:
        errors.append("streaming location must set read and send timeouts")
    else:
        try:
            read_value = _seconds(read_timeout.group(1))
            send_value = _seconds(send_timeout.group(1))
        except AssertionError as exc:
            errors.append(str(exc))
        else:
            if read_value != SSE_TIMEOUT_SECONDS or send_value != SSE_TIMEOUT_SECONDS:
                errors.append(
                    "streaming timeout must be "
                    f"{SSE_TIMEOUT_SECONDS}s to stay aligned with the "
                    f"{HEARTBEAT_SECONDS}s heartbeat"
                )
            if read_value < 2 * heartbeat:
                errors.append("streaming timeout must be at least two heartbeat periods")

    for pattern in SSE_PATHS:
        if pattern not in nginx_text or "include /etc/nginx/sse_proxy.conf;" not in nginx_text:
            errors.append("nginx must apply streaming settings to " + pattern)
            break
    if "listen 443 ssl" not in nginx_text:
        errors.append("TLS must be terminated at the proxy on port 443")
    if "ssl_certificate /etc/nginx/certs/tls.crt;" not in nginx_text:
        errors.append("proxy must use operator-provided TLS certificate files")
    if "ssl_certificate_key /etc/nginx/certs/tls.key;" not in nginx_text:
        errors.append("proxy must use operator-provided TLS private key files")
    if "TLSv1.2" not in nginx_text or "TLSv1.3" not in nginx_text:
        errors.append("proxy must allow TLS 1.2 and 1.3")
    if "return 301 https://$host$request_uri;" not in nginx_text:
        errors.append("HTTP must redirect to HTTPS")
    if "server app:8000;" not in nginx_text:
        errors.append("proxy must forward to the application service on port 8000")
    if "Strict-Transport-Security" not in nginx_text or "max-age=31536000" not in nginx_text:
        errors.append("HTTPS responses must include HSTS")
    if "X-Content-Type-Options" not in nginx_text or "nosniff" not in nginx_text:
        errors.append("HTTPS responses must include X-Content-Type-Options")
    if "Content-Security-Policy" not in nginx_text:
        errors.append("HTTPS responses must include Content-Security-Policy")
    else:
        for token in CSP_TOKENS:
            if token not in nginx_text:
                errors.append("CSP is missing required token: " + token)

    try:
        import yaml
    except ImportError:
        errors.append("PyYAML is required to check the compose overlay")
        return errors

    try:
        document = yaml.safe_load(overlay.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        errors.append("proxy compose overlay is not parseable")
        return errors
    services = document.get("services") if isinstance(document, dict) else None
    if not isinstance(services, dict) or set(services) != {"proxy"}:
        errors.append("proxy overlay must only add the proxy service")
        return errors
    proxy = services["proxy"]
    image = str(proxy.get("image") or "")
    if not image.startswith("nginx"):
        errors.append("proxy image must be nginx")
    depends = proxy.get("depends_on") or {}
    if depends.get("app") != {"condition": "service_healthy"}:
        errors.append("proxy must wait for a healthy application")
    ports = proxy.get("ports") or []
    def _publishes(item: object, port_var: str, container_port: str) -> bool:
        text = str(item)
        return (
            "PROXY_BIND_HOST:-127.0.0.1" in text
            and port_var in text
            and text.endswith(":" + container_port)
        )

    if not any(_publishes(item, "PROXY_HTTPS_PORT:-443", "443") for item in ports):
        errors.append("HTTPS port must default to loopback 443")
    if not any(_publishes(item, "PROXY_HTTP_PORT:-80", "80") for item in ports):
        errors.append("HTTP port must default to loopback 80")
    volumes = [str(item) for item in proxy.get("volumes") or []]
    if "./deploy/nginx/nginx.conf:/etc/nginx/nginx.conf:ro" not in volumes:
        errors.append("overlay must mount the proxy configuration")
    if "./deploy/nginx/sse_proxy.conf:/etc/nginx/sse_proxy.conf:ro" not in volumes:
        errors.append("overlay must mount the streaming settings")
    if not any("TLS_CERT_FILE:?" in item and "tls.crt" in item for item in volumes):
        errors.append("overlay must require an operator-provided TLS certificate")
    if not any("TLS_KEY_FILE:?" in item and "tls.key" in item for item in volumes):
        errors.append("overlay must require an operator-provided TLS private key")
    if proxy.get("restart") != "unless-stopped":
        errors.append("proxy must define a restart policy")
    if not proxy.get("healthcheck"):
        errors.append("proxy must define a health check")
    if not proxy.get("mem_limit") or not proxy.get("cpus"):
        errors.append("proxy must define resource limits")
    return errors


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _compose_cmd(project: str, compose_file: Path, *args: str) -> list[str]:
    return ["docker", "compose", "-p", project, "-f", str(compose_file), *args]


def _run(command: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _docker_path(path: Path) -> str:
    return path.resolve().as_posix()


def _header_map(headers: object) -> dict[str, str]:
    items: dict[str, str] = {}
    for key, value in headers:  # type: ignore[not-iterable]
        items[str(key).lower()] = str(value)
    return items


def _assert_security_headers(headers: dict[str, str], errors: list[str], where: str) -> None:
    for name, expected in REQUIRED_HEADERS.items():
        value = headers.get(name, "")
        if expected.lower() not in value.lower():
            errors.append(f"{where} missing {name}")
    policy = headers.get("content-security-policy", "")
    if not policy:
        errors.append(where + " missing content-security-policy")
        return
    for token in CSP_TOKENS:
        if token not in policy:
            errors.append(f"{where} CSP missing {token}")


def _read_stream(
    host: str,
    port: int,
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    deadline: float = 30.0,
    on_bytes: Callable[[bytes, float], None] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    context = ssl._create_unverified_context()
    connection = HTTPSConnection(host, port, context=context, timeout=deadline)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        chunks = bytearray()
        started = time.monotonic()
        while True:
            remaining = deadline - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("timed out reading the streamed response")
            sock = getattr(connection, "sock", None)
            if sock is not None:
                sock.settimeout(max(0.1, remaining))
            reader = getattr(response, "read1", response.read)
            piece = reader(256)
            if not piece:
                break
            chunks.extend(piece)
            if on_bytes is not None:
                on_bytes(bytes(chunks), time.monotonic() - started)
        return response.status, _header_map(response.getheaders()), bytes(chunks)
    finally:
        connection.close()


def run_drill(root: Path) -> list[str]:
    errors: list[str] = []
    if shutil.which("docker") is None:
        return ["docker is not available for the isolated proxy drill"]
    if shutil.which("openssl") is None:
        return ["openssl is not available to create a temporary drill certificate"]

    work = Path(tempfile.mkdtemp(prefix="proxy-sse-drill-"))
    compose_file = work / "compose.yml"
    cert = work / "tls.crt"
    key = work / "tls.key"
    https_port = _free_port()
    http_port = _free_port()
    started = False
    try:
        openssl_cnf = work / "openssl.cnf"
        openssl_cnf.write_text(
            "\n".join(
                [
                    "[req]",
                    "distinguished_name = req_distinguished_name",
                    "x509_extensions = v3_req",
                    "prompt = no",
                    "[req_distinguished_name]",
                    "CN = localhost",
                    "[v3_req]",
                    "subjectAltName = DNS:localhost,IP:127.0.0.1",
                    "",
                ]
            ),
            encoding="utf-8",
            newline="\n",
        )
        env = os.environ.copy()
        env["OPENSSL_CONF"] = str(openssl_cnf)
        cert_result = subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-keyout",
                str(key),
                "-out",
                str(cert),
                "-days",
                "1",
                "-nodes",
                "-config",
                str(openssl_cnf),
                "-extensions",
                "v3_req",
                "-subj",
                "/CN=localhost",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        if cert_result.returncode != 0 or not cert.is_file() or not key.is_file():
            detail = (cert_result.stderr or cert_result.stdout or "").strip()
            return ["failed to create a temporary drill certificate: " + detail]

        compose_file.write_text(
            "\n".join(
                [
                    "services:",
                    "  app:",
                    "    image: python:3.12-slim",
                    "    command: [\"python\", \"-u\", \"/mock/mock_sse_upstream.py\"]",
                    "    volumes:",
                    f"      - {_docker_path(root / 'deploy/nginx/mock_sse_upstream.py')}:/mock/mock_sse_upstream.py:ro",
                    "    expose:",
                    "      - \"8000\"",
                    "  proxy:",
                    "    image: nginx:alpine",
                    "    depends_on:",
                    "      - app",
                    "    ports:",
                    f"      - \"127.0.0.1:{https_port}:443\"",
                    f"      - \"127.0.0.1:{http_port}:80\"",
                    "    volumes:",
                    f"      - {_docker_path(root / 'deploy/nginx/nginx.conf')}:/etc/nginx/nginx.conf:ro",
                    f"      - {_docker_path(root / 'deploy/nginx/sse_proxy.conf')}:/etc/nginx/sse_proxy.conf:ro",
                    f"      - {_docker_path(cert)}:/etc/nginx/certs/tls.crt:ro",
                    f"      - {_docker_path(key)}:/etc/nginx/certs/tls.key:ro",
                ]
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

        up = _run(_compose_cmd(DRILL_PROJECT, compose_file, "up", "-d"), timeout=120)
        if up.returncode != 0:
            detail = (up.stderr or up.stdout or "").strip()
            return ["failed to start the isolated proxy drill: " + detail]
        started = True

        ready = False
        for _ in range(40):
            try:
                status, headers, _body = _read_stream(
                    "127.0.0.1",
                    https_port,
                    "GET",
                    "/",
                    deadline=3,
                )
            except (OSError, ssl.SSLError, TimeoutError):
                time.sleep(0.5)
                continue
            if status == 200:
                _assert_security_headers(headers, errors, "GET /")
                ready = True
                break
            time.sleep(0.5)
        if not ready:
            return ["isolated proxy did not become reachable on HTTPS"]

        redirect = _run(
            [
                "curl",
                "-sS",
                "-D",
                "-",
                "-o",
                os.devnull,
                "--max-redirs",
                "0",
                f"http://127.0.0.1:{http_port}/",
            ],
            timeout=10,
        )
        if redirect.returncode not in {0, 47} and "301" not in (redirect.stdout or ""):
            # curl 47 is max redirects; still may have printed headers
            pass
        redirect_out = (redirect.stdout or "") + (redirect.stderr or "")
        if "301" not in redirect_out or not re.search(r"(?i)location:\s*https://", redirect_out):
            errors.append("HTTP must redirect to HTTPS")

        arrivals: dict[str, float] = {}

        def mark(buffer: bytes, elapsed: float) -> None:
            for name in ("run.started", "token", "heartbeat", "done"):
                if name not in arrivals and f"event: {name}".encode() in buffer:
                    arrivals[name] = elapsed

        status, headers, body = _read_stream(
            "127.0.0.1",
            https_port,
            "POST",
            "/api/v1/chat/stream",
            headers={"Content-Type": "application/json"},
            body=b"{}",
            deadline=20,
            on_bytes=mark,
        )
        if status != 200:
            errors.append(f"POST /api/v1/chat/stream returned {status}")
        if b"text/event-stream" not in (headers.get("content-type", "").encode() + b""):
            if "text/event-stream" not in headers.get("content-type", ""):
                errors.append("stream endpoint must return text/event-stream")
        _assert_security_headers(headers, errors, "POST /api/v1/chat/stream")
        if "run.started" not in arrivals or "token" not in arrivals:
            errors.append("stream events were not received through the proxy")
        elif arrivals["token"] - arrivals["run.started"] < 1.5:
            errors.append(
                "stream events arrived together; proxy buffering may still be enabled"
            )
        if b"event: done" not in body:
            errors.append("stream ended before the final event")

        idle_arrivals: dict[str, float] = {}

        def mark_idle(buffer: bytes, elapsed: float) -> None:
            for name in ("run.started", "token", "heartbeat", "done"):
                if name not in idle_arrivals and f"event: {name}".encode() in buffer:
                    idle_arrivals[name] = elapsed

        idle_status, idle_headers, idle_body = _read_stream(
            "127.0.0.1",
            https_port,
            "POST",
            "/api/v1/chat/stream?idle=20",
            headers={"Content-Type": "application/json"},
            body=b"{}",
            deadline=40,
            on_bytes=mark_idle,
        )
        if idle_status != 200 or "heartbeat" not in idle_arrivals:
            errors.append("proxy closed the stream before a 20s idle heartbeat")
        elif idle_arrivals["heartbeat"] < 18:
            errors.append("idle heartbeat arrived too early to prove timeout alignment")
        _assert_security_headers(idle_headers, errors, "idle stream")
        if b"event: done" not in idle_body:
            errors.append("idle stream ended before the final event")

        replay_status, replay_headers, replay_body = _read_stream(
            "127.0.0.1",
            https_port,
            "GET",
            "/api/v1/runs/demo/events",
            headers={"Last-Event-ID": "cursor-42"},
            deadline=10,
        )
        if replay_status != 200:
            errors.append(f"GET /api/v1/runs/demo/events returned {replay_status}")
        if b"cursor-42" not in replay_body:
            errors.append("Last-Event-ID was not forwarded through the proxy")
        _assert_security_headers(replay_headers, errors, "GET /api/v1/runs/demo/events")
    except (OSError, ssl.SSLError, TimeoutError, subprocess.TimeoutExpired) as exc:
        errors.append("isolated proxy drill failed: " + str(exc))
    finally:
        if started or compose_file.is_file():
            _run(_compose_cmd(DRILL_PROJECT, compose_file, "down", "--remove-orphans"), timeout=120)
        shutil.rmtree(work, ignore_errors=True)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument(
        "command",
        nargs="?",
        choices=("static", "drill", "all"),
        default="all",
    )
    args = parser.parse_args()
    root = repo_root(args.root)
    errors: list[str] = []
    if args.command in {"static", "all"}:
        errors.extend(check_static(root))
    if args.command in {"drill", "all"} and not errors:
        errors.extend(run_drill(root))
    elif args.command == "all" and errors:
        print("static checks failed; isolated drill was not started", file=sys.stderr)
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        return 1
    if args.command == "static":
        print("reverse proxy static checks passed")
    elif args.command == "drill":
        print("isolated reverse proxy streaming drill passed")
    else:
        print("reverse proxy static checks and isolated streaming drill passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
