"""工具十要素契约（AGENTS.md §7）。

声明每个工具的权限、风险分级、副作用、超时、幂等、错误模型、审计与版本。
契约是声明层：不改变工具成功路径的观测文本，只在执行咽喉（ToolRegistry.run）
做调用前拦截、超时包裹与审计写入。

风险分级（tasks.yaml risk_policy / AGENTS.md §7）：
- L0 授权后只读、低敏；
- L1 低风险、可逆写，需鉴权/幂等/审计；
- L2 中风险或跨系统写，默认保守，必要时审批；
- L3 退款/删除/合同/财务/权限提升/不可逆敏感操作，必须人工审批——
  审批闭环存在之前，L3 工具在注册期即被拒绝（ToolRegistry.register）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RiskLevel(str, Enum):
    """工具风险分级。"""

    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


@dataclass(frozen=True)
class ToolPermission:
    """运行时 RBAC 校验所需的权限声明。

    - authenticated: 是否要求 ContextVar 中存在真实 user_id（dev 无认证模式为空）。
    - allowed_roles: 允许执行的角色集合（Role.value）；None 表示任意已认证角色。
      与 ROLE_PERMISSIONS 矩阵双轨（security.py 不在本卡 allowed_paths）。
    - tenant_scoped: 是否要求 ContextVar 中存在 tenant_id；工具参数不得接受
      tenant_id/user_id（防伪造身份）。
    - resource / action: 与既有 RBAC 语义对齐的标签字段，预留后续矩阵化。
    """

    authenticated: bool = False
    allowed_roles: frozenset[str] | None = None
    tenant_scoped: bool = False
    resource: str = "tools"
    action: str = "read"


@dataclass(frozen=True)
class ToolContract:
    """单个工具的十要素声明。"""

    version: str
    permission: ToolPermission
    risk: RiskLevel
    side_effect: str
    timeout_seconds: float
    max_retries: int = 0
    idempotent: bool = True
    conflict_policy: str = "none"
    error_model: str = "observation_string_no_raise"
    audit: bool = True
    # v1 校验开关：默认 False。False 时 Executor 对缺参/坏参放行（既有 4 个内置工具
    # 自身已返回友好文案），只把校验结论记入 ExecutorResult.audit_metadata；
    # True 时 Executor 在执行前硬拒绝（func 零调用）。既有内置工具均不开启。
    strict_validation: bool = False
