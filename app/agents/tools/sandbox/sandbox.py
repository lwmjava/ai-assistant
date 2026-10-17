"""代码沙箱四层防护实现。

四层防护依次执行：
1. **AST 白名单** — 编译期静态分析，拒绝危险语法（os.system / subprocess / __import__ / open 等）
2. **进程隔离** — subprocess 独立进程，隔离命名空间，无法访问父进程内存
3. **资源限制** — CPU 时间 / 内存 / 磁盘 / 输出字符数上限
4. **超时 Kill** — 超时硬杀进程树，确保不残留

平台兼容：
- Linux/macOS：setrlimit 后 execv 进入用户脚本，超时结束进程组
- Windows：Job Object 结束进程树。内存和 CPU 限额仍不在 Windows 上设置

未实现的能力（可按需扩展）：
- 沿类继承链拿到文件能力的绕过
- Windows 内存与 CPU 限额
- 子进程内流式截断输出
- chroot、非特权用户、网络隔离
"""

from __future__ import annotations

import ast
import asyncio
import logging
import platform
import shutil
import tempfile
import time
from pathlib import Path
from typing import ClassVar

from app.agents.tools.sandbox.base import (
    KillReason,
    SandboxConfig,
    SandboxResult,
    SecurityError,
)
from app.agents.tools.sandbox.proc import ISOLATION_FAILURE, ProcessOutcome, run_script
from app.agents.tools.sandbox.runtime import (
    child_environment,
    effective_timeout,
    sanitize_host_paths,
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
    # SKELETON：此列表尚未覆盖完整，可参考 Python 3.11 标准库全量审计后补充
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
        timeout = effective_timeout(cfg.timeout_seconds)
        start = time.perf_counter()

        # ── Layer 1：AST 白名单 ──
        try:
            self._check_ast(code, cfg)
        except SecurityError as exc:
            elapsed = (time.perf_counter() - start) * 1000
            return self._clean_result(
                SandboxResult(
                    exit_code=-1,
                    duration_ms=elapsed,
                    killed_by=exc.reason,
                    killed_detail=exc.detail,
                    output_limit=cfg.max_output_chars,
                )
            )

        # ── Layer 2：进程隔离 + Layer 3：资源限制 + Layer 4：超时 ──
        tmp_dir = tempfile.mkdtemp(prefix="sandbox_")
        try:
            script_path = Path(tmp_dir) / "user_script.py"
            script_path.write_text(
                self._SCRIPT_TEMPLATE.format(
                    allowed_builtins_repr=repr(sorted(_ALLOWED_BUILTINS)),
                    allowed_modules_repr=repr(sorted(cfg.allowed_imports)),
                    user_code=code,
                ),
                encoding="utf-8",
            )
            outcome = await asyncio.to_thread(
                run_script,
                str(script_path),
                cwd=tmp_dir,
                env=child_environment(cfg.env_vars, windows=self._platform == "Windows"),
                timeout=timeout,
                config=cfg,
            )
        finally:
            try:
                shutil.rmtree(tmp_dir)
            except OSError:
                logger.warning("沙箱临时目录未能删除")

        return self._clean_result(self._result_from_outcome(outcome, cfg, start, timeout))

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
            if isinstance(node, ast.Import | ast.ImportFrom):
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

    def _result_from_outcome(
        self,
        outcome: ProcessOutcome,
        config: SandboxConfig,
        start: float,
        timeout: float,
    ) -> SandboxResult:
        elapsed = (time.perf_counter() - start) * 1000
        if outcome.isolation_error:
            detail = outcome.isolation_error or ISOLATION_FAILURE
            return SandboxResult(
                exit_code=-1,
                stderr=detail,
                duration_ms=elapsed,
                killed_detail=detail,
                output_limit=config.max_output_chars,
                child_pid=outcome.pid,
            )
        stdout = outcome.stdout or ""
        stderr = outcome.stderr or ""
        total_output = stdout + stderr
        truncated = len(total_output) > config.max_output_chars
        if truncated:
            stdout = stdout[: config.max_output_chars]
            stderr = stderr[: max(0, config.max_output_chars - len(stdout))]
        killed_by = KillReason.NONE
        killed_detail = ""
        if outcome.timed_out:
            killed_by = KillReason.TIMEOUT
            killed_detail = f"执行超过 {timeout:g}s 被终止"
        elif outcome.exit_code in (-9, 137):
            killed_by = KillReason.TIMEOUT
            killed_detail = "进程被 SIGKILL 终止（可能超时或内存超限）"
        elif outcome.exit_code in (-6, 134):
            killed_by = KillReason.RESOURCE_MEMORY
            killed_detail = "进程被 SIGABRT 终止（可能内存超限）"
        return SandboxResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=-1 if outcome.timed_out else outcome.exit_code,
            duration_ms=elapsed,
            truncated=truncated,
            killed_by=killed_by,
            killed_detail=killed_detail,
            output_limit=config.max_output_chars,
            child_pid=outcome.pid,
        )

    @staticmethod
    def _clean_result(result: SandboxResult) -> SandboxResult:
        result.stdout = sanitize_host_paths(result.stdout)
        result.stderr = sanitize_host_paths(result.stderr)
        result.killed_detail = sanitize_host_paths(result.killed_detail)
        return result


# ── 工厂函数（供 Tool 注册使用）──────────────────────────
_global_sandbox: CodeSandbox | None = None


def get_sandbox() -> CodeSandbox:
    """返回全局沙箱单例（线程安全，每次 execute 创建独立子进程）。"""
    global _global_sandbox
    if _global_sandbox is None:
        _global_sandbox = CodeSandbox()
    return _global_sandbox
