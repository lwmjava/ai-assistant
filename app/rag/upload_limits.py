"""知识库上传的扩展名与大小检查。

只看文件名的最后一个后缀和字节长度。不合规则抛出 ``UploadLimitError``，
调用方在写盘之前把它变成 HTTP 错误。
"""

from __future__ import annotations

from pathlib import Path

_MIB = 1024 * 1024


class UploadLimitError(Exception):
    """扩展名或大小超出配置。"""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def parse_allowed_extensions(raw: str) -> tuple[str, ...]:
    """把逗号分隔的配置解析成小写扩展名，不带点，保留配置顺序。"""
    seen: list[str] = []
    for part in raw.split(","):
        ext = part.strip().lower().lstrip(".")
        if ext and ext not in seen:
            seen.append(ext)
    return tuple(seen)


def limit_label(max_bytes: int) -> str:
    """整 MiB 上限写成 ``10MB``，其余写成字节数。"""
    if max_bytes > 0 and max_bytes % _MIB == 0:
        return f"{max_bytes // _MIB}MB"
    return f"{max_bytes}字节"


def check_upload_limits(
    filename: str,
    size: int,
    *,
    allowed_extensions: tuple[str, ...],
    max_bytes: int,
) -> None:
    """类型不符或超过上限时抛出 ``UploadLimitError``。

    同一文件两种问题都有时，先返回类型错误。大小用字节长度，不看文件名里的数字。
    """
    ext = Path(filename or "").suffix.lower().lstrip(".")
    if ext not in allowed_extensions:
        shown = "、".join(allowed_extensions)
        raise UploadLimitError(400, f"不支持的文件类型。当前允许：{shown}")
    if size > max_bytes:
        raise UploadLimitError(413, f"文件超过 {limit_label(max_bytes)} 上限")
