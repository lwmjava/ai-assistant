"""进化候选接口：查询、拒绝、接受待办，以及把技能候选转成未启用草稿。"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from app.api.deps import get_db, require_permission
from app.evolution.candidates import (
    EvolutionCandidateError,
    accept_todo_candidate,
    get_candidate,
    list_candidates,
    promote_skill_candidate,
    reject_candidate,
)
from app.evolution.records import EvolutionCandidate
from app.models.user import User
from app.services.skill_service import SkillText

router = APIRouter(prefix="/evolution/candidates", tags=["evolution"])


class CandidateOut(BaseModel):
    """候选的可查询视图。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    status: str
    source_type: str
    source_run_id: str
    conversation_id: str | None
    tenant_id: str | None
    owner_user_id: str | None
    title: str
    description: str
    payload_json: str
    reviewed_by: str | None
    reviewed_at: object | None = None
    review_note: str | None
    skill_draft_id: str | None


class ReviewIn(BaseModel):
    """拒绝或接受时的可选说明。"""

    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=1000)


class PromoteIn(BaseModel):
    """把技能候选转成草稿时的可选字段。不填则由候选内容生成，仍须再确认才会生效。"""

    model_config = ConfigDict(extra="forbid")

    scope: Literal["private", "global"] = "private"
    note: str | None = Field(default=None, max_length=1000)
    name: str | None = None
    description: str | None = None
    keywords: list[str] | None = None
    constraints: str | None = None
    system_prompt: str | None = None
    example: str | None = None
    preconditions: str = ""
    workflow: str = ""
    tools: list[str] = Field(default_factory=list)
    escalation: str = ""
    tests: str = ""


def _http(exc: EvolutionCandidateError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _out(row: EvolutionCandidate) -> CandidateOut:
    return CandidateOut.model_validate(row)


@router.get("", response_model=list[CandidateOut])
def read_candidates(
    kind: Literal["skill", "todo"] | None = Query(default=None),
    status: Literal["pending", "rejected", "promoted", "accepted"] | None = Query(default=None),
    conversation_id: str | None = Query(default=None),
    current_user: User = Depends(require_permission("skills", "read")),
    session: Session = Depends(get_db),
) -> list[CandidateOut]:
    """列出当前用户可见的技能候选与待办。"""
    rows = list_candidates(
        session,
        current_user,
        kind=kind,
        status=status,
        conversation_id=conversation_id,
    )
    return [_out(row) for row in rows]


@router.get("/{candidate_id}", response_model=CandidateOut)
def read_candidate(
    candidate_id: str,
    current_user: User = Depends(require_permission("skills", "read")),
    session: Session = Depends(get_db),
) -> CandidateOut:
    """读取一条可见候选。"""
    try:
        return _out(get_candidate(session, current_user, candidate_id))
    except EvolutionCandidateError as exc:
        raise _http(exc) from exc


@router.post("/{candidate_id}/reject", response_model=CandidateOut)
def reject(
    candidate_id: str,
    body: ReviewIn | None = None,
    current_user: User = Depends(require_permission("skills", "write")),
    session: Session = Depends(get_db),
) -> CandidateOut:
    """拒绝一条待审候选。"""
    try:
        return _out(reject_candidate(session, current_user, candidate_id, note=(body.note if body else None)))
    except EvolutionCandidateError as exc:
        raise _http(exc) from exc


@router.post("/{candidate_id}/accept", response_model=CandidateOut)
def accept_todo(
    candidate_id: str,
    body: ReviewIn | None = None,
    current_user: User = Depends(require_permission("skills", "write")),
    session: Session = Depends(get_db),
) -> CandidateOut:
    """接受一条待办候选。只留下审核记录，不会自动创建工作流。"""
    try:
        return _out(accept_todo_candidate(session, current_user, candidate_id, note=(body.note if body else None)))
    except EvolutionCandidateError as exc:
        raise _http(exc) from exc


@router.post("/{candidate_id}/promote", response_model=CandidateOut)
def promote(
    candidate_id: str,
    body: PromoteIn | None = None,
    current_user: User = Depends(require_permission("skills", "write")),
    session: Session = Depends(get_db),
) -> CandidateOut:
    """把技能候选转成未启用草稿；仍须调用技能确认接口才会生效。"""
    payload = body or PromoteIn()
    text = None
    if any(
        value is not None
        for value in (
            payload.name,
            payload.description,
            payload.keywords,
            payload.constraints,
            payload.system_prompt,
            payload.example,
        )
    ):
        if not all(
            [
                payload.name,
                payload.description,
                payload.keywords,
                payload.constraints,
                payload.system_prompt,
                payload.example,
            ]
        ):
            raise HTTPException(
            status_code=422,
            detail="自定义草稿时需要同时提供名称、说明、关键词、约束、技能说明和示例",
        )
        text = SkillText(
            name=payload.name or "",
            description=payload.description or "",
            keywords=list(payload.keywords or []),
            constraints=payload.constraints or "",
            system_prompt=payload.system_prompt or "",
            example=payload.example or "",
        )
    try:
        return _out(
            promote_skill_candidate(
                session,
                current_user,
                candidate_id,
                scope=payload.scope,
                text=text,
                preconditions=payload.preconditions,
                workflow=payload.workflow,
                tools=payload.tools,
                escalation=payload.escalation,
                tests=payload.tests,
                note=payload.note,
            )
        )
    except EvolutionCandidateError as exc:
        raise _http(exc) from exc
