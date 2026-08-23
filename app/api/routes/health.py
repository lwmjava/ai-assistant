"""健康检查路由。"""

from fastapi import APIRouter

from app.core.config import settings

router = APIRouter(tags=["system"])


@router.get("/health")
def health_check() -> dict:
    """服务健康检查。

    返回服务名、版本与运行环境，供负载均衡 / 探针使用。
    """
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "env": settings.ENV,
    }
