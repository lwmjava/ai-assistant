"""LibreOffice 转换公共工具。

供老 Office 解析器和现代 Office 的 PDF/OCR 兜底复用，统一承载：
- 可执行文件探测
- headless 转换
- 结构化错误上下文
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from app.rag.document_parsers.base import DocumentParseError
from app.rag.import_trace import ImportTraceMixin

# 单次 LibreOffice 转换的超时时间，避免损坏文件导致子进程长时间挂起。
_LIBREOFFICE_TIMEOUT_SECONDS = 60.0


class LegacyOfficeConversionError(DocumentParseError, ImportTraceMixin):
    """LibreOffice 转换失败，并携带可持久化的诊断上下文。"""

    def __init__(
        self,
        *,
        message: str,
        stage: str = "legacy_office_convert",
        error_code: str,
        command: str | None = None,
        exit_code: int | None = None,
        stdout: str | None = None,
        stderr: str | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.error_code = error_code
        self.command = command
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


def _libreoffice_binary() -> str | None:
    """返回可用的 LibreOffice 可执行文件路径，未安装返回 None。"""
    for name in ("soffice", "libreoffice"):
        path = shutil.which(name)
        if path:
            return path
    return None


def convert_office_bytes_with_libreoffice(
    file_bytes: bytes, source_ext: str, target_ext: str
) -> bytes:
    """用 headless LibreOffice 将源字节转换为目标格式字节。"""
    binary = _libreoffice_binary()
    if not binary:
        raise LegacyOfficeConversionError(
            message="老 Office 格式依赖缺失：未检测到 LibreOffice（soffice）",
            error_code="libreoffice_missing",
        )
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / f"source.{source_ext}"
            src.write_bytes(file_bytes)
            command = [binary, "--headless", "--convert-to", target_ext, "--outdir", tmp, str(src)]
            result = subprocess.run(
                command,
                capture_output=True,
                timeout=_LIBREOFFICE_TIMEOUT_SECONDS,
            )
            if result.returncode != 0:
                raise LegacyOfficeConversionError(
                    message="老 Office 格式转换失败：LibreOffice 进程返回非 0 状态码",
                    error_code="libreoffice_non_zero_exit",
                    command=" ".join(command),
                    exit_code=result.returncode,
                    stdout=result.stdout.decode("utf-8", errors="replace"),
                    stderr=result.stderr.decode("utf-8", errors="replace"),
                )
            outputs = list(tmp_path.glob(f"*.{target_ext}"))
            if not outputs:
                raise LegacyOfficeConversionError(
                    message="老 Office 格式转换失败：LibreOffice 未生成目标文件",
                    error_code="libreoffice_output_missing",
                    command=" ".join(command),
                    exit_code=result.returncode,
                    stdout=result.stdout.decode("utf-8", errors="replace"),
                    stderr=result.stderr.decode("utf-8", errors="replace"),
                )
            return outputs[0].read_bytes()
    except subprocess.TimeoutExpired as exc:
        command = " ".join(str(part) for part in (exc.cmd or []))
        raise LegacyOfficeConversionError(
            message="老 Office 格式转换失败：LibreOffice 执行超时",
            error_code="libreoffice_timeout",
            command=command,
            stdout=(exc.stdout or b"").decode("utf-8", errors="replace"),
            stderr=(exc.stderr or b"").decode("utf-8", errors="replace"),
        ) from exc
    except DocumentParseError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise LegacyOfficeConversionError(
            message="老 Office 格式转换失败：请确认文件未损坏或 LibreOffice 环境可用",
            error_code="libreoffice_unknown_error",
        ) from exc
