"""认证路由：登录、刷新令牌、当前用户、公开注册、初始化向导、撤销刷新令牌。

登录、刷新、注册和撤销是安全敏感事件，成功与失败均写入审计日志：
失败尝试需要留痕以便发现撞库与凭据填充攻击。审计细节不写密码。
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session

from app.api.deps import audit_event, get_current_user, get_db
from app.audit.models import AuditAction
from app.core.security import (
    Role,
    TokenRevokedError,
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_refresh_token,
)
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    RevokeTokensResult,
    SetupStatus,
    Token,
    UserInfo,
)
from app.services.auth_service import (
    DefaultTenantInactiveError,
    EmailTakenError,
    SetupClosedError,
    UsernameTakenError,
    UserNotFoundError,
    authenticate,
    register_member,
    revoke_refresh_tokens,
    setup_system_admin,
    system_admin_exists,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _issue_token(user: User) -> Token:
    """按用户当前租户和 token_version 签发双令牌。"""
    return Token(
        access_token=create_access_token(user.id, user.tenant_id, user.role),
        refresh_token=create_refresh_token(user.id, user.token_version),
    )


def _require_system_admin(user: User = Depends(get_current_user)) -> User:
    """撤销刷新令牌只对系统管理员开放。"""
    if user.role_enum is not Role.SYSTEM_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="仅系统管理员可撤销令牌",
        )
    return user


@router.post("/login", response_model=Token)
async def login(
    body: LoginRequest, request: Request, session: Session = Depends(get_db)
) -> Token:
    """用户名密码登录，返回 access + refresh 双令牌。"""
    user = authenticate(session, body.username, body.password)
    if user is None:
        await audit_event(
            request,
            AuditAction.USER_LOGIN,
            user=None,
            details={"username": body.username, "success": False},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )
    await audit_event(
        request,
        AuditAction.USER_LOGIN,
        user=user,
        resource_type="user",
        resource_id=user.id,
        details={"username": user.username, "success": True},
    )
    return _issue_token(user)


@router.post("/refresh", response_model=Token)
async def refresh(
    body: RefreshRequest, request: Request, session: Session = Depends(get_db)
) -> Token:
    """使用 refresh_token 换取新的双令牌（refresh 轮转）。"""
    try:
        payload = decode_token(body.refresh_token)
    except Exception:
        await audit_event(
            request,
            AuditAction.USER_TOKEN_REFRESH,
            user=None,
            details={"success": False, "reason": "invalid"},
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="refresh_token 无效或已过期")
    if payload.get("type") != "refresh":
        await audit_event(
            request,
            AuditAction.USER_TOKEN_REFRESH,
            user=None,
            details={"success": False, "reason": "invalid"},
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="需要 refresh_token")

    user_id = payload.get("sub")
    user = session.get(User, user_id) if user_id else None
    if user is None or not user.is_active:
        await audit_event(
            request,
            AuditAction.USER_TOKEN_REFRESH,
            user=user,
            details={"success": False, "reason": "user_inactive"},
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已禁用")

    # 用最新 token_version 复核撤销状态。
    try:
        verify_refresh_token(body.refresh_token, user.token_version)
    except TokenRevokedError as e:
        await audit_event(
            request,
            AuditAction.USER_TOKEN_REFRESH,
            user=user,
            details={"success": False, "reason": "stale_version"},
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))

    await audit_event(
        request,
        AuditAction.USER_TOKEN_REFRESH,
        user=user,
        resource_type="user",
        resource_id=user.id,
        details={"success": True},
    )
    return _issue_token(user)


@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest, request: Request, session: Session = Depends(get_db)
) -> Token:
    """公开注册为 default 租户的 member，并签发该租户的令牌。"""
    try:
        user = register_member(
            session, username=body.username, password=body.password, email=body.email
        )
    except DefaultTenantInactiveError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="默认租户已停用")
    except UsernameTakenError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在")
    except EmailTakenError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="邮箱已存在")
    await audit_event(
        request,
        AuditAction.USER_CREATE,
        user=user,
        resource_type="user",
        resource_id=user.id,
        details={"username": user.username, "tenant_id": user.tenant_id, "role": user.role},
    )
    return _issue_token(user)


@router.get("/setup-status", response_model=SetupStatus)
def setup_status(session: Session = Depends(get_db)) -> SetupStatus:
    """没有系统管理员时，控制台应进入一次性向导。"""
    return SetupStatus(needs_setup=not system_admin_exists(session))


@router.post("/setup", response_model=Token, status_code=status.HTTP_201_CREATED)
async def setup(
    body: RegisterRequest, request: Request, session: Session = Depends(get_db)
) -> Token:
    """创建首个系统管理员。已有系统管理员时向导关闭。"""
    try:
        user = setup_system_admin(
            session, username=body.username, password=body.password, email=body.email
        )
    except SetupClosedError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="初始化向导已关闭")
    except DefaultTenantInactiveError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="默认租户已停用")
    except UsernameTakenError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在")
    except EmailTakenError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="邮箱已存在")
    await audit_event(
        request,
        AuditAction.USER_CREATE,
        user=user,
        resource_type="user",
        resource_id=user.id,
        details={"username": user.username, "tenant_id": user.tenant_id, "role": user.role},
    )
    return _issue_token(user)


@router.post("/users/{user_id}/revoke-tokens", response_model=RevokeTokensResult)
async def revoke_tokens(
    user_id: str,
    request: Request,
    session: Session = Depends(get_db),
    current_user: User = Depends(_require_system_admin),
) -> RevokeTokensResult:
    """递增指定用户的 token_version，使已发出的刷新令牌失效。"""
    try:
        user = revoke_refresh_tokens(session, user_id)
    except UserNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    await audit_event(
        request,
        AuditAction.USER_UPDATE,
        user=current_user,
        resource_type="user",
        resource_id=user.id,
        details={"action": "revoke_tokens", "token_version": user.token_version},
    )
    return RevokeTokensResult(user_id=user.id, token_version=user.token_version)


@router.get("/me", response_model=UserInfo)
def me(current_user: User = Depends(get_current_user)) -> UserInfo:
    """返回当前登录用户信息。"""
    return UserInfo.model_validate(current_user)
