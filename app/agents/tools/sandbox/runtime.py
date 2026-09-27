"""沙箱运行前的纯函数：环境、超时、路径清理、Unix 限额包装脚本。"""

from __future__ import annotations

import os
import re
import sys

from app.agents.tools.sandbox.base import SandboxConfig

_DEFAULT_TIMEOUT_SECONDS = 30.0

_WINDOWS_ENV_KEYS = (
    "SYSTEMROOT",
    "WINDIR",
    "PATH",
    "PATHEXT",
    "COMSPEC",
    "TEMP",
    "TMP",
)
_UNIX_ENV_KEYS = ("PATH", "LANG", "LC_ALL", "TMPDIR")

# 先匹配更长的前缀，避免 \\?\ 被当成普通 UNC。
_HOST_PATHS = (
    re.compile(r"\\\\\?\\[^\s\"']+"),
    re.compile(r"\\\\[^\s\"']+"),
    re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/][^\s\"']*"),
    re.compile(r"(?<![\w:/])/(?!/)[^\s\"']*"),
)


def effective_timeout(seconds: float) -> float:
    """非正数超时按默认 30 秒，避免 0 让每次执行立刻超时。"""
    if seconds <= 0:
        return _DEFAULT_TIMEOUT_SECONDS
    return seconds


def child_environment(extra: dict[str, str] | None, *, windows: bool) -> dict[str, str]:
    """只保留启动解释器必需的变量。名单外的调用方变量不传入。"""
    allowed = _WINDOWS_ENV_KEYS if windows else _UNIX_ENV_KEYS
    allowed_set = set(allowed)
    env = {key: os.environ[key] for key in allowed if key in os.environ and os.environ[key] != ""}
    for key, value in (extra or {}).items():
        if key in allowed_set:
            env[key] = value
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def sanitize_host_paths(text: str) -> str:
    """去掉宿主机绝对路径。保留 1/2 和网址里的斜杠。"""
    if not text:
        return text
    cleaned = text
    for pattern in _HOST_PATHS:
        cleaned = pattern.sub("<sandbox>", cleaned)
    return cleaned


def unix_wrapper_source(script_path: str, config: SandboxConfig) -> str:
    """限额设好后用 execv 换成用户脚本，避免包装进程里的 os 留在用户全局名字里。"""
    exe = repr(sys.executable)
    script = repr(script_path)
    memory = int(config.max_memory_mb) * 1024 * 1024
    disk = int(config.max_disk_mb) * 1024 * 1024
    cpu = int(config.max_cpu_seconds)
    return (
        "import os\n"
        "import resource\n"
        "import sys\n"
        "\n"
        "def _limit(name, value):\n"
        "    try:\n"
        "        resource.setrlimit(name, (value, value))\n"
        "    except (ValueError, OSError):\n"
        "        pass\n"
        "\n"
        f"_limit(resource.RLIMIT_CPU, {cpu})\n"
        f"_limit(resource.RLIMIT_AS, {memory})\n"
        f"_limit(resource.RLIMIT_FSIZE, {disk})\n"
        "_limit(resource.RLIMIT_NPROC, 0)\n"
        f"os.execv({exe}, [{exe}, {script}])\n"
    )
