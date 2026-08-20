"""工具（Function Calling）抽象。

让 Agent 在「行动」阶段能够声明并调用外部工具 / API：
- ``Tool``：单个工具的元数据与执行入口（名称、描述、参数 JSON Schema、执行函数）；
- ``ToolRegistry``：按名称管理的工具注册表，并提供供提示词使用的文本描述；
- ``parse_tool_call``：解析模型在「行动」阶段产出的工具调用指令（统一采用
  ``<tool_call>`` 包裹的 JSON 信封，避免与普通文本混淆）。

工具执行与具体大模型实现无关，因此本模块可在任意 LLM 提供商下工作。
"""

import inspect
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

# 工具调用的信封格式：模型在「行动」阶段若要调用工具，需输出此包裹结构，
# 且除信封外不输出多余内容；否则视为直接给出回答草稿。
_TOOL_CALL_PATTERN = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)


@dataclass
class ToolCall:
    """一次工具调用的解析结果。"""

    name: str
    arguments: dict


@dataclass
class Tool:
    """可被 Agent 调用的外部工具。

    ``func`` 可以是同步或异步函数，统一在 ``execute`` 中按协程处理。
    ``parameters`` 使用 JSON Schema 描述入参，便于生成提示词与未来接入
    原生函数调用接口。
    """

    name: str
    description: str
    parameters: dict
    func: Callable[[dict], "str | Awaitable[str]"]

    async def execute(self, arguments: dict) -> str:
        """执行工具，返回供模型阅读的观测文本。"""
        result = self.func(arguments)
        if inspect.isawaitable(result):
            result = await result
        return str(result)


class ToolRegistry:
    """按名称管理工具集合。"""

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        if tools:
            for tool in tools:
                self.register(tool)

    def register(self, tool: Tool) -> None:
        """注册或覆盖一个工具。"""
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def describe(self) -> str:
        """生成供大模型阅读的可用工具清单（含参数说明）。"""
        if not self._tools:
            return "（当前无可用的外部工具）"
        blocks: list[str] = []
        for tool in self._tools.values():
            params = json.dumps(tool.parameters, ensure_ascii=False)
            blocks.append(f"- {tool.name}：{tool.description}\n  参数(JSON Schema)：{params}")
        return "\n".join(blocks)

    async def run(self, call: ToolCall) -> str:
        """执行一次工具调用，返回结构化观测文本。"""
        tool = self.get(call.name)
        if tool is None:
            return f"[工具调用失败] 未找到名为「{call.name}」的工具。"
        try:
            observation = await tool.execute(call.arguments or {})
        except Exception as exc:  # noqa: BLE001 — 工具异常不应中断整个管线
            logger.exception("工具执行失败：%s", call.name)
            return f"[工具执行错误] {call.name}：{exc}"
        return f"[{call.name}] 参数={call.arguments} 结果={observation}"


def parse_tool_call(text: str) -> ToolCall | None:
    """从模型输出中解析工具调用。

    仅识别 ``<tool_call>`` 信封内的 JSON；若无法解析或无 ``name``，返回 None，
    表示模型意在直接输出回答。
    """
    match = _TOOL_CALL_PATTERN.search(text or "")
    if not match:
        return None
    raw = match.group(1).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    name = data.get("name") or data.get("tool")
    if not name:
        return None
    args = data.get("arguments") or data.get("args") or {}
    if not isinstance(args, dict):
        return None
    return ToolCall(name=name, arguments=args)
