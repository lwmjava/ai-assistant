"""调试与追踪模块。

提供 Agent 管线执行的全链路追踪：
- ``AgentTrace``：一次管线执行的完整追踪记录
- ``TraceEvent``：单次追踪事件
- ``TraceCollector``：全局 trace 收集器

对外暴露：
- ``AgentTrace`` / ``TraceEvent``：追踪数据类型
- ``TraceCollector``：全局单例收集器
"""

from app.debug.trace import AgentTrace, TraceCollector, TraceEvent

__all__ = [
    "AgentTrace",
    "TraceEvent",
    "TraceCollector",
]