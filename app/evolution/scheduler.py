"""Evolution 调度器 — 基于 asyncio 的 cron 定时蒸馏任务调度。

在应用 lifespan 中启动/停止。循环周期性检查是否到蒸馏时间，
到点则触发 Distiller 对近期对话进行批量分析。

当前实现：简化的时间窗口检查（每 N 秒检查一次）。
可按需扩展：cron 表达式解析、分布式锁、DB 持久化调度记录。
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.evolution.distiller import Distiller

logger = logging.getLogger(__name__)

# 进程级调度器实例
_scheduler_task: asyncio.Task | None = None
# 上次蒸馏执行时间
_last_distill_time: datetime | None = None


def _is_scheduler_runnable() -> bool:
    """检查调度器是否可运行。"""
    if not settings.EVOLUTION_DISTILL_ENABLED:
        return False
    return True


async def _loop() -> None:
    """调度器主循环：周期性检查并触发蒸馏。"""
    interval = settings.EVOLUTION_DISTILL_INTERVAL_SECONDS
    logger.info(
        "Evolution 蒸馏调度器已启动，扫描间隔 %ds，分析窗口 %dh，批次上限 %d 个会话",
        interval,
        settings.EVOLUTION_DISTILL_HOURS,
        settings.EVOLUTION_DISTILL_MAX_CONVERSATIONS,
    )

    while True:
        try:
            await _tick()
        except Exception:  # noqa: BLE001
            logger.exception("Evolution 蒸馏调度器 tick 异常")
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            break


async def _tick() -> None:
    """检查是否应该触发蒸馏，是则执行。"""
    global _last_distill_time

    now = datetime.now(timezone.utc)

    # 检查是否在允许的时间窗口内
    if not _is_in_time_window(now):
        return

    # 检查是否已超过最小间隔
    if _last_distill_time is not None:
        min_interval = timedelta(hours=settings.EVOLUTION_DISTILL_MIN_INTERVAL_HOURS)
        if now - _last_distill_time < min_interval:
            return

    # 执行蒸馏
    logger.info("触发夜间蒸馏（时间窗口内）...")
    _last_distill_time = now

    try:
        distiller = Distiller()
        result = await distiller.distill_recent(
            hours=settings.EVOLUTION_DISTILL_HOURS,
            max_conversations=settings.EVOLUTION_DISTILL_MAX_CONVERSATIONS,
        )

        if result.error:
            logger.warning("蒸馏执行异常: %s", result.error)
        elif result.total_issues > 0:
            logger.info(
                "蒸馏完成：对话=%d, 消息=%d, 洞察=%d (critical=%d, high=%d, medium=%d, low=%d), 技能建议=%d",
                result.conversations_analyzed,
                result.messages_analyzed,
                result.total_issues,
                result.critical_count,
                result.high_count,
                result.medium_count,
                result.low_count,
                len(result.skill_suggestions),
            )
            # 记录每条洞察
            for insight in result.insights:
                logger.info(
                    "  [%s][%s] %s (频次: %d)",
                    insight.severity.value,
                    insight.category.value,
                    insight.summary,
                    insight.frequency,
                )
        else:
            logger.info(
                "蒸馏完成：对话=%d, 消息=%d, 未发现显著改进点",
                result.conversations_analyzed,
                result.messages_analyzed,
            )

    except Exception:  # noqa: BLE001
        logger.exception("蒸馏执行失败")


def _is_in_time_window(now: datetime) -> bool:
    """检查当前时间是否在蒸馏时间窗口内。

    使用简化的时间窗口检查：判断当前小时是否在允许范围内。
    默认窗口为 02:00-05:00 UTC（对应北京时间 10:00-13:00）。
    """
    window_start = settings.EVOLUTION_DISTILL_WINDOW_START_HOUR
    window_end = settings.EVOLUTION_DISTILL_WINDOW_END_HOUR

    if window_start <= window_end:
        return window_start <= now.hour < window_end
    else:
        # 跨天窗口（如 22:00-02:00）
        return now.hour >= window_start or now.hour < window_end


async def start_scheduler() -> None:
    """启动 Evolution 蒸馏调度器（幂等）。"""
    global _scheduler_task

    if _scheduler_task is not None and not _scheduler_task.done():
        return
    if not _is_scheduler_runnable():
        return

    _scheduler_task = asyncio.create_task(_loop())
    logger.info("Evolution 蒸馏调度器任务已创建")


async def stop_scheduler() -> None:
    """停止 Evolution 蒸馏调度器（幂等）。"""
    global _scheduler_task

    if _scheduler_task is not None:
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        _scheduler_task = None
    logger.info("Evolution 蒸馏调度器已停止")


# 兼容别名
EvolutionScheduler = type(
    "EvolutionScheduler",
    (),
    {
        "start": staticmethod(start_scheduler),
        "stop": staticmethod(stop_scheduler),
        "__doc__": "Evolution 蒸馏调度器（兼容类包装）。",
    },
)