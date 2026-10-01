"""代码沙箱数据类型 — 无外部依赖，仅使用 Python 标准库。

定义沙箱执行的全生命周期数据：
- ``SandboxConfig``：执行前 — 四层防护参数
- ``SandboxResult``：执行后 — 输出、耗时、被哪层拦截
- ``SecurityError``：安全拒绝 — 代码被某层规则拒绝时抛出
- ``KillReason``：拦截原因枚举 — 记录被哪层 kill
"""

from dataclasses import dataclass, field
from enum import Enum


class KillReason(str, Enum):  # noqa: UP042 — 与项目其它枚举一致
    """沙箱拦截原因 — 标记代码被哪一层防护拒绝/终止。"""

    NONE = "none"  # 未被拦截，正常完成
    AST_REJECTED = "ast_rejected"  # Layer 1：AST 白名单拒绝
    RESOURCE_MEMORY = "resource_memory"  # Layer 3：内存超限
    RESOURCE_CPU = "resource_cpu"  # Layer 3：CPU 时间超限
    RESOURCE_OUTPUT = "resource_output"  # Layer 3：输出字符数超限
    TIMEOUT = "timeout"  # Layer 4：执行超时


@dataclass
class SandboxConfig:
    """沙箱执行配置 — 四层防护参数。

    所有字段均有默认值，调用方可按需覆盖。
    """

    # ── Layer 4：超时 ──
    timeout_seconds: float = 30.0
    """单次执行最大秒数，超时触发 SIGKILL 进程树。"""

    # ── Layer 3：资源限制 ──
    max_memory_mb: int = 256
    """最大内存（MB），超出触发 MemoryError。Windows 上通过 Job Object 实现。"""
    max_cpu_seconds: int = 10
    """最大 CPU 时间（秒），防止死循环耗尽 CPU。Unix 通过 setrlimit 实现。"""
    max_output_chars: int = 100_000
    """stdout+stderr 最大字符数，超出则截断并标记 truncated=True。"""
    max_disk_mb: int = 50
    """最大写入磁盘量（MB），Unix 通过 setrlimit(RLIMIT_FSIZE) 实现。"""

    # ── Layer 1：AST 白名单 ──
    allowed_imports: list[str] = field(
        default_factory=lambda: [
            "math",
            "json",
            "datetime",
            "re",
            "itertools",
            "collections",
            "functools",
            "statistics",
        ]
    )
    """允许导入的模块白名单，其他 import 语句将被拒绝。"""

    # ── 通用 ──
    work_dir: str = "/tmp/sandbox"
    """沙箱工作目录，进程无法访问此目录之外的文件。"""
    env_vars: dict[str, str] = field(default_factory=dict)
    """注入的环境变量（PATH 等由沙箱控制，此处仅允许追加安全变量）。"""


@dataclass
class SandboxResult:
    """沙箱执行结果 — 无论成功/失败/被 kill 均返回此结构。"""

    stdout: str = ""
    """标准输出内容。"""

    stderr: str = ""
    """标准错误内容。"""

    exit_code: int = 0
    """进程退出码。0 = 正常，非 0 = 异常，-1 = 被沙箱 kill。"""

    duration_ms: float = 0.0
    """实际执行耗时（毫秒）。"""

    truncated: bool = False
    """输出是否因超限被截断。"""

    killed_by: KillReason = KillReason.NONE
    """被哪层防护拦截，NONE 表示正常完成。"""

    killed_detail: str = ""
    """拦截详情（如 "AST 拒绝：不允许 import os"）。"""

    output_limit: int = 100_000
    """本次输出上限，用于截断说明。"""

    child_pid: int = 0
    """本次直接子进程编号。0 表示没有启动。不进入界面结果。"""

    def to_observation(self) -> str:
        """生成供 Agent 阅读的观测文本。路径应已在写入前清理。"""
        output = self.stdout
        if self.truncated:
            output += f"\n\n[输出已被截断，仅展示前 {self.output_limit} 字符]"
        if self.killed_by == KillReason.NONE and self.exit_code == 0:
            return output
        if self.killed_by != KillReason.NONE:
            head = f"[沙箱执行被拦截] 原因：{self.killed_by.value}\n详情：{self.killed_detail}"
        else:
            head = f"[沙箱执行错误] exit_code={self.exit_code}\nstderr:\n{self.stderr[:2000]}"
        if output:
            return f"{head}\n{output}"
        return head

    def public_view(self) -> dict[str, str]:
        """给界面的结果。不含宿主机路径。"""
        if self.killed_by == KillReason.TIMEOUT:
            status = "timeout"
        elif self.killed_by != KillReason.NONE or self.exit_code != 0:
            status = "error"
        else:
            status = "ok"
        stdout = self.stdout
        if self.truncated:
            stdout += f"\n\n[输出已截断，仅展示前 {self.output_limit} 字符]"
        reason = ""
        if status != "ok":
            reason = self.killed_detail or self.stderr[:500] or "执行失败"
        return {"status": status, "stdout": stdout, "reason": reason}

    def to_dict(self) -> dict:
        """序列化为字典，供日志/审计使用。"""
        return {
            "stdout": self.stdout[:500],
            "stderr": self.stderr[:500],
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "truncated": self.truncated,
            "killed_by": self.killed_by.value,
            "killed_detail": self.killed_detail,
        }


class SecurityError(Exception):
    """代码被沙箱安全策略拒绝（Layer 1 AST 白名单）。"""

    def __init__(self, reason: KillReason, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"[{reason.value}] {detail}")
