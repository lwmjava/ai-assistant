"""知识库源文件本地存储。

负责将上传的原始文件持久化到项目根目录下的 ``data/knowledge``，
并提供读回、删除与路径解析能力。数据库中仅保存相对路径，
避免环境迁移时写死绝对路径。
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ALLOWED_EXTENSIONS = {
    "txt",
    "md",
    "json",
    "xml",
    "csv",
    "html",
    "htm",
    "docx",
    "xlsx",
    "pptx",
    "pdf",
}


def knowledge_storage_root() -> Path:
    """返回知识库源文件根目录。"""
    return _PROJECT_ROOT / "data" / "knowledge"


def _sanitize_segment(value: str, *, default: str) -> str:
    """清洗目录名或文件名片段，避免路径穿越与特殊字符。"""
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", value).strip("._")
    return cleaned or default


def _normalized_extension(filename: str) -> str:
    """仅保留白名单扩展名，未知类型统一落为 .bin。"""
    ext = Path(filename).suffix.lower().lstrip(".")
    return f".{ext}" if ext in _ALLOWED_EXTENSIONS else ".bin"


def save_source_file(tenant_id: str, file_bytes: bytes, filename: str) -> str:
    """保存源文件并返回相对项目根目录的路径。"""
    tenant_dir = knowledge_storage_root() / _sanitize_segment(
        tenant_id, default="unknown_tenant"
    )
    tenant_dir.mkdir(parents=True, exist_ok=True)

    original_name = Path(filename or "upload.bin").name
    stem = _sanitize_segment(Path(original_name).stem, default="upload")
    ext = _normalized_extension(original_name)
    stored_name = f"{uuid.uuid4().hex}_{stem}{ext}"
    path = tenant_dir / stored_name
    path.write_bytes(file_bytes)
    return path.relative_to(_PROJECT_ROOT).as_posix()


def resolve_source_file_path(relative_path: str) -> Path:
    """将数据库中的相对路径解析为绝对路径，并校验其位于知识库目录下。"""
    root = knowledge_storage_root().resolve()
    candidate = (_PROJECT_ROOT / relative_path.replace("\\", "/")).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise FileNotFoundError("源文件路径非法或不存在") from exc
    return candidate


def read_source_file(relative_path: str) -> bytes:
    """读取源文件内容，不存在时抛 ``FileNotFoundError``。"""
    path = resolve_source_file_path(relative_path)
    return path.read_bytes()


def delete_source_file(relative_path: str) -> bool:
    """删除源文件；文件不存在或路径非法时返回 False。"""
    try:
        path = resolve_source_file_path(relative_path)
    except FileNotFoundError:
        return False
    if not path.exists():
        return False
    path.unlink()

    root = knowledge_storage_root().resolve()
    current = path.parent
    while current != root:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent
    return True
