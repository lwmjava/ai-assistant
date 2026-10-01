"""RAG 导入任务调度器：周期性扫描并执行待处理导入任务。"""

from __future__ import annotations

import asyncio
import logging

from app.core.config import settings
from app.rag.import_jobs import run_import_jobs_once

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None


def _is_scheduler_runnable() -> bool:
    """根据配置决定是否启动导入调度器。"""
    return settings.RAG_IMPORT_ENABLED


async def _loop() -> None:
    logger.info("RAG 导入调度器已启动，扫描间隔 %.1fs", settings.RAG_IMPORT_INTERVAL_SECONDS)
    while True:
        try:
            await run_import_jobs_once()
            from datetime import UTC, datetime

            from sqlmodel import Session

            from app.core.database import engine
            from app.rag.retention import purge_expired_documents

            with Session(engine) as session:
                await purge_expired_documents(session, now=datetime.now(UTC))
        except Exception:  # noqa: BLE001
            logger.exception("RAG 导入调度器 tick 异常")
        try:
            await asyncio.sleep(settings.RAG_IMPORT_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            break


async def start_scheduler() -> None:
    """启动导入调度器后台任务（幂等）。"""
    global _task
    if _task is not None and not _task.done():
        return
    if not _is_scheduler_runnable():
        return
    _task = asyncio.create_task(_loop())
    logger.info("RAG 导入调度器任务已创建")


async def stop_scheduler() -> None:
    """停止导入调度器后台任务（幂等）。"""
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        _task = None
    logger.info("RAG 导入调度器已停止")
