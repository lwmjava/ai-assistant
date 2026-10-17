"""按用户原文选择对话路径。

不另调模型。认不出就按简单问题，避免一次分类失败把请求送进多轮编排。

判定只比子串，不是语义理解：同样的意思换个说法就可能落到别的路径。
因此关键词按「漏判代价」不对称地放宽：

- 工具路径收紧：说错会多一次工具清单注入和一轮编排，白花延迟。
- 知识库路径放宽：说错只是多一次检索，检索无果时会按
  「没有可用的检索结果」降级回答；反过来漏判则是回答失去依据。
"""

from dataclasses import dataclass
from enum import Enum

_CALCULATOR_MARKS = ("算一下", "帮我算", "计算一下", "计算器")
_CODE_MARKS = (
    "运行这段代码",
    "执行这段代码",
    "代码沙箱",
    "帮我运行",
    "帮我执行",
    "跑一下代码",
    "写段代码",
    "运行一下",
    "执行一下",
)
_TIME_MARKS = ("现在几点", "当前时间", "今天几号")
_RAG_MARKS = (
    "知识库",
    "根据文档",
    "根据资料",
    "查一下",
    "来源文件",
    "文档里",
    "文件里",
    "写明",
    "手册里",
    "制度里",
)
_MULTI_MARKS = ("先调研", "分步调研", "多轮")


class RouteKind(str, Enum):
    """代码强制执行的四条路径。"""

    SIMPLE = "simple"
    TOOLS = "tools"
    RAG = "rag"
    MULTI = "multi"


@dataclass(frozen=True)
class ChatRoute:
    """一条已决定的路径。工具路径带上允许的工具名。"""

    kind: RouteKind
    tool_names: frozenset[str] = frozenset()


def route_message(text: str) -> ChatRoute:
    """根据明确说法选择路径。同时命中时，工具优先于知识库，知识库优先于多轮。"""
    raw = text or ""
    tool_names: set[str] = set()
    if any(mark in raw for mark in _CALCULATOR_MARKS):
        tool_names.add("calculator")
    if any(mark in raw for mark in _CODE_MARKS):
        tool_names.add("code_sandbox")
    if any(mark in raw for mark in _TIME_MARKS):
        tool_names.add("get_current_datetime")
    if tool_names:
        return ChatRoute(kind=RouteKind.TOOLS, tool_names=frozenset(tool_names))
    if any(mark in raw for mark in _RAG_MARKS):
        return ChatRoute(kind=RouteKind.RAG)
    if any(mark in raw for mark in _MULTI_MARKS):
        return ChatRoute(kind=RouteKind.MULTI)
    return ChatRoute(kind=RouteKind.SIMPLE)
