"""SEC-006：日志侧身份证、手机号、银行卡脱敏。"""

import io
import logging

from app.security.log_redaction import install_log_redaction
from app.security.log_sanitizer import LogSanitizer

ID_CARD = "110101199001011234"
PHONE = "13800138000"
BANK = "6222021234567890123"


def test_log_sanitizer_redacts_id_phone_bank() -> None:
    sanitizer = LogSanitizer()
    text = sanitizer.sanitize(f"用户 {ID_CARD} 手机 {PHONE} 卡号 {BANK}")
    assert ID_CARD not in text
    assert PHONE not in text
    assert BANK not in text
    assert text.count("***") >= 3


def test_log_sanitizer_keeps_hex_version_hashes() -> None:
    sanitizer = LogSanitizer()
    version = "1.0#sha256:e87edb2efae6cac0006afe5b27dca523b3ad34679fedc3643214368238484844"
    text = sanitizer.sanitize(f"skill_activated effective_version={version} origin=builtin")
    assert version in text


def test_log_filter_redacts_pii_in_message() -> None:
    install_log_redaction()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("app.security.test_sec006")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        logger.info("id=%s phone=%s bank=%s", ID_CARD, PHONE, BANK)
    finally:
        logger.removeHandler(handler)
    out = stream.getvalue()
    assert ID_CARD not in out
    assert PHONE not in out
    assert BANK not in out
