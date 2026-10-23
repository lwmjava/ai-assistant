"""系统管理员的功能开关 API。"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from app.api.deps import get_current_user, get_db
from app.core.security import Role
from app.models.user import User
from app.schemas.feature_flags import FeatureFlagOut, FeatureFlagUpdate
from app.services.feature_flags import get_flag, list_flags, set_flag

router = APIRouter(prefix="/admin/features", tags=["admin-features"])


def _require_system_admin(user: User = Depends(get_current_user)) -> User:
    if user.role_enum is not Role.SYSTEM_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="仅系统管理员可管理功能开关",
        )
    return user


@router.get("", response_model=list[FeatureFlagOut])
def list_feature_flags(
    session: Session = Depends(get_db),
    _: User = Depends(_require_system_admin),
) -> list[FeatureFlagOut]:
    return [FeatureFlagOut.model_validate(item) for item in list_flags(session)]


@router.patch("/{flag_key}", response_model=FeatureFlagOut)
def patch_feature_flag(
    flag_key: str,
    body: FeatureFlagUpdate,
    session: Session = Depends(get_db),
    current_user: User = Depends(_require_system_admin),
) -> FeatureFlagOut:
    try:
        view = set_flag(
            session,
            flag_key,
            body.value,
            actor_id=current_user.id,
            actor_tenant_id=current_user.tenant_id,
        )
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未知的功能开关")
    except RuntimeError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="功能开关变更未能写入审计，未修改",
        )
    return FeatureFlagOut.model_validate(view)


@router.get("/{flag_key}", response_model=FeatureFlagOut)
def get_feature_flag(
    flag_key: str,
    session: Session = Depends(get_db),
    _: User = Depends(_require_system_admin),
) -> FeatureFlagOut:
    view = get_flag(session, flag_key)
    if view is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未知的功能开关")
    return FeatureFlagOut.model_validate(view)
