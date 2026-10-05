"""对话接口：发起对话、流式对话、会话管理。

所有接口均需认证，并按角色校验 ``conversations`` 资源权限（依赖 require_permission）。
权限判定只区分「该角色能否做这类操作」，归属校验在会话服务内完成：
普通用户仅能访问自己的会话，系统管理员可见同租户全部。
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session
from sse_starlette.sse import EventSourceResponse

from app.agents.tools.base import ToolRegistry
from app.agents.tools.builtin import default_tools
from app.api.deps import audit_event, get_db, require_permission
from app.audit.models import AuditAction
from app.core.config import settings
from app.models.conversation import Conversation
from app.models.user import User
from app.security.types import SecurityRejectedError
from app.services.chat_service import ChatService
from app.services.quota import QuotaExceededError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

_service = ChatService()


class ChatRequest(BaseModel):
    """对话请求体。"""

    message: str
    conversation_id: str | None = None


class SourceOut(BaseModel):
    """一条检索来源。没有页码或段落时对应字段为空。"""

    filename: str
    page: int | None = None
    section: str | None = None
    excerpt: str | None = None  # 拘录原文连段
    chunk_id: str | None = None  # 分块 id，可定位到具体分块
    document_id: str | None = None  # 所属文档 id


class CodeResultOut(BaseModel):
    """一次代码执行给界面的结果。不含宿主机路径。"""

    status: str
    stdout: str = ""
    reason: str = ""


class ChatResponse(BaseModel):
    """非流式对话响应。"""

    conversation_id: str
    reply: str
    model: str | None = None
    sources: list[SourceOut] = []
    code_results: list[CodeResultOut] = []
    skill_names: list[str] = []


class ConversationOut(BaseModel):
    """会话概要（不含消息体）。"""

    id: str
    tenant_id: str
    user_id: str
    title: str | None
    created_at: str
    updated_at: str


class MessageOut(BaseModel):
    """消息概要。"""

    id: str
    role: str
    content: str
    model: str | None
    created_at: str
    sources: list[SourceOut] = []
    code_results: list[CodeResultOut] = []
    skill_names: list[str] = []
    # complete：正常写完。stopped：生成已停下，正文不是完整回复。
    status: str = "complete"


class ConversationDetail(ConversationOut):
    """会话详情（含消息列表）。"""

    messages: list[MessageOut]


class ConversationRename(BaseModel):
    """重命名会话。只接受标题。"""

    model_config = ConfigDict(extra="forbid")

    title: str


def _parse_sources(raw: str | None) -> list[SourceOut]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    sources: list[SourceOut] = []
    for item in parsed:
        if not isinstance(item, dict) or not item.get("filename"):
            continue
        sources.append(SourceOut.model_validate(item))
    return sources


def _parse_code_results(raw: str | None) -> list[CodeResultOut]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    results: list[CodeResultOut] = []
    for item in parsed:
        if not isinstance(item, dict) or not item.get("status"):
            continue
        results.append(
            CodeResultOut(
                status=str(item.get("status")),
                stdout=str(item.get("stdout") or ""),
                reason=str(item.get("reason") or ""),
            )
        )
    return results


def _latest_assistant_code_results(conv: Conversation) -> list[CodeResultOut]:
    assistants = [message for message in conv.messages if message.role == "assistant"]
    if not assistants:
        return []
    latest = max(assistants, key=lambda message: message.created_at)
    return _parse_code_results(latest.code_results)


def _parse_skill_names(raw: str | None) -> list[str]:
    """NULL、空串和空数组都读成空列表。"""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if isinstance(item, str)]


def _latest_assistant_skill_names(conv: Conversation) -> list[str]:
    assistants = [message for message in conv.messages if message.role == "assistant"]
    if not assistants:
        return []
    latest = max(assistants, key=lambda message: message.created_at)
    return _parse_skill_names(latest.skill_names)


def _latest_assistant_sources(conv: Conversation) -> list[SourceOut]:
    assistants = [message for message in conv.messages if message.role == "assistant"]
    if not assistants:
        return []
    latest = max(assistants, key=lambda message: message.created_at)
    return _parse_sources(latest.sources)


def _conv_out(conv: Conversation) -> ConversationOut:
    return ConversationOut(
        id=conv.id,
        tenant_id=conv.tenant_id,
        user_id=conv.user_id,
        title=conv.title,
        created_at=conv.created_at.isoformat(),
        updated_at=conv.updated_at.isoformat(),
    )


def _conv_detail(conv: Conversation) -> ConversationDetail:
    messages = [
        MessageOut(
            id=m.id,
            role=m.role,
            content=m.content,
            model=m.model,
            created_at=m.created_at.isoformat(),
            sources=_parse_sources(m.sources),
            code_results=_parse_code_results(m.code_results),
            skill_names=_parse_skill_names(m.skill_names),
            status=m.status or "complete",
        )
        for m in sorted(conv.messages, key=lambda x: x.created_at)
    ]
    base = _conv_out(conv).model_dump()
    base["messages"] = messages
    return ConversationDetail(**base)


@router.post("", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    current_user: User = Depends(require_permission("conversations", "write")),
    session: Session = Depends(get_db),
) -> ChatResponse:
    """发起一次非流式对话。"""
    if not req.message.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="message 不能为空")
    try:
        conv, reply = await _service.chat(
            session, current_user, req.message, req.conversation_id
        )
    except QuotaExceededError as exc:
        return JSONResponse(status_code=status.HTTP_429_TOO_MANY_REQUESTS, content=exc.as_dict())
    except SecurityRejectedError as exc:
        # 安全拒绝（限流 / 注入阻断）不是「资源不存在」，需回真实状态码。
        if exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            seconds = exc.retry_after_seconds
            headers: dict[str, str] = {}
            if seconds is not None:
                headers["Retry-After"] = str(seconds)
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"code": "rate_limited", "retry_after_seconds": seconds},
                headers=headers,
            )
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return ChatResponse(
        conversation_id=conv.id,
        reply=reply,
        model=getattr(_service.llm, "model", None),
        sources=_latest_assistant_sources(conv),
        code_results=_latest_assistant_code_results(conv),
        skill_names=_latest_assistant_skill_names(conv),
    )


@router.post(
    "/stream",
    response_class=EventSourceResponse,
    responses={
        200: {
            "description": "以 text/event-stream 持续返回对话事件",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    },
)
async def chat_stream(
    req: ChatRequest,
    current_user: User = Depends(require_permission("conversations", "write")),
    session: Session = Depends(get_db),
):
    """发起流式对话，以 SSE 增量返回管线阶段与最终回复。"""
    if not req.message.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="message 不能为空")

    def _sse(event_type: str, data: object) -> dict[str, str]:
        return {
            "event": event_type,
            "data": json.dumps({"type": event_type, "data": data}, ensure_ascii=False),
        }

    def _event_payload(event_type: str, raw: str) -> object:
        if event_type in {"sources", "code_result"}:
            return json.loads(raw) if raw else []
        if event_type == "rate_limit":
            if not raw:
                return {"retry_after_seconds": None}
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
            return {"retry_after_seconds": None}
        return raw

    stream = _service.chat_stream(session, current_user, req.message, req.conversation_id)
    try:
        first = await stream.__anext__()
    except StopAsyncIteration:
        await stream.aclose()
        return EventSourceResponse(iter(()))
    except QuotaExceededError as exc:
        await stream.aclose()
        return JSONResponse(status_code=status.HTTP_429_TOO_MANY_REQUESTS, content=exc.as_dict())
    except ValueError as exc:
        await stream.aclose()
        missing = str(exc)

        async def missing_conversation():
            yield _sse("error", missing)

        return EventSourceResponse(missing_conversation())

    async def event_generator():
        try:
            yield _sse(first.type, _event_payload(first.type, first.data))
            async for event in stream:
                yield _sse(event.type, _event_payload(event.type, event.data))
        except ValueError as exc:
            yield _sse("error", str(exc))
        except Exception:
            logger.exception("流式对话失败")
            yield _sse("error", "生成失败，请稍后重试")

    return EventSourceResponse(event_generator())


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(
    current_user: User = Depends(require_permission("conversations", "read")),
    session: Session = Depends(get_db),
) -> list[ConversationOut]:
    """列出当前用户可见的会话。"""
    convs = _service.list_conversations(session, current_user)
    return [_conv_out(c) for c in convs]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: str,
    current_user: User = Depends(require_permission("conversations", "read")),
    session: Session = Depends(get_db),
) -> ConversationDetail:
    """获取会话详情（含消息列表）。"""
    conv = _service.get_conversation(session, current_user, conversation_id)
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在或无权访问"
        )
    return _conv_detail(conv)


@router.patch("/conversations/{conversation_id}", response_model=ConversationOut)
async def rename_conversation(
    conversation_id: str,
    req: ConversationRename,
    request: Request,
    current_user: User = Depends(require_permission("conversations", "write")),
    session: Session = Depends(get_db),
) -> ConversationOut:
    """修改自己的会话标题。当前租户的系统管理员也可修改该租户内的会话。"""
    title = req.title.strip()
    if not title:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="标题不能为空")
    if len(title) > 80:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="标题不能超过 80 个字",
        )
    conv = _service.rename_conversation(session, current_user, conversation_id, title)
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在或无权访问"
        )
    await audit_event(
        request,
        AuditAction.OTHER,
        user=current_user,
        resource_type="conversation",
        resource_id=conversation_id,
        details={"action": "conversation_rename"},
    )
    return _conv_out(conv)


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    request: Request,
    current_user: User = Depends(require_permission("conversations", "delete")),
    session: Session = Depends(get_db),
) -> dict:
    """删除会话及其消息。"""
    ok = _service.delete_conversation(session, current_user, conversation_id)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在或无权访问"
        )
    await audit_event(
        request,
        AuditAction.CONVERSATION_DELETE,
        user=current_user,
        resource_type="conversation",
        resource_id=conversation_id,
    )
    return {"deleted": True}


@router.get("/tools")
async def list_tools(
    current_user: User = Depends(require_permission("agents", "read")),
) -> list[dict]:
    """列出当前可用的工具（内置 + MCP）：名称、描述与参数 Schema。

    工具清单会暴露服务端可用能力，因此要求认证与 ``agents:read`` 权限，
    不对外公开。
    """
    registry = ToolRegistry(default_tools())
    if settings.MCP_ENABLED:
        try:
            from app.mcp.manager import get_mcp_manager

            mgr = await get_mcp_manager()
            if mgr is not None:
                for tool in await mgr.collect_tools():
                    registry.register(tool)
        except Exception:  # noqa: BLE001
            logger.exception("收集 MCP 工具失败")
    return [
        {
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters,
        }
        for t in registry.all()
    ]
