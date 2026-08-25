"""代码沙箱模块。

提供四层安全防护的代码执行环境：
- Layer 1：AST 白名单 — 仅允许安全 Python 子集
- Layer 2：进程隔离 — 独立子进程执行
- Layer 3：资源限制 — CPU / 内存 / 磁盘 / 输出上限
- Layer 4：超时 Kill  — 超时硬杀进程树

对外暴露：
- ``CodeSandbox``：沙箱执行器
- ``SandboxResult``：执行结果
- ``SandboxConfig``：执行配置
- ``SecurityError``：安全拒绝异常
"""

from app.agents.tools.sandbox.base import SandboxConfig, SandboxResult, SecurityError
from app.agents.tools.sandbox.sandbox import CodeSandbox

__all__ = ["CodeSandbox", "SandboxConfig", "SandboxResult", "SecurityError"]