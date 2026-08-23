"""HTTP API Channel。

真正的 HTTP 服务由 FastAPI 路由直接承载；
本 Channel 作为 Channel 抽象的「锚点」，登记 API_HTTP 类型，
并为后续 CLI / 第三方等接入方式提供统一的扩展范式。
"""

from app.channels.base import AbstractChannel, ChannelRequest, ChannelResponse, ChannelType


class HttpApiChannel(AbstractChannel):
    """HTTP API 接入 Channel。

    实际请求处理由 FastAPI 路由完成；此处的 dispatch 仅作为
    抽象层占位，便于在统一 Channel 体系内编排（如统一埋点、限流）。
    """

    def __init__(self, asgi_app=None) -> None:
        super().__init__(ChannelType.API_HTTP)
        self.asgi_app = asgi_app

    async def dispatch(self, request: ChannelRequest) -> ChannelResponse:
        """占位实现：声明该请求由 FastAPI 直接处理。"""
        return ChannelResponse(
            status_code=200,
            body={"channel": self.channel_type.value, "handled_by": "fastapi"},
        )
