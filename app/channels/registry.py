"""Channel 注册表（单例）。

集中登记所有已启用的 Channel，供运行时按类型检索与路由。
"""

import logging
from typing import Callable

from app.channels.base import AbstractChannel, ChannelType

logger = logging.getLogger(__name__)


class ChannelRegistry:
    """Channel 注册表。"""

    def __init__(self) -> None:
        self._channels: dict[ChannelType, AbstractChannel] = {}

    def register(self, channel: AbstractChannel) -> None:
        """登记一个 Channel；同类型重复登记会覆盖并告警。"""
        if channel.channel_type in self._channels:
            logger.warning("Channel 类型 %s 已被覆盖登记", channel.channel_type.value)
        self._channels[channel.channel_type] = channel
        logger.info("已登记 Channel: %s", channel.channel_type.value)

    def get(self, channel_type: ChannelType) -> AbstractChannel | None:
        """按类型获取 Channel。"""
        return self._channels.get(channel_type)

    def all(self) -> list[AbstractChannel]:
        """返回所有已登记的 Channel。"""
        return list(self._channels.values())

    def register_factory(self, channel_type: ChannelType, factory: Callable[[], AbstractChannel]) -> None:
        """通过工厂函数惰性登记（适用于需要运行时依赖的 Channel）。"""
        self.register(factory())


# 全局单例注册表。
registry = ChannelRegistry()
