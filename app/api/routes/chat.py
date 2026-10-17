"""对话接口：发起对话、流式对话、会话管理。

所有接口均需认证，并按角色校验 ``conversations`` 资源权限（依赖 require_permission）。
权限判定只区分「该角色能否做这类操作」，归属校验在会话服务内完成：
普通用户仅能访问自己的会话，系统管理员可见同租户全部。
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlmodel import Session
from sse_starlette.sse import EventSourceResponse

from app.agents.tools.base import ToolRegistry
from app.agents.tools.builtin import default_tools
from app.api.deps import audit_event, get_db, require_permission
from app.api.errors import PublicHTTPError, error_responses, public_events, public_response
from app.audit.models import AuditAction
from app.core.config import settings
from app.core.log_context import get_trace_id
from app.models.conversation import Conversation
from app.models.user import User
from app.security.types import SecurityRejectedError
from app.services.active_runs import (
    RunAccessError,
    RunCapacityError,
    RunConflictError,
    active_runs,
)
from app.services.agent_execution_guard import ResumeRejectedError
from app.services.chat_attachments import (
    MAX_ATTACHMENT_BYTES,
    AttachmentInput,
    AttachmentTooLargeError,
    AttachmentValidationError,
    attachment_summaries,
)
from app.services.chat_service import ChatService, ModelUnavailableError
from app.services.quota import QuotaExceededError

logger = logging.getLogger(__name__)

@asynccontextmanager
async def run_lifespan(_app):
    yield
    await active_runs.shutdown()


router = APIRouter(prefix="/chat", tags=["chat"], lifespan=run_lifespan)
runs_router = APIRouter(prefix="/runs", tags=["runs"])

_service = ChatService()


class ChatRequest(BaseModel):
    """对话请求体。"""

    message: str
    attachments: list[AttachmentInput] = Field(default_factory=list)
    run_id: UUID | None = Field(default=None, description="客户端预生成的唯一运行 ID；重复提交拒绝")
    conversation_id: str | None = None
    resume_message_id: str | None = Field(default=None, min_length=1, description="恢复同一次未完成执行的原用户消息 ID")

    @model_validator(mode="after")
    def validate_resume_binding(self):
        if self.resume_message_id is not None and not self.conversation_id:
            raise ValueError("续跑需要 conversation_id")
        return self


class RunOut(BaseModel):
    """当前主体运行的稳定查询与取消响应，不含内部错误或模型输入。"""

    run_id: str
    status: Literal["running", "done", "failed", "cancelled"]
    conversation_id: str | None = None
    message_id: str | None = None
    assistant_message_id: str | None = None
    reply: str = ""


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
    message_id: str | None = Field(default=None, description="本次执行对应的用户消息 ID")
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


class AttachmentOut(BaseModel):
    """历史仅显示附件摘要，不返回图片或文档正文。"""

    name: str
    size: int
    mime_type: str


class MessageOut(BaseModel):
    """消息概要。"""

    id: str
    role: str
    content: str
    attachments: list[AttachmentOut] = Field(default_factory=list)
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
            attachments=[AttachmentOut(**item) for item in attachment_summaries(m.attachments)],
        )
        for m in sorted(conv.messages, key=lambda x: x.created_at)
    ]
    base = _conv_out(conv).model_dump()
    base["messages"] = messages
    return ConversationDetail(**base)


@router.post("", response_model=ChatResponse, responses={
    **error_responses("session_expired", "user_disabled", "tenant_suspended",
                      "rate_limited", "quota_exceeded", "model_unavailable", "file_too_large"),
    400: {"description": "消息为空或恢复内容不一致"},
    404: {"description": "恢复会话或消息不存在或无权访问"},
    409: {"description": "执行仍在进行、快照不可恢复或当前编排不支持恢复"},
})
async def chat(
    req: ChatRequest,
    current_user: User = Depends(require_permission("conversations", "write")),
    session: Session = Depends(get_db),
) -> ChatResponse | JSONResponse:
    """发起一次非流式对话。"""
    if not req.message.strip() and not req.attachments and not req.resume_message_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="message 不能为空")
    try:
        conv, reply = await _service.chat(
            session, current_user, req.message, req.conversation_id,
            **({"resume_message_id": req.resume_message_id} if req.resume_message_id is not None else {}),
            **({"attachments": req.attachments} if req.attachments else {}),
        )
    except AttachmentTooLargeError as exc:
        raise PublicHTTPError(413, "file_too_large", detail=str(exc),
                              extra={"limit_bytes": MAX_ATTACHMENT_BYTES}) from exc
    except AttachmentValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ResumeRejectedError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except ModelUnavailableError as exc:
        raise PublicHTTPError(502, "model_unavailable") from exc
    except QuotaExceededError as exc:
        return public_response(429, "quota_exceeded", legacy=exc.as_dict())
    except SecurityRejectedError as exc:
        # 安全拒绝（限流 / 注入阻断）不是「资源不存在」，需回真实状态码。
        if exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            seconds = exc.retry_after_seconds
            headers: dict[str, str] = {}
            if seconds is not None:
                headers["Retry-After"] = str(seconds)
            return public_response(429, "rate_limited",
                                   legacy={"code": "rate_limited", "retry_after_seconds": seconds},
                                   retry_after=seconds, headers=headers)
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return ChatResponse(
        conversation_id=conv.id,
        message_id=session.info.get("agent_message_id"),
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
        **error_responses("session_expired", "user_disabled", "tenant_suspended",
                          "rate_limited", "quota_exceeded", "file_too_large"),
        400: {"description": "消息为空或恢复内容不一致"},
        404: {"description": "恢复会话或消息不存在或无权访问"},
        409: {"description": "同主体运行编号重复、回放冲突或快照不可恢复"},
        503: {"description": "活动运行容量已满，稍后重试；不会取消已有运行"},
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
    if not req.message.strip() and not req.attachments and not req.resume_message_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="message 不能为空")

    try:
        prepared = _service.prepare_chat_attachments(
            session, current_user, req.message, req.conversation_id, req.resume_message_id, req.attachments
        )
    except AttachmentTooLargeError as exc:
        raise PublicHTTPError(413, "file_too_large", detail=str(exc),
                              extra={"limit_bytes": MAX_ATTACHMENT_BYTES}) from exc
    except AttachmentValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ResumeRejectedError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except SecurityRejectedError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    def produce(run_session, user):
        return _service.chat_stream(
            run_session, user, req.message, req.conversation_id,
            **({"resume_message_id": req.resume_message_id} if req.resume_message_id is not None else {}),
            **({"attachments_prepared": prepared} if prepared else {}),
        )

    try:
        run = active_runs.create(
            current_user, session.get_bind(), produce,
            run_id=str(req.run_id) if req.run_id else None, conversation_id=req.conversation_id,
        )
    except RunAccessError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RunConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RunCapacityError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    await asyncio.shield(run.ready.wait())
    if isinstance(run.initial_error, ResumeRejectedError):
        raise HTTPException(status_code=run.initial_error.status_code, detail=str(run.initial_error))
    if isinstance(run.initial_error, QuotaExceededError):
        return public_response(429, "quota_exceeded", legacy=run.initial_error.as_dict())
    return EventSourceResponse(
        public_events(active_runs.subscribe(run, current_user, 0), get_trace_id()),
        headers={"X-Run-ID": run.run_id}, ping=None)


def _owned_run(run_id: str, current_user: User):
    try:
        return active_runs.get(run_id, current_user)
    except RunAccessError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@runs_router.get("/{run_id}", response_model=RunOut, responses={
    404: {"description": "运行不存在、已过期或当前身份无权访问"},
})
async def get_run(run_id: str, current_user: User = Depends(require_permission("conversations", "read"))):
    """读取当前主体拥有的运行状态与已安全持久化的最终回复。"""
    return _owned_run(run_id, current_user).result()


@runs_router.get("/{run_id}/events", response_class=EventSourceResponse, responses={
    200: {"description": "按事件位置续订运行", "content": {"text/event-stream": {"schema": {"type": "string"}}}},
    409: {"description": "事件位置无效或回放窗口已过期，请查询最终结果"},
    404: {"description": "运行不存在、已过期或当前身份无权访问"},
})
async def run_events(run_id: str, request: Request,
               current_user: User = Depends(require_permission("conversations", "read"))):
    """续订已有运行，不再次执行消息、模型或工具。"""
    run = _owned_run(run_id, current_user)
    try:
        after = active_runs.cursor(run, request.headers.get("Last-Event-ID"))
    except RunConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return EventSourceResponse(
        public_events(active_runs.subscribe(run, current_user, after), get_trace_id()),
        headers={"X-Run-ID": run.run_id}, ping=None
    )


@runs_router.post("/{run_id}/cancel", response_model=RunOut, responses={
    404: {"description": "运行不存在、已过期或当前身份无权访问"},
})
async def cancel_run(run_id: str, current_user: User = Depends(require_permission("conversations", "write"))):
    """显式停止当前主体的运行；断开订阅本身不会执行取消。"""
    run = _owned_run(run_id, current_user)
    await active_runs.cancel(run, current_user)
    return run.result()


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
