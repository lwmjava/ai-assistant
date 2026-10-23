"""Windows-safe subprocess capture for UTF-8 CLI output used by gate tests."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from typing import Any


def run_utf8(
    args: Sequence[str],
    *,
    timeout: float | None = 60,
    check: bool = False,
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    """Run a child process and decode stdout/stderr as UTF-8.

    On Windows, ``text=True`` alone uses the ANSI/GBK code page and can leave
    ``stdout``/``stderr`` as ``None`` when the child prints UTF-8 Chinese.
    """
    merged = dict(os.environ if env is None else env)
    merged.setdefault("PYTHONUTF8", "1")
    merged.setdefault("PYTHONIOENCODING", "utf-8")
    return subprocess.run(
        list(args),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=check,
        env=merged,
        cwd=cwd,
        **kwargs,
    )


def python_utf8(script_or_args: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """Run ``sys.executable`` with UTF-8 capture."""
    return run_utf8([sys.executable, *script_or_args], **kwargs)
