"""通过显式 Compose 项目管理部署，不导入应用运行配置。"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

from dotenv import dotenv_values

_SECRET_NAME = re.compile(r"password|passwd|secret|token|key|credential|database_url", re.I)
_SENSITIVE_LINE = re.compile(
    r"password|passwd|secret|token|api[_-]?key|authorization|bearer|credential|"
    r"[a-z][a-z0-9+.-]*://[^\s/]*@|\beyJ[A-Za-z0-9_-]+\.|\bsk-[A-Za-z0-9_-]{20,}",
    re.I,
)


def _positive(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("必须为正整数") from None
    if number <= 0:
        raise argparse.ArgumentTypeError("必须为正整数")
    return number


def _project(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", value):
        raise argparse.ArgumentTypeError("项目名仅允许小写字母、数字、下划线和连字符，并以字母或数字开头")
    return value


def add_commands(subparsers) -> None:
    for name, help_text in (
        ("start", "启动显式指定的 Compose 项目并等待健康"),
        ("stop", "停止指定项目的容器，保留容器和数据卷"),
        ("status", "查看指定项目的容器状态（包括已停止容器）"),
        ("logs", "读取指定项目最近的脱敏日志，不持续跟随"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("--project-name", required=True, type=_project, help="明确指定要管理的 Compose 项目")
        command.add_argument("--env-file", required=True, help="显式配置文件路径")
        command.add_argument("--compose-file", default="docker-compose.prod.yml", help="编排文件路径")
        command.add_argument("--timeout", type=_positive, default=300, help="客户端最长等待秒数，默认 300")
        if name == "logs":
            command.add_argument("--tail", type=_positive, default=100, help="每个容器最多读取的日志行数")


def _safe_output(text: str, secrets: set[str]) -> str:
    forms = {form for value in secrets for form in (value, quote(value, safe=""), *value.splitlines()) if form}
    return "\n".join(
        "[日志已省略：敏感内容]" if _SENSITIVE_LINE.search(line) or any(form in line for form in forms) else line
        for line in text.splitlines()
    )


def execute(args: argparse.Namespace) -> int:
    try:
        compose_file = Path(args.compose_file).resolve(strict=True)
        env_file = Path(args.env_file).resolve(strict=True)
        if not compose_file.is_file() or not env_file.is_file():
            raise OSError
        values = dict(dotenv_values(env_file, interpolate=False))
        expanded = dict(dotenv_values(env_file, interpolate=True))
    except (OSError, ValueError, UnicodeError):
        print("编排或配置文件不可读；请检查 --compose-file 和 --env-file 指向的文件及权限。", file=sys.stderr)
        return 1

    environment = {key: value for key, value in os.environ.items() if not key.startswith("COMPOSE_")}
    secrets = {value for value in [*values.values(), *expanded.values()] if value}
    secrets.update(value for key, value in environment.items() if value and _SECRET_NAME.search(key))
    for value in values.values():
        for name in re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)", value or ""):
            if environment.get(name):
                secrets.add(environment[name])
    argv = [
        "docker", "compose", "--project-directory", str(compose_file.parent),
        "--file", str(compose_file), "--env-file", str(env_file), "--project-name", args.project_name,
    ]
    operations = {
        "start": ["up", "--detach", "--wait", "--wait-timeout", str(args.timeout)],
        "stop": ["stop", "--timeout", "30"],
        "status": ["ps", "--all"],
        "logs": ["logs", "--no-color", "--tail", str(getattr(args, "tail", 100))],
    }
    try:
        result = subprocess.run(
            [*argv, *operations[args.command]],
            cwd=compose_file.parent, env=environment, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=args.timeout, check=False,
        )
    except FileNotFoundError:
        print("找不到 Docker；请安装 Docker Compose v2，并确认 docker 位于 PATH。", file=sys.stderr)
        return 1
    except OSError:
        print("无法执行 Docker；请检查程序权限与 Docker 服务访问权限。", file=sys.stderr)
        return 1
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
        print(
            "等待超时或已中断；容器操作可能仍在进行，请用相同项目和配置执行 status 核对。",
            file=sys.stderr,
        )
        return 124 if isinstance(error, subprocess.TimeoutExpired) else 130
    if result.returncode:
        print(
            f"Compose 操作失败（退出码 {result.returncode}）；请检查 Docker 服务、Compose v2、配置和项目状态。"
            "为保护凭据未回显底层输出。",
            file=sys.stderr,
        )
        return result.returncode if result.returncode > 0 else 1
    if args.command in {"status", "logs"}:
        print(_safe_output(result.stdout, secrets))
    else:
        print("Compose 操作已完成；可用相同项目和配置执行 status 核对。")
    return 0
