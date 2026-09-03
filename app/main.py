"""FastAPI 应用入口。

启动流程（lifespan）：
1. 初始化数据库表；
2. 引导初始管理员（若库中无用户且配置了环境变量）；
3. 校验生产环境安全配置（JWT 密钥等），不合规则拒绝启动；
4. 登记 Channel（API / 后续 CLI / 第三方）。
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
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

    # 4.6 启动 Evolution 蒸馏调度器（内部校验 EVOLUTION_DISTILL_ENABLED）
    from app.evolution.scheduler import start_scheduler as start_evolution

    await start_evolution()

    yield
    logger.info("ai-assistant 正在关闭。")

    # 关闭时停止调度器（幂等）
    from app.workflow.scheduler import stop_scheduler

    await stop_scheduler()

    from app.evolution.scheduler import stop_scheduler as stop_evolution

    await stop_evolution()


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


# ── 前端构建产物托管 ────────────────────────────────────
#
# 开启 `SERVE_FRONTEND` 且 `frontend/dist` 存在时，本进程同时提供 API 与界面，
# 部署只需一个 Python 进程。托管与否由配置显式决定，不随磁盘上是否有构建产物
# 而改变——否则 `GET /` 的契约会随本地构建状态漂移。

FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
_SERVE_FRONTEND = settings.SERVE_FRONTEND and FRONTEND_DIST.is_dir()

if settings.SERVE_FRONTEND and not FRONTEND_DIST.is_dir():
    logger.warning(
        "SERVE_FRONTEND 已开启，但未找到构建产物 %s；"
        "请先在 frontend/ 下执行 npm run build。本次启动不托管前端。",
        FRONTEND_DIST,
    )


@app.get("/", tags=["system"], include_in_schema=not _SERVE_FRONTEND)
def root():
    """根路径：托管前端时返回应用入口，否则返回服务基本信息。"""
    if _SERVE_FRONTEND:
        # index.html 不缓存，保证前端发版后客户端能立即取到新入口
        return FileResponse(
            FRONTEND_DIST / "index.html",
            headers={"Cache-Control": "no-cache"},
        )
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/docs",
        "health": "/api/health",
    }


if _SERVE_FRONTEND:
    # 带内容哈希的产物可长期强缓存
    app.mount(
        "/assets",
        StaticFiles(directory=FRONTEND_DIST / "assets"),
        name="frontend-assets",
    )
    logger.info("已托管前端构建产物：%s", FRONTEND_DIST)

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str) -> FileResponse:
        """前端路由回退：非 API 路径交给 index.html 由前端路由接管。

        `/api/*` 未命中的请求不参与回退，仍由 FastAPI 返回 JSON 404——
        否则 API 消费者会收到 HTML 而误判为接口异常。
        """
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="接口不存在")

        # 目录穿越防护：解析后必须仍位于产物目录内
        target = (FRONTEND_DIST / full_path).resolve()
        if target.is_file() and target.is_relative_to(FRONTEND_DIST.resolve()):
            return FileResponse(target)

        # index.html 不缓存，保证前端发版后客户端能立即取到新入口
        return FileResponse(
            FRONTEND_DIST / "index.html",
            headers={"Cache-Control": "no-cache"},
        )
