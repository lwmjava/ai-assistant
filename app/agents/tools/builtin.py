"""内置工具集。

提供一组开箱即用的安全工具，便于在「行动」阶段直接复用：
- ``calculator``：仅允许四则运算与括号的安全算术求值；
- ``code_sandbox``：四层防护的 Python 代码沙箱安全执行；
- ``get_current_datetime``：返回当前本地时间；
- ``web_fetch``：对给定 URL 发起 GET 请求并截取响应正文（便于接入外部 API）。

业务可按需在 ``ToolRegistry`` 中注册更多自定义工具。
"""

import ast
import datetime
import math
from contextvars import ContextVar

from app.agents.tools.base import Tool
from app.agents.tools.contract import RiskLevel, ToolContract, ToolPermission
from app.agents.tools.outcome import ToolOutput

# 只在这一次工具调用里交接界面结果。管线取走后清空，不在进程里攒列表。
_pending_code_results: ContextVar[list[dict[str, str]] | None] = ContextVar(
    "code_sandbox_public_results", default=None
)

_ALLOWED_NODES = (
    ast.Expression,
    ast.Constant,
    ast.BinOp,
    ast.UnaryOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Pow,
    ast.Mod,
    ast.USub,
    ast.UAdd,
)


def _safe_eval(expr: str) -> float | int:
    """仅允许数值与四则运算的安全求值，拒绝任意表达式。"""
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ValueError(f"不支持的表达式元素：{type(node).__name__}")
    return eval(  # noqa: S307 — 已通过 AST 白名单约束，仅数值运算
        compile(tree, "<calc>", "eval"), {"__builtins__": {}}, {}
    )


async def calculator(arguments: dict) -> str:
    """计算给定的算术表达式。"""
    expr = str(arguments.get("expression", "")).strip()
    if not expr:
        return ToolOutput("未提供 expression 参数。", status="error", error_code="missing_expression")
    try:
        value = _safe_eval(expr)
    except (SyntaxError, ValueError, ZeroDivisionError, TypeError, OverflowError) as exc:
        return ToolOutput(f"计算失败：{exc}", status="error", error_code="invalid_expression")
    if not isinstance(value, int | float) or (isinstance(value, float) and not math.isfinite(value)):
        return ToolOutput("计算失败：结果不是有限数值。", status="error", error_code="non_finite_result")
    return str(value)


async def get_current_datetime(arguments: dict) -> str:
    """返回当前本地时间（ISO 格式）。"""
    now = datetime.datetime.now()
    fmt = str(arguments.get("format", "") or "%Y-%m-%d %H:%M:%S").strip()
    try:
        return now.strftime(fmt)
    except (ValueError, TypeError):
        return now.isoformat()


def publish_code_result(view: dict[str, str]) -> None:
    """把一条界面结果放进这次调用的缓冲。"""
    current = _pending_code_results.get()
    if current is None:
        current = []
        _pending_code_results.set(current)
    current.append(
        {
            "status": str(view.get("status", "")),
            "stdout": str(view.get("stdout", "")),
            "reason": str(view.get("reason", "")),
        }
    )


def take_code_results() -> list[dict[str, str]]:
    """取走并清空这次调用的界面结果。"""
    current = _pending_code_results.get()
    _pending_code_results.set(None)
    return list(current or [])


async def code_sandbox(arguments: dict) -> str:
    """在四层防护沙箱中执行 Python 代码，返回执行结果。"""
    from app.agents.tools.sandbox import CodeSandbox, SandboxConfig
    from app.agents.tools.sandbox.runtime import effective_timeout

    code = str(arguments.get("code", "")).strip()
    if not code:
        publish_code_result(
            {"status": "error", "stdout": "", "reason": "未提供 code 参数。"}
        )
        return ToolOutput("未提供 code 参数。", status="error", error_code="missing_code")
    raw_timeout = arguments.get("timeout", 30)
    try:
        timeout = float(raw_timeout)
    except (TypeError, ValueError):
        timeout = 30
    sandbox = CodeSandbox()
    config = SandboxConfig(timeout_seconds=effective_timeout(timeout))
    result = await sandbox.execute(code, config)
    publish_code_result(result.public_view())
    from app.agents.tools.sandbox.base import KillReason

    observation = result.to_observation()
    if result.killed_by == KillReason.TIMEOUT:
        return ToolOutput(observation, status="timeout", error_code="sandbox_timeout")
    if result.killed_by != KillReason.NONE:
        return ToolOutput(observation, status="denied", error_code="sandbox_denied")
    if result.exit_code != 0:
        return ToolOutput(observation, status="error", error_code="sandbox_error")
    return observation


async def web_fetch(arguments: dict) -> str:
    """对给定 URL 发起 GET 请求并返回截断后的响应正文。"""
    import httpx

    url = str(arguments.get("url", "")).strip()
    if not url:
        return ToolOutput("未提供 url 参数。", status="error", error_code="missing_url")
    max_chars = int(arguments.get("max_chars", 2000))
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, follow_redirects=True)
            text = resp.text
    except Exception as exc:  # noqa: BLE001 — 网络不可达等异常统一为观测文本
        return ToolOutput(f"请求失败：{exc}", status="error", error_code="network_error")
    snippet = text[: max_chars]
    observation = f"HTTP {resp.status_code}，正文前 {len(snippet)} 字符：\n{snippet}"
    if resp.status_code >= 400:
        return ToolOutput(observation, status="error", error_code="http_error")
    return observation


def default_tools() -> list[Tool]:
    """返回内置工具列表。"""
    l0 = ToolContract(
        version="1.0.0",
        permission=ToolPermission(authenticated=False),
        risk=RiskLevel.L0,
        side_effect="none",
        timeout_seconds=30.0,
        idempotent=True,
    )
    l1 = ToolContract(
        version="1.0.0",
        permission=ToolPermission(authenticated=False),
        risk=RiskLevel.L1,
        side_effect="network_read",
        timeout_seconds=30.0,
        idempotent=True,
    )
    tools = [
        Tool(
            name="calculator",
            description="对数学表达式求值，支持 + - * / ** % 与括号，仅限数值运算。",
            parameters={
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "要计算的算术表达式"}
                },
                "required": ["expression"],
            },
            func=calculator,
            contract=l0,
        ),
        Tool(
            name="code_sandbox",
            description=(
                "在四层安全防护的 Python 沙箱中执行代码。"
                "支持标准 Python 语法（数学、字符串、列表、字典、循环、函数等），"
                "禁止文件 I/O、网络、系统调用、子进程等危险操作。"
                "超时默认 30 秒，可配置。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "要执行的 Python 代码",
                    },
                    "timeout": {
                        "type": "number",
                        "description": "超时秒数，默认 30",
                    },
                },
                "required": ["code"],
            },
            func=code_sandbox,
            contract=l1,
        ),
        Tool(
            name="get_current_datetime",
            description="获取当前本地时间，支持 strftime 格式化（默认 %Y-%m-%d %H:%M:%S）。",
            parameters={
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "description": "时间格式，留空使用默认格式",
                    }
                },
            },
            func=get_current_datetime,
            contract=l0,
        ),
        Tool(
            name="web_fetch",
            description="对给定 URL 发起 HTTP GET 请求并取回截断后的正文，便于调用外部 API / 网页。",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "目标 URL"},
                    "max_chars": {
                        "type": "integer",
                        "description": "返回的最大字符数，默认 2000",
                    },
                },
                "required": ["url"],
            },
            func=web_fetch,
            contract=l1,
        ),
    ]
    from app.agents.tools.file_ops import file_ops_tool, file_ops_write_tool
    from app.agents.tools.web_search import search_tool

    for factory in (search_tool, file_ops_tool, file_ops_write_tool):
        optional = factory()
        if optional is not None:
            tools.append(optional)
    return tools
