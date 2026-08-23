"""认证路由：登录、刷新令牌、当前用户。"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from app.api.deps import get_current_user, get_db
from app.core.security import (
    TokenRevokedError,
    create_access_token,
    create_refresh_token,
    verify_refresh_token,
)
from app.models.user import User
from app.schemas.auth import LoginRequest, RefreshRequest, Token, UserInfo
from app.services.auth_service import authenticate

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=Token)
def login(body: LoginRequest, session: Session = Depends(get_db)) -> Token:
    """用户名密码登录，返回 access + refresh 双令牌。"""
    user = authenticate(session, body.username, body.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )
    access_token = create_access_token(user.id, user.tenant_id, user.role)
    refresh_token = create_refresh_token(user.id, user.token_version)
    return Token(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=Token)
def refresh(body: RefreshRequest, session: Session = Depends(get_db)) -> Token:
    """使用 refresh_token 换取新的双令牌（refresh 轮转）。"""
    try:
        payload = verify_refresh_token(body.refresh_token, 0)
    except TokenRevokedError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="refresh_token 无效或已过期")

    user_id = payload.get("sub")
    user = session.get(User, user_id) if user_id else None
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已禁用")

    # 用最新 token_version 复核撤销状态。
    try:
        verify_refresh_token(body.refresh_token, user.token_version)
    except TokenRevokedError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))

    access_token = create_access_token(user.id, user.tenant_id, user.role)
    refresh_token = create_refresh_token(user.id, user.token_version)
    return Token(access_token=access_token, refresh_token=refresh_token)


@router.get("/me", response_model=UserInfo)
def me(current_user: User = Depends(get_current_user)) -> UserInfo:
    """返回当前登录用户信息。"""
    return UserInfo.model_validate(current_user)
