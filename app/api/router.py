"""API 路由聚合。"""

from fastapi import APIRouter

from app.api.routes import auth, chat, health, mcp, rag, workflow, audit

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(chat.router)
api_router.include_router(rag.router)
api_router.include_router(mcp.router)
api_router.include_router(workflow.router)
api_router.include_router(audit.router)
