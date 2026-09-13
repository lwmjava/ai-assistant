"""导入任务错误追踪公共抽象。

统一承载可持久化的失败诊断上下文，供 OCR、URL 抓取、老 Office 转换以及后续
对象存储 / 数据库连接器复用。任务层会把这些结构化字段写入 ``ImportJobTrace``，
同时仅向 ``ImportJob.error`` 写入裁剪后的摘要。
"""

from __future__ import annotations

TRACEABLE_ERROR_FIELDS = (
    "stage",
    "error_code",
    "command",
    "exit_code",
    "stdout",
    "stderr",
)


class ImportTraceMixin:
    """为异常对象补充可持久化的追踪字段。"""

    def _init_import_trace(
        self,
        *,
        stage: str = "import_job",
        error_code: str | None = None,
        command: str | None = None,
        exit_code: int | None = None,
        stdout: str | None = None,
        stderr: str | None = None,
    ) -> None:
        self.stage = stage
        self.error_code = error_code
        self.command = command
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


class ImportTraceError(RuntimeError, ImportTraceMixin):
    """通用的可追踪导入错误，供连接器类异常直接复用。"""

    def __init__(
        self,
        message: str,
        *,
        stage: str = "import_job",
        error_code: str | None = None,
        command: str | None = None,
        exit_code: int | None = None,
        stdout: str | None = None,
        stderr: str | None = None,
    ) -> None:
        super().__init__(message)
        self._init_import_trace(
            stage=stage,
            error_code=error_code,
            command=command,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )


def copy_import_trace(
    target: Exception,
    source: Exception,
    *,
    stage: str | None = None,
) -> Exception:
    """把源异常上的追踪字段复制到新异常上。"""
    for field in TRACEABLE_ERROR_FIELDS:
        if hasattr(source, field):
            setattr(target, field, getattr(source, field))
    if stage is not None:
        setattr(target, "stage", stage)
    return target
