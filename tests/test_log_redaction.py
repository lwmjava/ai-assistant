"""日志脱敏过滤器，以及登录、上传、对话失败时的日志与响应。"""

import ast
import io
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.security import Role
from app.main import app
from app.models.user import User
from app.security.log_redaction import REDACTED_LOG_MESSAGE, install_log_redaction
from app.security.log_sanitizer import LogSanitizer

PASSWORD = "correct horse battery"
TOKEN = "alpha beta"
JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
    ".dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
)
API_KEY = "sk-" + "a" * 20

_ROOT = Path(__file__).resolve().parents[1]


def _capture_logger(name: str) -> tuple[logging.Logger, io.StringIO, logging.Handler]:
    install_log_redaction()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return logger, stream, handler


def test_log_sanitizer_redacts_spaced_password_and_keeps_neighbor() -> None:
    """无引号口令里的空格后半段也要遮住，旁边的字段留下。"""
    sanitizer = LogSanitizer()
    spaced = sanitizer.sanitize(f"password={PASSWORD}")
    assert spaced == "password=***"
    assert PASSWORD not in spaced

    neighbor = sanitizer.sanitize("password=secret123&user=alice")
    assert "secret123" not in neighbor
    assert "alice" in neighbor

    quoted = sanitizer.sanitize('{"token": "abc123xyz", "user": "alice"}')
    assert "abc123xyz" not in quoted
    assert "alice" in quoted


def test_log_filter_redacts_password_token_bearer_and_api_key() -> None:
    """消息里的口令、令牌、Bearer JWT 和 sk- 长密钥整段换成占位符。"""
    logger, stream, handler = _capture_logger("app.security.test_log_fields")
    try:
        logger.info("password=%s", PASSWORD)
        logger.info("token=%s", TOKEN)
        logger.info("Authorization: Bearer %s", JWT)
        logger.info("key=%s", API_KEY)
    finally:
        logger.removeHandler(handler)
    text = stream.getvalue()
    assert PASSWORD not in text
    assert TOKEN not in text
    assert JWT not in text
    assert "eyJ" not in text
    assert API_KEY not in text
    assert text.count("***") >= 4


def test_log_filter_redacts_secrets_inside_traceback() -> None:
    """回溯文本里的同一密钥也不留下。"""
    logger, stream, handler = _capture_logger("app.security.test_log_traceback")
    secret = f"password={PASSWORD} token={TOKEN} Bearer {JWT} {API_KEY}"
    try:
        try:
            raise RuntimeError(secret)
        except RuntimeError:
            logger.exception("对话失败")
    finally:
        logger.removeHandler(handler)
    text = stream.getvalue()
    assert PASSWORD not in text
    assert TOKEN not in text
    assert JWT not in text
    assert API_KEY not in text
    assert "Traceback" in text


def test_log_filter_failure_drops_original_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """脱敏过程出错时丢掉原文，改写固定占位。"""

    def _boom(self: LogSanitizer, text: str) -> str:
        raise RuntimeError("sanitizer down")

    monkeypatch.setattr(LogSanitizer, "sanitize", _boom)
    logger, stream, handler = _capture_logger("app.security.test_log_failure")
    try:
        logger.info("password=%s", PASSWORD)
    finally:
        logger.removeHandler(handler)
    text = stream.getvalue()
    assert PASSWORD not in text
    assert REDACTED_LOG_MESSAGE in text


def test_log_filter_keeps_uvicorn_access_arguments() -> None:
    """访问日志仍要能拆开五个参数，路径里的令牌被遮住。"""
    install_log_redaction()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)

    class _AccessLike(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            client, method, path, version, status = record.args  # type: ignore[misc]
            return f'{client} - "{method} {path} HTTP/{version}" {status}'

    handler.setFormatter(_AccessLike())
    logger = logging.getLogger("uvicorn.access")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    previous = logger.propagate
    logger.propagate = False
    try:
        logger.info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1:1",
            "GET",
            f"/api?token={TOKEN}",
            "1.1",
            200,
        )
    finally:
        logger.propagate = previous
        logger.removeHandler(handler)
    text = stream.getvalue()
    assert TOKEN not in text
    assert 'GET /api?token=*** HTTP/1.1' in text
    assert text.rstrip().endswith("200")


def test_log_redaction_install_does_not_stack() -> None:
    """重复安装不叠两层过滤器。"""
    install_log_redaction()
    install_log_redaction()
    root = logging.getLogger()
    marked = [item for item in root.filters if getattr(item, "_redacts_log_secrets", False)]
    assert len(marked) == 1


def test_main_installs_log_redaction() -> None:
    """应用模块在创建时调用安装函数。"""
    source = (_ROOT / "app" / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    called = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "install_log_redaction":
                called = True
    assert called


def _writer() -> User:
    return User(
        id="log-redaction-user",
        tenant_id="log-redaction-tenant",
        username="log-redaction",
        hashed_password="",
        role=Role.TENANT_ADMIN.value,
        token_version=0,
        is_active=True,
    )


def test_log_login_failure_hides_password_and_traceback(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """登录失败的日志不含提交的口令，响应正文不含回溯。"""

    def _reject(session: object, username: str, password: str) -> None:
        logging.getLogger("app.services.auth_service").error("登录失败 password=%s", password)
        return None

    monkeypatch.setattr("app.api.routes.auth.authenticate", _reject)
    caplog.set_level(logging.INFO)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/api/auth/login",
        json={"username": "nobody", "password": PASSWORD},
    )
    assert response.status_code == 401
    assert "Traceback" not in response.text
    assert PASSWORD not in response.text
    assert PASSWORD not in caplog.text


def test_log_upload_failure_hides_api_key_and_traceback(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """上传失败会把文件名写入日志；其中的密钥和响应里的回溯都不应出现。"""

    def _disk(*_args: object, **_kwargs: object) -> str:
        raise OSError("disk full")

    monkeypatch.setattr("app.api.routes.rag.save_source_file", _disk)
    app.dependency_overrides[get_current_user] = _writer
    caplog.set_level(logging.INFO)
    filename = f"notes password={PASSWORD} {API_KEY}.txt"
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/api/rag/documents/upload",
            files={"file": (filename, b"hello", "text/plain")},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 500
    assert "Traceback" not in response.text
    assert PASSWORD not in response.text
    assert API_KEY not in response.text
    assert PASSWORD not in caplog.text
    assert API_KEY not in caplog.text


def test_log_chat_failure_hides_token_and_traceback(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """对话失败的日志不含请求里的令牌，响应正文不含回溯。"""

    async def _fail(session: object, user: object, message: str, conversation_id: str | None = None) -> None:
        try:
            raise RuntimeError(f"token={TOKEN} Bearer {JWT}")
        except RuntimeError:
            logging.getLogger("app.services.chat_service").exception("对话失败 message=%s", message)
        raise RuntimeError("pipeline down")

    monkeypatch.setattr("app.api.routes.chat._service.chat", _fail)
    app.dependency_overrides[get_current_user] = _writer
    caplog.set_level(logging.INFO)
    message = f"token={TOKEN} Bearer {JWT}"
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post("/api/chat", json={"message": message})
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 500
    assert "Traceback" not in response.text
    assert TOKEN not in response.text
    assert JWT not in response.text
    assert TOKEN not in caplog.text
    assert JWT not in caplog.text
    assert "eyJ" not in caplog.text
