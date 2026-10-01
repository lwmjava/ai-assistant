"""技能接口：列表、详情、创建、修改、启用、停用和删除。"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from app.api.deps import audit_event, get_db, require_permission
from app.audit.models import AuditAction
from app.models.user import User
from app.services.skill_service import (
    SkillRequestError,
    SkillRowView,
    SkillText,
    audit_details,
    create_skill,
    delete_skill,
    get_skill,
    list_skills,
    set_enabled,
    update_skill,
)

router = APIRouter(prefix="/skills", tags=["skills"])

_ACTIONS = {
    "skill_create": AuditAction.SKILL_CREATE,
    "skill_update": AuditAction.SKILL_UPDATE,
    "skill_disable": AuditAction.SKILL_DISABLE,
    "skill_enable": AuditAction.SKILL_ENABLE,
    "skill_delete": AuditAction.SKILL_DELETE,
}


class SkillWrite(BaseModel):
    """创建和修改共用的正文字段。不接收 tools。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    keywords: list[str] = Field(min_length=1)
    constraints: str
    system_prompt: str
    example: str


class SkillCreate(SkillWrite):
    """创建时可选范围。默认私有。"""

    scope: Literal["private", "global"] = "private"


class SkillListOut(BaseModel):
    """列表和写操作响应。不含技能正文。"""

    id: str
    tenant_id: str
    owner_id: str
    owner_username: str | None = None
    tenant_name: str | None = None
    name: str
    description: str
    keywords: list[str]
    source: str
    scope: str
    enabled: bool


class SkillDetailOut(SkillListOut):
    """详情才返回约束、说明和示例。"""

    constraints: str = ""
    system_prompt: str = ""
    example: str = ""
    version: str = "1.0"


def _text(body: SkillWrite) -> SkillText:
    return SkillText(
        name=body.name.strip(),
        description=body.description,
        keywords=list(body.keywords),
        constraints=body.constraints,
        system_prompt=body.system_prompt,
        example=body.example,
    )


def _list_out(view: SkillRowView) -> SkillListOut:
    return SkillListOut(
        id=view.id,
        tenant_id=view.tenant_id,
        owner_id=view.owner_id,
        owner_username=view.owner_username,
        tenant_name=view.tenant_name,
        name=view.name,
        description=view.description,
        keywords=view.keywords,
        source=view.source,
        scope=view.scope,
        enabled=view.enabled,
    )


def _detail_out(view: SkillRowView) -> SkillDetailOut:
    base = _list_out(view).model_dump()
    base.update(
        constraints=view.constraints,
        system_prompt=view.system_prompt,
        example=view.example,
        version=view.version,
    )
    return SkillDetailOut(**base)


def _http(exc: SkillRequestError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.get("", response_model=list[SkillListOut])
def read_skills(
    q: str | None = None,
    tenant_id: str | None = None,
    owner_id: str | None = None,
    scope: Literal["private", "global", "builtin"] | None = None,
    enabled: bool | None = Query(default=None),
    current_user: User = Depends(require_permission("skills", "read")),
    session: Session = Depends(get_db),
) -> list[SkillListOut]:
    """列出当前用户能看的技能。"""
    try:
        rows = list_skills(
            session,
            current_user,
            q=q,
            tenant_id=tenant_id,
            owner_id=owner_id,
            scope=scope,
            enabled=enabled,
        )
    except SkillRequestError as exc:
        raise _http(exc) from exc
    return [_list_out(row) for row in rows]


@router.post("", response_model=SkillListOut, status_code=status.HTTP_201_CREATED)
async def add_skill(
    body: SkillCreate,
    request: Request,
    current_user: User = Depends(require_permission("skills", "write")),
    session: Session = Depends(get_db),
) -> SkillListOut:
    """创建私有技能，或由系统管理员创建系统全局技能。"""
    try:
        change = create_skill(session, current_user, _text(body), body.scope)
    except SkillRequestError as exc:
        raise _http(exc) from exc
    await _audit(request, current_user, change.action, change.view)
    return _list_out(change.view)


@router.get("/{skill_id}", response_model=SkillDetailOut)
def read_skill(
    skill_id: str,
    current_user: User = Depends(require_permission("skills", "read")),
    session: Session = Depends(get_db),
) -> SkillDetailOut:
    """读取一条技能的正文。"""
    try:
        return _detail_out(get_skill(session, current_user, skill_id))
    except SkillRequestError as exc:
        raise _http(exc) from exc


@router.patch("/{skill_id}", response_model=SkillListOut)
async def edit_skill(
    skill_id: str,
    body: SkillWrite,
    request: Request,
    current_user: User = Depends(require_permission("skills", "write")),
    session: Session = Depends(get_db),
) -> SkillListOut:
    """修改技能正文。响应仍不回显正文。"""
    try:
        change = update_skill(session, current_user, skill_id, _text(body))
    except SkillRequestError as exc:
        raise _http(exc) from exc
    await _audit(request, current_user, change.action, change.view)
    return _list_out(change.view)


@router.post("/{skill_id}/disable", response_model=SkillListOut)
async def disable_skill(
    skill_id: str,
    request: Request,
    current_user: User = Depends(require_permission("skills", "write")),
    session: Session = Depends(get_db),
) -> SkillListOut:
    """停用一条技能。重复停用仍返回当前状态。"""
    return await _set_flag(skill_id, False, request, current_user, session)


@router.post("/{skill_id}/enable", response_model=SkillListOut)
async def enable_skill(
    skill_id: str,
    request: Request,
    current_user: User = Depends(require_permission("skills", "write")),
    session: Session = Depends(get_db),
) -> SkillListOut:
    """启用一条技能。重复启用仍返回当前状态。"""
    return await _set_flag(skill_id, True, request, current_user, session)


@router.delete("/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_skill(
    skill_id: str,
    request: Request,
    current_user: User = Depends(require_permission("skills", "delete")),
    session: Session = Depends(get_db),
) -> Response:
    """删除一条技能。"""
    try:
        change = delete_skill(session, current_user, skill_id)
    except SkillRequestError as exc:
        raise _http(exc) from exc
    await _audit(request, current_user, change.action, change.view)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _set_flag(
    skill_id: str,
    enabled: bool,
    request: Request,
    current_user: User,
    session: Session,
) -> SkillListOut:
    try:
        change = set_enabled(session, current_user, skill_id, enabled)
    except SkillRequestError as exc:
        raise _http(exc) from exc
    if change.audit:
        await _audit(request, current_user, change.action, change.view)
    return _list_out(change.view)


async def _audit(request: Request, user: User, action: str, view: SkillRowView) -> None:
    await audit_event(
        request,
        _ACTIONS[action],
        user=user,
        resource_type="skill",
        resource_id=view.id,
        details=audit_details(view),
    )
