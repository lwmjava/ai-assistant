"""按文件头魔术数字校验上传类型。

扩展名白名单仍先行；本模块只拒绝「扩展名在名单内但文件头不符」的伪造。
文本类扩展名无稳定魔术头，仅拒绝已知二进制签名冒充文本。
"""

from __future__ import annotations

from pathlib import Path

# Office Open XML 与 ZIP 同头；OLE 复合文档用于旧版 Office。
_ZIP = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_OLE = (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",)
_PDF = (b"%PDF",)
_PNG = (b"\x89PNG\r\n\x1a\n",)
_JPEG = (b"\xff\xd8\xff",)

_BINARY_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "pdf": _PDF,
    "png": _PNG,
    "jpg": _JPEG,
    "jpeg": _JPEG,
    "doc": _OLE,
    "xls": _OLE,
    "ppt": _OLE,
    "docx": _ZIP,
    "xlsx": _ZIP,
    "pptx": _ZIP,
}

# 无稳定魔术头的文本/源码类：拒绝以已知二进制头开头。
_TEXT_EXTENSIONS = frozenset({
    "txt", "md", "json", "xml", "csv", "html", "htm",
    "go", "py", "css", "java", "js", "yaml", "yml",
    "c", "h", "cpp", "cc", "cxx", "hpp", "vue", "ts",
})

_KNOWN_BINARY_PREFIXES: tuple[bytes, ...] = (
    b"%PDF",
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"PK\x03\x04",
    b"PK\x05\x06",
    b"PK\x07\x08",
    b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
    b"\x7fELF",
    b"MZ",
)


class MagicByteMismatchError(Exception):
    """扩展名与文件头不一致。"""

    def __init__(self, detail: str = "文件内容与扩展名不符") -> None:
        self.detail = detail
        super().__init__(detail)


def _extension(filename: str) -> str:
    return Path(filename or "").suffix.lower().lstrip(".")


def check_magic_bytes(filename: str, data: bytes) -> None:
    """文件头与扩展名不符时抛出 ``MagicByteMismatchError``。"""
    if not data:
        raise MagicByteMismatchError("文件不能为空")
    ext = _extension(filename)
    expected = _BINARY_SIGNATURES.get(ext)
    if expected is not None:
        if not any(data.startswith(sig) for sig in expected):
            raise MagicByteMismatchError("文件内容与扩展名不符")
        return
    if ext in _TEXT_EXTENSIONS:
        if any(data.startswith(sig) for sig in _KNOWN_BINARY_PREFIXES):
            raise MagicByteMismatchError("文件内容与扩展名不符")
        return
    # 未登记扩展名：交给上层扩展名白名单拒绝，这里不额外放行也不误伤。
