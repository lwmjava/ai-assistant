"""SEC-005：上传按文件头校验魔术数字。"""

import pytest

from app.rag.magic_bytes import MagicByteMismatchError, check_magic_bytes
from app.services.chat_attachments import AttachmentInput, AttachmentValidationError, prepare_attachments


def test_pdf_extension_with_text_body_rejected() -> None:
    with pytest.raises(MagicByteMismatchError):
        check_magic_bytes("report.pdf", b"not a pdf")


def test_pdf_extension_with_pdf_header_ok() -> None:
    check_magic_bytes("report.pdf", b"%PDF-1.4 fake")


def test_txt_disguised_as_pdf_header_rejected() -> None:
    with pytest.raises(MagicByteMismatchError):
        check_magic_bytes("notes.txt", b"%PDF-1.4")


def test_unknown_extension_still_not_validated_here() -> None:
    # 扩展名白名单由上层拒绝；本层对未登记类型不额外拦截。
    check_magic_bytes("virus.exe", b"MZ")


def test_chat_attachment_rejects_forged_extension() -> None:
    import base64

    payload = base64.b64encode(b"hello-not-pdf").decode("ascii")
    with pytest.raises(AttachmentValidationError):
        prepare_attachments([AttachmentInput(name="a.pdf", content_base64=payload)])
