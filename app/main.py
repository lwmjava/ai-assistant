"""FastAPI 应用入口。

启动流程（lifespan）：
1. 初始化数据库表；
2. 引导初始管理员（若库中无用户且配置了环境变量）；
3. 校验生产环境安全配置（JWT 密钥等），不合规则拒绝启动；
4. 登记 Channel（API / 后续 CLI / 第三方）。
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session

from app.api.router import api_router
from app.channels.http_channel import HttpApiChannel
from app.channels.registry import registry
from app.core.config import settings
from app.core.database import engine, init_db
from app.core.security import validate_jwt_secret
from app.services.auth_service import ensure_initial_admin

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理。"""
    # 1. 数据库建表
    init_db()

    # 2. 初始管理员引导
    with Session(engine) as session:
        ensure_initial_admin(session)

    # 3. 生产环境安全校验
    if not validate_jwt_secret():
        raise RuntimeError(
            "生产环境安全配置校验未通过（JWT_SECRET_KEY 使用了默认值），应用已拒绝启动。"
        )

    # 4. 登记 Channel
    registry.register(HttpApiChannel(asgi_app=app))
    logger.info("ai-assistant 启动完成，已登记 %d 个 Channel。", len(registry.all()))

    # 4.5 启动工作流调度器（内部校验 WORKFLOW_ENABLED 与 croniter，不可运行时不启动）
    from app.workflow.scheduler import start_scheduler

    await start_scheduler()

    yield
    logger.info("ai-assistant 正在关闭。")

    # 关闭时停止调度器（幂等）
    from app.workflow.scheduler import stop_scheduler

    await stop_scheduler()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="企业级开源 AI 助手平台 — RAG + Agent 编排 + MCP 协议",
    lifespan=lifespan,
)

# CORS：生产环境应显式配置 CORS_ORIGINS，避免使用 *。
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载 API 路由（/api 前缀）。
app.include_router(api_router, prefix="/api")


@app.get("/", tags=["system"])
def root() -> dict:
    """根路径：返回服务基本信息。"""
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/docs",
        "health": "/api/health",
    }
