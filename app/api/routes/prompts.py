"""Prompt variant compare, publish, and rollback API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from app.api.deps import get_current_user, get_db, require_system_admin
from app.models.user import User
from app.schemas.prompt_variant import (
    PromptCompareCreate,
    PromptCompareOut,
    PromptPublicationOut,
    PromptPublishRequest,
    PromptRollbackRequest,
    PromptSlotOut,
    PromptVariantCreate,
    PromptVariantOut,
)
from app.services.prompt_variants import (
    PromptVariantAuthError,
    PromptVariantConflictError,
    PromptVariantError,
    PromptVariantService,
)

router = APIRouter(prefix="/prompt-variants", tags=["prompt-variants"])


def _service(session: Session = Depends(get_db)) -> PromptVariantService:
    return PromptVariantService(session)


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PromptVariantAuthError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, PromptVariantConflictError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, PromptVariantError):
        code = status.HTTP_404_NOT_FOUND if "not_found" in str(exc) else status.HTTP_400_BAD_REQUEST
        return HTTPException(status_code=code, detail=str(exc))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="prompt_variant_error")


@router.get("/slots", response_model=list[PromptSlotOut])
def list_slots(
    _: User = Depends(get_current_user),
    service: PromptVariantService = Depends(_service),
) -> list[PromptSlotOut]:
    return service.list_slots()


@router.get("/slots/{slot_key}/variants", response_model=list[PromptVariantOut])
def list_variants(
    slot_key: str,
    _: User = Depends(get_current_user),
    service: PromptVariantService = Depends(_service),
) -> list[PromptVariantOut]:
    try:
        return service.list_variants(slot_key)
    except PromptVariantError as exc:
        raise _map_error(exc) from exc


@router.post("/slots/{slot_key}/variants", response_model=PromptVariantOut)
def create_variant(
    slot_key: str,
    body: PromptVariantCreate,
    user: User = Depends(get_current_user),
    service: PromptVariantService = Depends(_service),
) -> PromptVariantOut:
    try:
        return service.create_variant(
            user,
            slot_key=slot_key,
            label=body.label,
            body=body.body,
            notes=body.notes,
        )
    except PromptVariantError as exc:
        raise _map_error(exc) from exc


@router.get("/slots/{slot_key}/publication", response_model=PromptPublicationOut | None)
def get_publication(
    slot_key: str,
    _: User = Depends(get_current_user),
    service: PromptVariantService = Depends(_service),
) -> PromptPublicationOut | None:
    try:
        return service.get_publication(slot_key)
    except PromptVariantError as exc:
        raise _map_error(exc) from exc


@router.post("/slots/{slot_key}/publish", response_model=PromptPublicationOut)
def publish_variant(
    slot_key: str,
    body: PromptPublishRequest,
    user: User = Depends(require_system_admin),
    service: PromptVariantService = Depends(_service),
) -> PromptPublicationOut:
    try:
        return service.publish(
            user,
            slot_key=slot_key,
            variant_id=body.variant_id,
            expected_revision=body.expected_revision,
            note=body.note,
            compare_run_id=body.compare_run_id,
        )
    except (PromptVariantError, PromptVariantConflictError, PromptVariantAuthError) as exc:
        raise _map_error(exc) from exc


@router.post("/slots/{slot_key}/rollback", response_model=PromptPublicationOut)
def rollback_variant(
    slot_key: str,
    body: PromptRollbackRequest,
    user: User = Depends(require_system_admin),
    service: PromptVariantService = Depends(_service),
) -> PromptPublicationOut:
    try:
        return service.rollback(
            user,
            slot_key=slot_key,
            expected_revision=body.expected_revision,
            to_variant_id=body.to_variant_id,
            note=body.note,
        )
    except (PromptVariantError, PromptVariantConflictError, PromptVariantAuthError) as exc:
        raise _map_error(exc) from exc


@router.post("/compares", response_model=PromptCompareOut)
def create_compare(
    body: PromptCompareCreate,
    user: User = Depends(get_current_user),
    service: PromptVariantService = Depends(_service),
) -> PromptCompareOut:
    try:
        return service.compare(
            user,
            slot_key=body.slot_key,
            baseline_variant_id=body.baseline_variant_id,
            candidate_variant_id=body.candidate_variant_id,
            dataset_id=body.dataset_id,
            dataset_version_id=body.dataset_version_id,
            provider=body.provider,
            max_external_calls=body.max_external_calls,
        )
    except PromptVariantError as exc:
        raise _map_error(exc) from exc


@router.get("/compares/{compare_id}", response_model=PromptCompareOut)
def get_compare(
    compare_id: str,
    user: User = Depends(get_current_user),
    service: PromptVariantService = Depends(_service),
) -> PromptCompareOut:
    try:
        return service.get_compare(user, compare_id)
    except PromptVariantError as exc:
        raise _map_error(exc) from exc
