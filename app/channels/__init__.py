"""Channel 抽象层。

Channel 是系统对外的统一接入抽象。不同的客户端形态
（HTTP API / WebUI / CLI / 第三方集成）都通过各自的 Channel 接入，
由 ChannelRegistry 统一登记与路由，使业务核心与接入方式解耦。
"""
