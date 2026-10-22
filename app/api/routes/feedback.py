"""Authorized feedback submission, review and deletion."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlmodel import Session, col, select

from app.api.deps import audit_event, get_db, require_permission
from app.audit.models import AuditAction
from app.models.feedback import ChatFeedback
from app.models.user import User
from app.schemas.feedback import (
    AttemptOut,
    FailureFeedbackIn,
    FeedbackOut,
    FeedbackPage,
    MessageRatingIn,
    RatingOut,
    RatingPage,
)
from app.services.chat_feedback import (
    FeedbackAccessError,
    FeedbackConflictError,
    FeedbackService,
    FeedbackValidationError,
)


class FeedbackRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request: Request):
            try:
                return await original(request)
            except RequestValidationError:
                return JSONResponse({"detail": "反馈参数无效"}, status_code=422)

        return handle


router = APIRouter(prefix="/chat", tags=["feedback"], route_class=FeedbackRoute)
reader = require_permission("conversations", "read")
writer = require_permission("conversations", "write")


def service(session: Session = Depends(get_db)) -> FeedbackService:
    return FeedbackService(session)


def public_error(exc: Exception) -> HTTPException:
    if isinstance(exc, FeedbackAccessError):
        return HTTPException(404, "记录不存在")
    return HTTPException(422, "该记录状态、反馈类型或评论无效")


@router.get("/attempts/{attempt_id}", response_model=AttemptOut)
def get_attempt(attempt_id: str, user: User = Depends(reader), svc: FeedbackService = Depends(service)):
    try:
        row = svc.owned_attempt(user, attempt_id)
    except FeedbackAccessError as exc:
        raise public_error(exc) from None
    submitted = svc.session.exec(
        select(ChatFeedback).where(col(ChatFeedback.attempt_id) == row.id, col(ChatFeedback.user_id) == user.id)
    ).first()
    return AttemptOut.model_validate(row).model_copy(
        update={"feedback_submitted": submitted is not None, "feedback_id": submitted.id if submitted else None}
    )


@router.post("/feedback", response_model=FeedbackOut)
async def post_feedback(
    body: FailureFeedbackIn, request: Request, user: User = Depends(writer), svc: FeedbackService = Depends(service)
):
    try:
        row = svc.submit_failure(user, body.attempt_id, body.kind, body.comment)
    except (FeedbackAccessError, FeedbackConflictError, FeedbackValidationError) as exc:
        raise public_error(exc) from None
    await audit_event(request, AuditAction.OTHER, user, "chat_feedback", row.id, {"signal": row.kind})
    return row


@router.post("/ratings", response_model=RatingOut)
async def post_rating(
    body: MessageRatingIn, request: Request, user: User = Depends(writer), svc: FeedbackService = Depends(service)
):
    try:
        row = svc.submit_rating(user, body.assistant_message_id, body.rating, body.reason_code, body.comment)
    except (FeedbackAccessError, FeedbackConflictError, FeedbackValidationError) as exc:
        raise public_error(exc) from None
    await audit_event(request, AuditAction.OTHER, user, "message_rating", row.id, {"signal": row.rating})
    return row


@router.get("/feedback", response_model=FeedbackPage)
def list_feedback(
    tenant_id: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    user: User = Depends(reader),
    svc: FeedbackService = Depends(service),
):
    try:
        rows, total = svc.list_records(user, "failure", tenant_id=tenant_id, page=page, page_size=page_size)
    except FeedbackAccessError as exc:
        raise public_error(exc) from None
    return FeedbackPage(
        items=[FeedbackOut.model_validate(row) for row in rows], total=total, page=page, page_size=page_size
    )


@router.get("/ratings", response_model=RatingPage)
def list_ratings(
    tenant_id: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    user: User = Depends(reader),
    svc: FeedbackService = Depends(service),
):
    try:
        rows, total = svc.list_records(user, "rating", tenant_id=tenant_id, page=page, page_size=page_size)
    except FeedbackAccessError as exc:
        raise public_error(exc) from None
    return RatingPage(
        items=[RatingOut.model_validate(row) for row in rows], total=total, page=page, page_size=page_size
    )


async def delete_record(request: Request, user: User, svc: FeedbackService, kind: str, record_id: str):
    try:
        svc.delete(user, kind, record_id)
    except FeedbackAccessError as exc:
        raise public_error(exc) from None
    await audit_event(
        request,
        AuditAction.OTHER,
        user,
        "chat_feedback" if kind == "failure" else "message_rating",
        record_id,
        {"action": "delete"},
    )
    return {"deleted": True}


@router.delete("/feedback/{record_id}")
async def delete_feedback(
    record_id: str, request: Request, user: User = Depends(writer), svc: FeedbackService = Depends(service)
):
    return await delete_record(request, user, svc, "failure", record_id)


@router.delete("/ratings/{record_id}")
async def delete_rating(
    record_id: str, request: Request, user: User = Depends(writer), svc: FeedbackService = Depends(service)
):
    return await delete_record(request, user, svc, "rating", record_id)
