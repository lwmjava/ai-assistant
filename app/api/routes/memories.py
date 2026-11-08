"""当前用户查看、纠正、失效和导出自己的跨会话记忆。"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from app.api.deps import audit_event, get_db, require_permission
from app.audit.models import AuditAction
from app.memory.cross_session import CrossSessionMemoryService
from app.models.cross_session_memory import CrossSessionMemory
from app.models.user import User
from app.services.memory_policy import MemoryPolicyError

router = APIRouter(prefix="/memories", tags=["memories"])


class MemorySwitchUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class MemorySwitchOut(BaseModel):
    deployment_enabled: bool
    user_enabled: bool
    effective: bool


class MemoryOut(BaseModel):
    id: str
    source_grade: str
    content: str
    source_conversation_id: str
    source_message_id: str
    supersedes_id: str | None
    conflict_group_id: str | None
    invalidated: bool


class MemoryCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=4000)


class MemoryExportItem(BaseModel):
    id: str
    source_grade: str
    content: str
    source_conversation_id: str
    source_message_id: str
    supersedes_id: str | None
    superseded: bool
    conflict_group_id: str | None


def _memory_out(row: CrossSessionMemory) -> MemoryOut:
    return MemoryOut(
        id=row.id,
        source_grade=row.source_grade,
        content=row.content,
        source_conversation_id=row.source_conversation_id,
        source_message_id=row.source_message_id,
        supersedes_id=row.supersedes_id,
        conflict_group_id=row.conflict_group_id,
        invalidated=row.invalidated_at is not None,
    )


def _reject(exc: MemoryPolicyError) -> HTTPException:
    text = str(exc)
    if text == "memory writes are disabled":
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="跨会话记忆写入已关闭")
    if text == "memory contains personal data":
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="内容含个人敏感信息，未保存")
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="记忆不存在")


@router.get("/settings", response_model=MemorySwitchOut)
async def read_memory_switch(
    current_user: User = Depends(require_permission("conversations", "read")),
    session: Session = Depends(get_db),
) -> MemorySwitchOut:
    """读取部署开关和当前用户自己的读写开关。"""
    try:
        view = CrossSessionMemoryService(session).describe_switch(current_user)
    except MemoryPolicyError as exc:
        raise _reject(exc) from exc
    return MemorySwitchOut(**view)


@router.put("/settings", response_model=MemorySwitchOut)
async def update_memory_switch(
    body: MemorySwitchUpdate,
    request: Request,
    current_user: User = Depends(require_permission("conversations", "write")),
    session: Session = Depends(get_db),
) -> MemorySwitchOut:
    """关闭或恢复当前用户自己的读写。不能打开已关闭的部署开关。"""
    try:
        view = CrossSessionMemoryService(session).set_switch(current_user, body.enabled)
    except MemoryPolicyError as exc:
        raise _reject(exc) from exc
    await audit_event(
        request,
        AuditAction.OTHER,
        current_user,
        resource_type="memory",
        resource_id=current_user.id,
        details={"operation": "switch", "user_enabled": body.enabled},
    )
    return MemorySwitchOut(**view)


@router.get("", response_model=list[MemoryOut])
async def list_memories(
    current_user: User = Depends(require_permission("conversations", "read")),
    session: Session = Depends(get_db),
) -> list[MemoryOut]:
    """列出当前用户仍有效的记忆。不含别人的记录，也不含已失效或已被纠正替代的版本。"""
    try:
        rows = CrossSessionMemoryService(session).list_visible(current_user)
    except MemoryPolicyError as exc:
        raise _reject(exc) from exc
    return [_memory_out(row) for row in rows]


@router.get("/export", response_model=list[MemoryExportItem])
async def export_memories(
    request: Request,
    current_user: User = Depends(require_permission("conversations", "read")),
    session: Session = Depends(get_db),
) -> list[MemoryExportItem]:
    """导出当前用户未失效的记忆。正文已脱敏，不含嵌入向量。"""
    try:
        rows = CrossSessionMemoryService(session).export_visible(current_user)
    except MemoryPolicyError as exc:
        raise _reject(exc) from exc
    await audit_event(
        request,
        AuditAction.OTHER,
        current_user,
        resource_type="memory",
        resource_id=current_user.id,
        details={"operation": "export", "count": len(rows)},
    )
    return [MemoryExportItem.model_validate(row) for row in rows]


@router.get("/{memory_id}", response_model=MemoryOut)
async def read_memory(
    memory_id: str,
    current_user: User = Depends(require_permission("conversations", "read")),
    session: Session = Depends(get_db),
) -> MemoryOut:
    """读取当前用户的一条仍有效记忆。别人的记录和已失效记录都按不存在返回。"""
    try:
        row = CrossSessionMemoryService(session).get_visible(current_user, memory_id)
    except MemoryPolicyError as exc:
        raise _reject(exc) from exc
    return _memory_out(row)


@router.post("/{memory_id}/corrections", response_model=MemoryOut)
async def correct_memory(
    memory_id: str,
    body: MemoryCorrection,
    request: Request,
    current_user: User = Depends(require_permission("conversations", "write")),
    session: Session = Depends(get_db),
) -> MemoryOut:
    """用新正文纠正自己的记忆。旧版本保留，相同正文不重复写入。"""
    service = CrossSessionMemoryService(session)
    try:
        new_id = await service.correct_text(current_user, memory_id, body.content)
        row = service.get_visible(current_user, new_id)
    except MemoryPolicyError as exc:
        raise _reject(exc) from exc
    await audit_event(
        request,
        AuditAction.OTHER,
        current_user,
        resource_type="memory",
        resource_id=new_id,
        details={"operation": "correct", "previous_id": memory_id},
    )
    return _memory_out(row)


@router.delete("/{memory_id}", response_model=MemoryOut)
async def invalidate_memory(
    memory_id: str,
    request: Request,
    current_user: User = Depends(require_permission("conversations", "delete")),
    session: Session = Depends(get_db),
) -> MemoryOut:
    """让自己的一条记忆不再被检索。记录保留，重复删除结果相同。"""
    try:
        row = CrossSessionMemoryService(session).invalidate(current_user, memory_id)
    except MemoryPolicyError as exc:
        raise _reject(exc) from exc
    await audit_event(
        request,
        AuditAction.OTHER,
        current_user,
        resource_type="memory",
        resource_id=memory_id,
        details={"operation": "invalidate"},
    )
    return _memory_out(row)
