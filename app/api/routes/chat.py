"""对话接口：发起对话、流式对话、会话管理。

所有接口均需认证，并按角色校验 ``conversations`` 资源权限（依赖 require_permission）。
权限判定只区分「该角色能否做这类操作」，归属校验在会话服务内完成：
普通用户仅能访问自己的会话，系统管理员可见同租户全部。
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from sqlmodel import Session

from app.api.deps import audit_event, get_db, require_permission
from app.audit.models import AuditAction
from app.core.config import settings
from app.security.types import SecurityRejectedError
from app.models.conversation import Conversation
from app.models.user import User
from app.services.chat_service import ChatService
from app.agents.tools.base import ToolRegistry
from app.agents.tools.builtin import default_tools

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

_service = ChatService()


class ChatRequest(BaseModel):
    """对话请求体。"""

    message: str
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    """非流式对话响应。"""

    conversation_id: str
    reply: str
    model: str | None = None


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


class ConversationDetail(ConversationOut):
    """会话详情（含消息列表）。"""

    messages: list[MessageOut]


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
    except SecurityRejectedError as exc:
        # 安全拒绝（限流 / 注入阻断）不是「资源不存在」，需回真实状态码。
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return ChatResponse(
        conversation_id=conv.id, reply=reply, model=getattr(_service.llm, "model", None)
    )


@router.post("/stream")
async def chat_stream(
    req: ChatRequest,
    current_user: User = Depends(require_permission("conversations", "write")),
    session: Session = Depends(get_db),
):
    """发起流式对话，以 SSE 增量返回管线阶段与最终回复。"""
    if not req.message.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="message 不能为空")

    async def event_generator():
        try:
            async for event in _service.chat_stream(
                session, current_user, req.message, req.conversation_id
            ):
                yield {
                    "event": event.type,
                    "data": json.dumps(
                        {"type": event.type, "data": event.data}, ensure_ascii=False
                    ),
                }
        except ValueError as exc:
            yield {
                "event": "error",
                "data": json.dumps(
                    {"type": "error", "data": str(exc)}, ensure_ascii=False
                ),
            }

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
