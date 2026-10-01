"""知识库上传类型与大小的纯函数检查。"""

import pytest

from app.core.config import settings
from app.rag.upload_limits import UploadLimitError, check_upload_limits, parse_allowed_extensions

_ALLOWED = parse_allowed_extensions(settings.RAG_UPLOAD_ALLOWED_EXTENSIONS)


def test_upload_limit_rejects_unknown_extension() -> None:
    with pytest.raises(UploadLimitError) as exc:
        check_upload_limits("data.bin", 3, allowed_extensions=_ALLOWED, max_bytes=10)
    assert exc.value.status_code == 400
    assert "txt" in exc.value.detail


def test_upload_limit_rejects_size_after_type_check() -> None:
    with pytest.raises(UploadLimitError) as exc:
        check_upload_limits("note.txt", 11, allowed_extensions=_ALLOWED, max_bytes=10)
    assert exc.value.status_code == 413
    assert "上限" in exc.value.detail


def test_upload_limit_allows_exact_size_and_uppercase_extension() -> None:
    check_upload_limits("NOTE.TXT", 10, allowed_extensions=_ALLOWED, max_bytes=10)


def test_upload_limit_default_message_uses_ten_megabytes() -> None:
    with pytest.raises(UploadLimitError) as exc:
        check_upload_limits(
            "big.txt",
            10 * 1024 * 1024 + 1,
            allowed_extensions=_ALLOWED,
            max_bytes=10 * 1024 * 1024,
        )
    assert exc.value.detail == "文件超过 10MB 上限"


def test_upload_limit_same_file_reports_type_before_size() -> None:
    with pytest.raises(UploadLimitError) as exc:
        check_upload_limits("data.bin", 10_000, allowed_extensions=_ALLOWED, max_bytes=4)
    assert exc.value.status_code == 400
