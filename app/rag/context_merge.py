"""将会话记忆与 RAG 检索上下文按预算合并。

管线只有一个 ``state.context``。检索结果不得覆盖记忆，也不得超过各自字符预算。

RAG-029 前这里用 ``text[:limit].rstrip() + "…"`` 按**字符**硬切整段字符串，会切掉
``[/UNTRUSTED_SOURCE]`` 闭合标记、把块内 ``` 围栏切成一半。现在预算逻辑统一由
``app.rag.context_builder`` 承担：记忆按**条目**裁剪（保留最新、不产生半条），
RAG 段落先给闭合标记留位置再截断。本模块只保留字符串入口的签名与不可信工具拒绝。
"""

from __future__ import annotations

from app.core.config import settings
from app.rag.context_builder import build_from_rag_text


def merge_memory_and_rag(memory_text: str, rag_text: str) -> str:
    """记忆在前、RAG 在后；任一侧为空则只保留另一侧。

    围栏闭合是硬要求：只要 ``rag_text`` 里出现 ``[UNTRUSTED_SOURCE]``，返回值
    必以 ``[/UNTRUSTED_SOURCE]`` 结尾（空间不足时整段丢弃，也不留半截围栏）。
    """
    payload = build_from_rag_text(
        memory_text,
        rag_text,
        memory_budget=settings.RAG_MEMORY_CONTEXT_CHARS,
        rag_budget=settings.RAG_CONTEXT_CHARS,
    )
    return payload.text


def reject_untrusted_tool_call(tool_name: str, user_input: str, context: str) -> bool:
    """不可信上下文点名、且用户原文未出现的工具名则拒绝。

    ``[UNTRUSTED_SOURCE]`` 只是开关，不是匹配范围。开关打开后对整段
    ``context`` 做子串匹配，记忆和之后追加的 critique 也算在内。不是通用
    工具白名单，也不是只在围栏标记内部查找。整段 context 中不存在的工具名
    不会被拒绝。
    """
    name = (tool_name or "").strip()
    if not name or "[UNTRUSTED_SOURCE]" not in (context or ""):
        return False
    if name not in context:
        return False
    return name not in (user_input or "")
