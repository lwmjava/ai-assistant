"""将会话记忆与 RAG 检索上下文按预算合并。

管线只有一个 ``state.context``。检索结果不得覆盖记忆，也不得超过各自字符预算。
"""

from __future__ import annotations

from app.core.config import settings


def _trim(text: str, limit: int) -> str:
    body = (text or "").strip()
    if limit <= 0 or len(body) <= limit:
        return body
    return body[:limit].rstrip() + "…"


def merge_memory_and_rag(memory_text: str, rag_text: str) -> str:
    """记忆在前、RAG 在后；任一侧为空则只保留另一侧。"""
    memory = _trim(memory_text, settings.RAG_MEMORY_CONTEXT_CHARS)
    rag = _trim(rag_text, settings.RAG_CONTEXT_CHARS)
    parts: list[str] = []
    if memory:
        parts.append(f"## 会话记忆\n{memory}")
    if rag:
        parts.append(rag)
    return "\n\n".join(parts)


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
