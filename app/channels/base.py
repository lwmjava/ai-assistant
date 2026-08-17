"""Channel 抽象基类与请求/响应数据模型。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ChannelType(str, Enum):
    """Channel 类型。"""

    API_HTTP = "api_http"  # HTTP API 接入（OpenAPI）
    WEBUI = "webui"  # 内置 Web 聊天界面
    CLI = "cli"  # 命令行工具
    THIRD_PARTY = "third_party"  # 第三方系统集成


@dataclass
class ChannelRequest:
    """统一的 Channel 入站请求。

    各 Channel 将自身协议（HTTP/CLI/...）转换为该结构后交给业务核心处理。
    """

    method: str = "POST"
    path: str = "/"
    headers: dict[str, str] = field(default_factory=dict)
    query: dict[str, str] = field(default_factory=dict)
    body: Any = None
    # 已鉴权的用户标识（由 Channel 在接入层完成认证后填充）。
    user_id: str | None = None
    tenant_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChannelResponse:
    """统一的 Channel 出站响应。"""

    status_code: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    body: Any = None


class AbstractChannel(ABC):
    """Channel 抽象基类。

    子类实现 ``dispatch``，将 ``ChannelRequest`` 转换为内部处理并产出
    ``ChannelResponse``。具体接入（如 FastAPI 路由、CLI 解析）由子类负责。
    """

    channel_type: ChannelType

    def __init__(self, channel_type: ChannelType) -> None:
        self.channel_type = channel_type

    @abstractmethod
    async def dispatch(self, request: ChannelRequest) -> ChannelResponse:
        """处理请求并返回响应。"""
        raise NotImplementedError
