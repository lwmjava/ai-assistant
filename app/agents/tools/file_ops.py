"""Read approved public shared text files, never tenant storage or host secrets.

Version 1.0.0; L0 read only, no write/list/delete, no retry and no cache. Root
aliases are an operator-maintained public-sharing grant for every authenticated
chat caller. They must not be used to expose tenant-private directories.
"""

import asyncio
import json
import os
import re
import stat
import sys
from pathlib import Path, PureWindowsPath
from typing import Any

from app.agents.tools.base import Tool
from app.core.config import get_settings

_ALIAS = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,31}\Z")
_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".rst"}
_PRIVATE_NAMES = {
    ".git", ".ssh", ".aws", ".azure", ".kube", ".codex", ".agents",
    "credentials", "secrets", "uploads", "tenant", "tenants", "rag_sources", "knowledge",
}
_PRIVATE_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".pem", ".key", ".p12", ".pfx"}
_DEVICES = {"con", "prn", "aux", "nul", *[f"com{i}" for i in range(1, 10)], *[f"lpt{i}" for i in range(1, 10)]}
_SECRET_CONTENT = re.compile(
    r"-----BEGIN (?:[A-Z ]*PRIVATE KEY|OPENSSH)|"
    r"(?i:(?:api[_-]?key|password|secret|access[_-]?token|authorization)\s*[\"']?\s*[:=]\s*[\"']?\S+)"
)


def _private_component(value: str) -> bool:
    lower = value.casefold()
    return (
        lower in _PRIVATE_NAMES
        or lower.split(".")[0] in _DEVICES
        or lower.startswith(".env")
        or Path(lower).suffix in _PRIVATE_SUFFIXES
        or any(term in lower for term in ("credential", "secret", "password", "token"))
    )


def _linked(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _check_chain(path: Path) -> None:
    for item in (*reversed(path.parents), path):
        if _linked(item):
            raise ValueError("linked_path")


def _approved_roots() -> dict[str, Path]:
    settings = get_settings()
    size = settings.FILE_OPS_MAX_BYTES
    if type(size) is not int or not 1 <= size <= 65_536:
        raise ValueError("invalid_configuration")
    raw = settings.FILE_OPS_ALLOWED_ROOTS
    if not isinstance(raw, str) or len(raw) > 16_384:
        raise ValueError("invalid_configuration")
    try:
        mapping = json.loads(raw)
    except RecursionError as exc:
        raise ValueError("invalid_configuration") from exc
    if not isinstance(mapping, dict) or len(mapping) > 32:
        raise ValueError("invalid_configuration")
    roots = {}
    project = Path(__file__).resolve().parents[3]
    private = [project / "app", project / "data" / "knowledge", project / "frontend"]
    for alias, value in mapping.items():
        if not isinstance(alias, str) or not _ALIAS.fullmatch(alias) or not isinstance(value, str):
            raise ValueError("invalid_configuration")
        root = Path(value)
        if not root.is_absolute() or str(root).startswith("\\\\") or any(_private_component(p) for p in root.parts):
            raise ValueError("invalid_configuration")
        _check_chain(root)
        resolved = root.resolve(strict=True)
        if (
            not resolved.is_dir()
            or resolved == Path(resolved.anchor)
            or project.is_relative_to(resolved)
            or resolved == project / "data"
        ):
            raise ValueError("invalid_configuration")
        if any(resolved.is_relative_to(p) for p in private):
            raise ValueError("invalid_configuration")
        roots[alias] = resolved
    return roots


def file_ops_availability() -> dict[str, str]:
    """Validate grants without exposing host paths or file contents."""
    if get_settings().FILE_OPS_ENABLED is not True:
        return {"status": "unavailable", "reason": "disabled"}
    try:
        roots = _approved_roots()
    except (OSError, ValueError, TypeError):
        return {"status": "unavailable", "reason": "invalid_configuration"}
    return {"status": "available" if roots else "unavailable", "reason": "configured" if roots else "no_allowed_roots"}


def _observation(status: str, code: str, **extra: Any) -> str:
    return json.dumps({"status": status, "code": code, "untrusted": True, **extra}, ensure_ascii=False)


def _handle_path(fd: int) -> Path:
    """Bind validation to the actual opened object, not just the original string."""
    if sys.platform == "win32":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        final_path = kernel.GetFinalPathNameByHandleW
        final_path.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        final_path.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32_768)
        length = final_path(msvcrt.get_osfhandle(fd), buffer, len(buffer), 0)
        if not 0 < length < len(buffer):
            raise OSError("handle_path_unavailable")
        name = buffer.value
        if name.startswith("\\\\?\\UNC\\"):
            name = "\\\\" + name[8:]
        elif name.startswith("\\\\?\\"):
            name = name[4:]
        return Path(name)
    # Linux supports descriptor paths. Fail closed on platforms where the actual
    # opened path cannot be proven; a string-only precheck is not sufficient.
    return Path(os.readlink(f"/proc/self/fd/{fd}"))


def _read(root: Path, relative: str, maximum: int) -> tuple[str, bool]:
    windows = PureWindowsPath(relative)
    if (
        not relative
        or len(relative) > 1024
        or windows.drive
        or windows.root
        or "\\" in relative
        or any(c in relative for c in ":\x00")
        or any(ord(c) < 32 for c in relative)
    ):
        raise ValueError("invalid_path")
    parts = relative.split("/")
    if any(p in {"", ".", ".."} or p.endswith((".", " ")) or _private_component(p) for p in parts):
        raise ValueError("invalid_path")
    target = root.joinpath(*parts)
    if target.suffix.casefold() not in _EXTENSIONS:
        raise ValueError("unsupported_file")
    _check_chain(target)
    canonical = target.resolve(strict=True)
    if not canonical.is_relative_to(root):
        raise ValueError("outside_root")
    before = canonical.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError("unsupported_file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(canonical, flags)
    try:
        opened = os.fstat(fd)
        final = _handle_path(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or not final.is_relative_to(root)
            or final != canonical
        ):
            raise ValueError("file_changed")
        _check_chain(final)
        payload = os.read(fd, maximum + 1)
    finally:
        os.close(fd)
    truncated = len(payload) > maximum
    sample = payload[:maximum]
    # If truncation ends halfway through a UTF-8 code point, remove only that
    # partial point. All other malformed bytes are refused rather than replaced.
    try:
        content = sample.decode("utf-8")
    except UnicodeDecodeError as exc:
        if truncated and exc.reason == "unexpected end of data" and exc.end == len(sample):
            content = sample[:exc.start].decode("utf-8")
        else:
            raise ValueError("unsupported_file") from exc
    if any(ord(c) < 32 and c not in "\n\r\t" for c in content) or _SECRET_CONTENT.search(content):
        raise ValueError("unsupported_file")
    return content, truncated


async def file_ops(arguments: dict) -> str:
    """Return a bounded read from an explicitly shared public root alias."""
    if file_ops_availability()["status"] != "available":
        return _observation("unavailable", file_ops_availability()["reason"])
    alias = arguments.get("root")
    relative = arguments.get("path")
    maximum = arguments.get("max_bytes", get_settings().FILE_OPS_MAX_BYTES)
    if (
        set(arguments) - {"root", "path", "max_bytes"}
        or not isinstance(alias, str)
        or not isinstance(relative, str)
        or type(maximum) is not int
        or not 1 <= maximum <= get_settings().FILE_OPS_MAX_BYTES
    ):
        return _observation("error", "invalid_arguments")
    try:
        roots = _approved_roots()
        root = roots.get(alias)
        if root is None:
            return _observation("error", "access_denied")
        content, truncated = await asyncio.to_thread(_read, root, relative, maximum)
        return _observation("ok", "ok", root=alias, path=relative, content=content, truncated=truncated)
    except (OSError, ValueError, TypeError):
        return _observation("error", "access_denied")


def file_ops_tool() -> Tool | None:
    if file_ops_availability()["status"] != "available":
        return None
    try:
        aliases = list(_approved_roots())
    except (OSError, ValueError, TypeError):
        return None
    return Tool(
        name="file_ops",
        description="只读管理员明确授权公开共享目录的 UTF-8 文本；返回不可信资料，不接受写入或租户私有路径。",
        parameters={
            "type": "object",
            "properties": {
                "root": {"type": "string", "enum": aliases},
                "path": {"type": "string", "minLength": 1, "maxLength": 1024},
                "max_bytes": {"type": "integer", "minimum": 1, "maximum": get_settings().FILE_OPS_MAX_BYTES},
            },
            "required": ["root", "path"],
            "additionalProperties": False,
        },
        func=file_ops,
    )
