"""Bounded checkpoint retention maintenance in the application lifecycle."""

import asyncio
import logging
from contextlib import suppress

from sqlmodel import Session

from app.core.config import settings
from app.core.database import engine
from app.services.agent_state_service import cleanup_expired_checkpoints

logger = logging.getLogger(__name__)
_task: asyncio.Task | None = None
_INTERVAL_SECONDS = 60
_BATCH_SIZE = 100


def run_cleanup_batch() -> int:
    if not settings.AGENT_STATE_PERSIST_ENABLED:
        return 0
    try:
        with Session(engine) as session:
            return cleanup_expired_checkpoints(session, limit=_BATCH_SIZE)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Agent checkpoint cleanup unavailable error_type=%s", type(exc).__name__)
        return 0


async def _loop() -> None:
    while True:
        await asyncio.sleep(_INTERVAL_SECONDS)
        worker = asyncio.create_task(asyncio.to_thread(run_cleanup_batch))
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            await worker
            raise


async def start_cleanup() -> None:
    global _task
    if not settings.AGENT_STATE_PERSIST_ENABLED or (_task is not None and not _task.done()):
        return
    await asyncio.to_thread(run_cleanup_batch)
    _task = asyncio.create_task(_loop(), name="agent-checkpoint-cleanup")


async def stop_cleanup() -> None:
    global _task
    task, _task = _task, None
    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
