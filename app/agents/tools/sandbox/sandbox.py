"""代码沙箱四层防护实现。

四层防护依次执行：
1. **AST 白名单** — 编译期静态分析，拒绝危险语法（os.system / subprocess / __import__ / open 等）
2. **进程隔离** — subprocess 独立进程，隔离命名空间，无法访问父进程内存
3. **资源限制** — CPU 时间 / 内存 / 磁盘 / 输出字符数上限
4. **超时 Kill** — 超时硬杀进程树，确保不残留

平台兼容：
- Linux/macOS：全部四层可用
- Windows：Layer 1/2/4 可用；Layer 3 资源限制通过 Job Object 部分实现，
  不可用时不阻塞执行，仅记录告警

未实现的能力（待内核打磨阶段补充）：
- SKELETON：Layer 2 进程隔离 — 当前 subprocess.run 未限制文件系统访问
- SKELETON：Layer 3 资源限制 — Windows 上 Job Object 未实现，仅 Unix setrlimit 包装
- SKELETON：Layer 3 输出截断 — 当前未在子进程内做流式截断，依赖 stdout 全量捕获
- SKELETON：Layer 4 进程树强杀 — 当前仅 kill 直接子进程，未递归 kill 孙进程
"""

from __future__ import annotations

import ast
import logging
import os
import platform
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import ClassVar

from app.agents.tools.sandbox.base import (
    KillReason,
    SandboxConfig,
    SandboxResult,
    SecurityError,
)

logger = logging.getLogger(__name__)

# ── Layer 1：AST 白名单 ────────────────────────────────────────
# 允许的 AST 节点类型（白名单）。不在白名单中的节点类型将被拒绝。
# 这是防护的第一道防线，在编译期阻止危险代码。

_ALLOWED_AST_NODES: tuple[type[ast.AST], ...] = (
    # 顶层
    ast.Module,
    ast.Expression,
    ast.Expr,
    # 常量
    ast.Constant,
    # 运算
    ast.BinOp, ast.UnaryOp, ast.BoolOp,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv,
    ast.Mod, ast.Pow, ast.USub, ast.UAdd, ast.Not,
    ast.And, ast.Or,
    # 比较
    ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.Is, ast.IsNot, ast.In, ast.NotIn,
    # 变量
    ast.Name, ast.Load, ast.Store,
    # 赋值
    ast.Assign, ast.AugAssign,
    # 数据结构
    ast.List, ast.Tuple, ast.Dict, ast.Set,
    ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp,
    ast.comprehension,
    # 下标/属性
    ast.Subscript, ast.Slice, ast.Attribute,
    # 控制流
    ast.If, ast.IfExp, ast.For, ast.While, ast.Break, ast.Continue, ast.Pass,
    # 函数/类
    ast.FunctionDef, ast.AsyncFunctionDef, ast.Return,
    ast.arguments, ast.arg, ast.Lambda,
    ast.ClassDef,
    # 调用
    ast.Call, ast.keyword,
    # 字符串
    ast.JoinedStr, ast.FormattedValue,
    # 类型
    ast.AnnAssign, ast.TypeIgnore,
    # 异常
    ast.Try, ast.ExceptHandler, ast.Raise, ast.Assert,
    # 导入（仅允许白名单模块）
    ast.Import, ast.ImportFrom, ast.alias,
    # 布尔
    ast.NameConstant,  # Python 3.7 兼容
)

# 禁止的 AST 节点 — 即使出现在白名单中也单独检查
# 这些是明确的危险操作，绝不允许
_FORBIDDEN_NODES: tuple[type[ast.AST], ...] = (
    ast.Global,    # 全局变量修改
    ast.Nonlocal,  # 闭包变量修改
    ast.Delete,    # del 语句
)

# 禁止的模块 — 即使 import 也无法导入
_BLOCKED_IMPORTS: frozenset[str] = frozenset({
    "os", "subprocess", "sys", "shutil", "signal",
    "socket", "http", "urllib", "requests", "httpx",
    "multiprocessing", "threading", "concurrent",
    "ctypes", "cffi", "_ctypes",
    "importlib", "pkgutil", "pkg_resources",
    "builtins", "__builtins__",
    "pathlib", "io", "codecs",  # 文件 I/O 禁止
    "pickle", "marshal", "shelve",
    "asyncio",  # 禁止异步事件循环
    "traceback", "inspect", "ast", "compile",  # 反射/代码生成禁止
    "atexit", "gc", "warnings",
    # SKELETON：此列表待内核打磨阶段补充完整，参考 Python 3.11 标准库全量审计
})

# 允许的内置函数
_ALLOWED_BUILTINS: frozenset[str] = frozenset({
    "abs", "all", "any", "ascii", "bin", "bool", "bytearray", "bytes",
    "chr", "complex", "dict", "divmod", "enumerate", "filter", "float",
    "format", "frozenset", "getattr", "hasattr", "hash", "hex", "id",
    "int", "isinstance", "issubclass", "iter", "len", "list", "map",
    "max", "min", "next", "object", "oct", "ord", "pow", "print",
    "range", "repr", "reversed", "round", "set", "slice", "sorted",
    "str", "sum", "tuple", "type", "vars", "zip",
    "True", "False", "None", "Exception", "StopIteration", "ValueError",
    "TypeError", "KeyError", "IndexError", "ZeroDivisionError",
    "RuntimeError", "NotImplementedError", "AttributeError",
})

# 资源限制的哨兵值（表示该平台不支持）
_SENTINEL_UNSUPPORTED = -1


class CodeSandbox:
    """四层防护代码沙箱。

    使用方式：
        sandbox = CodeSandbox()
        result = await sandbox.execute("print(1+1)")
        print(result.to_observation())

    线程安全：每次 execute 创建独立临时目录和子进程，无共享状态。
    """

    # 执行脚本的模板：将用户代码包裹在受限环境中
    _SCRIPT_TEMPLATE: ClassVar[str] = """\
# ═══════════════════════════════════════════════════════════
# 沙箱执行环境 — 由 CodeSandbox 自动生成，请勿手动修改
# ═══════════════════════════════════════════════════════════
import builtins as __sandbox_builtins__

# 受限内置函数
__safe_builtins__ = {allowed_builtins_repr}

# 白名单模块
__allowed_modules__ = {allowed_modules_repr}

class __SandboxImportWrapper__:
    \"\"\"限制 import 仅允许白名单模块。\"\"\"
    def __init__(self, allowed):
        self._allowed = allowed
    def __getattr__(self, name):
        if name in self._allowed:
            return __sandbox_builtins__.__import__(name)
        raise ImportError(f"沙箱禁止导入模块: {{name}}")

# 替换 __builtins__ 为受限版本
__sandbox_builtins__.__dict__["__import__"] = __SandboxImportWrapper__(__allowed_modules__)

# 清除危险内置函数
for __name in dir(__sandbox_builtins__):
    if __name.startswith("_") and __name != "__name__":
        continue
    if __name not in __safe_builtins__ and __name not in (
        "__name__", "__doc__", "__package__", "__loader__", "__spec__",
        "__build_class__", "__import__", "copyright", "credits", "license",
    ):
        try:
            del __sandbox_builtins__.__dict__[__name]
        except (KeyError, TypeError):
            pass

# ═══════════════════════════════════════════════════════════
# 用户代码
# ═══════════════════════════════════════════════════════════
{user_code}
"""

    def __init__(self) -> None:
        self._platform = platform.system()  # "Windows" | "Linux" | "Darwin"

    # ── 公开 API ──────────────────────────────────────────
    async def execute(
        self,
        code: str,
        config: SandboxConfig | None = None,
    ) -> SandboxResult:
        """执行代码，依次通过四层防护。

        Args:
            code: 待执行的 Python 代码字符串。
            config: 执行配置，为 None 时使用默认配置。

        Returns:
            SandboxResult：无论成功/失败/被 kill 均返回。
        """
        cfg = config or SandboxConfig()
        start = time.perf_counter()

        # ── Layer 1：AST 白名单 ──
        try:
            self._check_ast(code, cfg)
        except SecurityError as exc:
            elapsed = (time.perf_counter() - start) * 1000
            return SandboxResult(
                exit_code=-1,
                duration_ms=elapsed,
                killed_by=exc.reason,
                killed_detail=exc.detail,
            )

        # ── Layer 2：进程隔离 + Layer 3：资源限制 + Layer 4：超时 ──
        # 三层合并到子进程执行中，减少跨进程通信开销
        with tempfile.TemporaryDirectory(prefix="sandbox_") as tmp_dir:
            script_path = Path(tmp_dir) / "user_script.py"
            script_path.write_text(
                self._SCRIPT_TEMPLATE.format(
                    allowed_builtins_repr=repr(sorted(_ALLOWED_BUILTINS)),
                    allowed_modules_repr=repr(sorted(cfg.allowed_imports)),
                    user_code=code,
                ),
                encoding="utf-8",
            )

            try:
                proc = await self._run_subprocess(script_path, cfg, tmp_dir)
            except subprocess.TimeoutExpired:
                elapsed = (time.perf_counter() - start) * 1000
                return SandboxResult(
                    exit_code=-1,
                    duration_ms=elapsed,
                    killed_by=KillReason.TIMEOUT,
                    killed_detail=f"执行超过 {cfg.timeout_seconds}s 被终止",
                )

        elapsed = (time.perf_counter() - start) * 1000

        # 检查输出是否超限
        total_output = (proc.stdout or "") + (proc.stderr or "")
        truncated = len(total_output) > cfg.max_output_chars
        if truncated:
            stdout = (proc.stdout or "")[:cfg.max_output_chars]
            stderr = (proc.stderr or "")[:max(0, cfg.max_output_chars - len(stdout))]
        else:
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""

        # 检查是否因资源限制被 kill
        killed_by = KillReason.NONE
        killed_detail = ""
        if proc.returncode == -9 or proc.returncode == 137:  # SIGKILL
            killed_by = KillReason.TIMEOUT
            killed_detail = "进程被 SIGKILL 终止（可能超时或内存超限）"
        elif proc.returncode == -6 or proc.returncode == 134:  # SIGABRT
            killed_by = KillReason.RESOURCE_MEMORY
            killed_detail = "进程被 SIGABRT 终止（可能内存超限）"

        return SandboxResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=proc.returncode,
            duration_ms=elapsed,
            truncated=truncated,
            killed_by=killed_by,
            killed_detail=killed_detail,
        )

    # ── Layer 1：AST 白名单 ───────────────────────────────
    def _check_ast(self, code: str, config: SandboxConfig) -> None:
        """静态分析代码 AST，拒绝危险语法和禁止模块。"""
        try:
            tree = ast.parse(code, mode="exec")
        except SyntaxError as exc:
            raise SecurityError(
                KillReason.AST_REJECTED,
                f"语法错误：{exc.msg}（行 {exc.lineno}）",
            )

        for node in ast.walk(tree):
            # 检查禁止的节点类型
            if isinstance(node, _FORBIDDEN_NODES):
                raise SecurityError(
                    KillReason.AST_REJECTED,
                    f"禁止使用 {type(node).__name__}（不安全操作）",
                )

            # 检查节点是否在白名单中
            if not isinstance(node, _ALLOWED_AST_NODES):
                raise SecurityError(
                    KillReason.AST_REJECTED,
                    f"不支持的语法：{type(node).__name__}（AST 白名单未包含）",
                )

            # 检查 import 语句
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                self._check_import(node, config)

    def _check_import(
        self, node: ast.Import | ast.ImportFrom, config: SandboxConfig
    ) -> None:
        """检查 import 语句是否引用了禁止模块。"""
        if isinstance(node, ast.ImportFrom):
            if node.module is None:
                return  # from . import xxx 相对导入
            root = node.module.split(".")[0]
        else:
            # ast.Import
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _BLOCKED_IMPORTS:
                    raise SecurityError(
                        KillReason.AST_REJECTED,
                        f"禁止导入模块：{root}（安全策略拒绝）",
                    )
                if root not in config.allowed_imports:
                    raise SecurityError(
                        KillReason.AST_REJECTED,
                        f"不在白名单中的模块：{root}（仅允许 {config.allowed_imports}）",
                    )
            return

        if root in _BLOCKED_IMPORTS:
            raise SecurityError(
                KillReason.AST_REJECTED,
                f"禁止导入模块：{root}（安全策略拒绝）",
            )
        if root not in config.allowed_imports:
            raise SecurityError(
                KillReason.AST_REJECTED,
                f"不在白名单中的模块：{root}（仅允许 {config.allowed_imports}）",
            )

    # ── Layer 2：进程隔离 ─────────────────────────────────
    async def _run_subprocess(
        self,
        script_path: Path,
        config: SandboxConfig,
        tmp_dir: str,
    ) -> subprocess.CompletedProcess:
        """在独立子进程中执行脚本，合并 Layer 2/3/4。

        SKELETON：当前实现为最简版本。
        - 未限制文件系统访问（应 chroot 或限制 work_dir）
        - 未设置进程用户（应以 nobody 运行）
        - 未设置网络隔离（应禁止网络访问）
        """
        env = os.environ.copy()
        env.update(config.env_vars)
        # 限制 PATH 为安全目录
        env["PATH"] = "/usr/local/bin:/usr/bin:/bin"
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        # SKELETON：Layer 3 资源限制 — Unix 平台通过 setrlimit
        # 通过 Python 包装器设置资源限制后执行用户脚本
        limit_wrapper = self._build_limit_wrapper(script_path, config)

        try:
            # SKELETON：使用 asyncio.create_subprocess_exec 替代 subprocess.run
            # 以支持流式输出截断和非阻塞超时
            proc = await self._run_with_limits(
                limit_wrapper, config, tmp_dir, env
            )
        except subprocess.TimeoutExpired:
            raise
        return proc

    def _build_limit_wrapper(self, script_path: Path, config: SandboxConfig) -> str:
        """构建资源限制包装脚本（Unix 平台）。

        在子进程内通过 setrlimit 设置资源上限后 exec 用户脚本。
        Windows 上跳过 setrlimit（不支持），直接执行用户脚本。
        """
        if self._platform == "Windows":
            # SKELETON：Windows 上通过 Job Object 实现资源限制
            return str(script_path)

        wrapper = f"""\
import resource
import sys
import os

# CPU 时间限制
try:
    resource.setrlimit(resource.RLIMIT_CPU, ({config.max_cpu_seconds}, {config.max_cpu_seconds}))
except (ValueError, OSError):
    pass

# 内存限制
try:
    mem_bytes = {config.max_memory_mb} * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
except (ValueError, OSError):
    pass

# 磁盘写入限制
try:
    disk_bytes = {config.max_disk_mb} * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_FSIZE, (disk_bytes, disk_bytes))
except (ValueError, OSError):
    pass

# 禁止创建子进程
try:
    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
except (ValueError, OSError):
    pass

# 执行用户脚本
sys.path.insert(0, os.path.dirname('{script_path.as_posix()}'))
exec(open('{script_path.as_posix()}', encoding='utf-8').read())
"""
        return wrapper

    async def _run_with_limits(
        self,
        code_or_path: str,
        config: SandboxConfig,
        tmp_dir: str,
        env: dict,
    ) -> subprocess.CompletedProcess:
        """执行子进程（带超时和资源限制）。"""
        # 判断是包装脚本还是直接路径
        if code_or_path.endswith(".py") and "\n" not in code_or_path:
            # 直接执行脚本文件
            args = [sys.executable, code_or_path]
        else:
            # 通过 -c 执行包装脚本
            args = [sys.executable, "-c", code_or_path]

        # SKELETON：使用 subprocess.run 阻塞执行。
        # 内核打磨时改为 asyncio.create_subprocess_exec + 流式读取。
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=config.timeout_seconds,
            cwd=tmp_dir,
            env=env,
            # SKELETON：未设置 preexec_fn=os.setpgrp（Unix 进程组隔离）
            # SKELETON：未设置 start_new_session=True（进程组 leader）
            # SKELETON：未设置 close_fds=True
        )


# ── 工厂函数（供 Tool 注册使用）──────────────────────────
_global_sandbox: CodeSandbox | None = None


def get_sandbox() -> CodeSandbox:
    """返回全局沙箱单例（线程安全，每次 execute 创建独立子进程）。"""
    global _global_sandbox
    if _global_sandbox is None:
        _global_sandbox = CodeSandbox()
    return _global_sandbox