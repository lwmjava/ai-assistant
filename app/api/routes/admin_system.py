"""系统管理员看到的运行状态。失败的探测仍返回 200，由页面标出哪一项不通。"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_user
from app.api.routes.health import STARTED_AT, _check, _configured_vector_backend, _database_status, _vector_store_status
from app.core.config import settings
from app.core.security import Role
from app.models.user import User
from app.schemas.admin import ProbeStatus, SystemChecks, SystemStatusOut, VectorProbeStatus

router = APIRouter(prefix="/admin/system", tags=["admin-system"])


def _require_system_admin(user: User = Depends(get_current_user)) -> User:
    """系统状态只对系统管理员开放。"""
    if user.role_enum is not Role.SYSTEM_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="仅系统管理员可查看系统状态",
        )
    return user


@router.get("/status", response_model=SystemStatusOut)
def system_status(_: User = Depends(_require_system_admin)) -> SystemStatusOut:
    """返回数据库、向量库、版本和本进程启动时间。"""
    database = _check(_database_status)
    try:
        vector_status, backend = _vector_store_status()
    except Exception:
        vector_status = "error"
        backend = _configured_vector_backend()
    overall = "ok" if database == "ok" and vector_status == "ok" else "error"
    return SystemStatusOut(
        status=overall,
        app=settings.APP_NAME,
        version=settings.APP_VERSION,
        env=settings.ENV,
        started_at=STARTED_AT,
        checks=SystemChecks(
            database=ProbeStatus(status=database),
            vector_store=VectorProbeStatus(status=vector_status, backend=backend),
        ),
    )
