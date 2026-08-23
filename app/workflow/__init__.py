"""Workflow 工作流引擎包。

提供：
- ``engine``：WorkflowEngine（cron 解析 / 任务执行 / 执行历史 / owner 失效处理）；
- ``scheduler``：WorkflowScheduler（asyncio 调度循环，在应用 lifespan 启动 / 关闭）；
- ``bridge``：WorkflowBridge（连接 ChatService，以 owner 身份执行 Prompt）。

cron 解析依赖可选包 ``croniter``：缺失时调度器不启动（手动触发仍可用），
并在调用 cron 计算时给出清晰错误，而非启动崩溃。
"""
