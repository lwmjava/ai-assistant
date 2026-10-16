# 千问有界真实验收方案

2026-10-16 用户答复：“用千问的吧，可以用我配置在.env 环境的密钥”。此授权用于千问真实验收，不延伸到其他供应商、不修改 .env；CLI init 与普通 pytest 继续禁止继承工作区配置。

已只读核对非敏感配置：现有 DashScope 兼容端点为 dashscope.aliyuncs.com/compatible-mode/v1，EMBEDDING_API_KEY 已配置，主对话配置则属于另一供应商。仅取 DashScope 密钥，不输出其值，也不发送给其他域名。

使用阿里云官方 [qwen-plus 模型说明](https://help.aliyun.com/zh/model-studio/qwen-plus) 与 [兼容接口说明](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)，核对文本输入、窗口 1000000、最大输出 32768。仅在验收子进程声明这一部署/模型的文本能力，不修改生产配置、不宣称视觉支持。

tests/manual_qwen_probe.py 默认只显示方案，显式 --execute 才读取授权密钥并调用。实际走现有 ChatService.chat、AgentPipeline、安全、能力与预算边界，使用新建临时 SQLite、合成用户/租户与一句公开验收文本；RAG 保持启用但使用明确 Mock Embedding，故不证明真实检索质量。单次进程最多 8 个真实 HTTP 请求，只允许 HTTPS 指定 DashScope /chat/completions，单请求输出不超过 256 tokens，序列化请求不超过 32768 字节，超时 30 秒，无自动重跑或供应商兜底。只输出受控状态、模型/Token 数、请求数量和数据库成功标记；不打印原始异常、请求头或响应正文。

成功须得到非空正常回复，且从新 Session 读到该合成会话的完整助手消息。该结果只能证明千问这一家；原卡两家提供商切换仍不能据此关闭。若凭据无文本权限或服务拒绝，保留实际失败，不借用另一家密钥或放宽护栏。真实账单以供应商记录为准，不把请求上限当免费承诺。

本次只验无工具合成对话，局部 Service 注入空工具注册表，防止模型意外选择文件/网页工具读取工作区；不声称验证工具执行能力。额外 DNS/socket 边界只放行 DashScope 的 HTTPS 连接，普通完整回归仍保留原工具、安全与 RAG 配置，不借此注入替代其失败结论。
