# PAGT-001 工具 Evaluation v1

版本：pagt001-tools-v1，2026-10-11。数据分类：AI 编写 Smoke、Adversarial 与 Observed Regression，非人工 Gold。无真实收费调用、真实模型质量或性能指标。

真实应用、ChatService、AgentPipeline、工具注册器和适配器与隔离 HTTP 响应、实际临时共享文件组成 E2；直接 helper 断言是 E3，只作为补充。固定本地脚本模型是测试驱动，不是生产生成模型。搜索来源使用 `example.org` 合成公共 URL，不把这些数据宣称为实际搜索结果。

| Case | 实际链路/输入 | 可观察期望 | 正式测试 |
|---|---|---|---|
| S01 四国内 provider | default_tools → AgentPipeline → 合成 HTTP | 固定国内端点，单次请求，搜索观测保留来源 | test_pagt001_integration.py 四 provider Agent 场景 |
| S02 对话持久化 | /api/chat → ChatService → 工具 → 回复 → 新请求读取消息 | 回答来源可回读，未自动注入历史/知识库到搜索请求 | test_pagt001_integration.py 四 provider API 场景 |
| S03 无密钥/默认关闭 | 实际 TestClient 启停 → /api/health /注册 | 服务启动，状态 unavailable，仅旧四工具 | integration 健康与旧工具场景 |
| S04 文件成功 | 实际共享临时文件 → AgentPipeline | 有界 UTF-8 文本可读，原数据不变 | integration 与 tools 文件场景 |
| A01 数值 | bool/NaN/Infinity/极端整数/非法字节数 | 不注册或安全拒绝，绝不无限读/请求 | tools 数值场景、integration NaN |
| A02 远端失败 | timeout/429/5xx/非法JSON/超大响应/取消 | 固定脱敏失败，取消传播，无重试/国外兜底 | integration 与 tools 网络场景 |
| A03 权限/范围 | 绝对/跨根/../.env/未知tenant/写动作 | 拒绝且实际文件未变化 | integration 与 tools 文件边界 |
| A04 对象绑定 | 非国内模型、来源伪协议、junction/symlink/hardlink、打开时替换 | 安全拒绝，句柄指向的对象必须在授权根且身份匹配 | tools 与独立入口审查 |
| A05 回滚 | 执行中关闭、失败后旧工具、重复读取 | 新工具拒绝，旧四工具可用，无文件写副作用 | tools 与 integration 关闭/兼容 |
| R01 拒绝参数回显 | 注册器接收 api_key/绝对 path | Agent 观测不回显秘密/主机路径 | integration 敏感参数正式失败后修复 |
| R02 异常回显 | 新工具 unexpected exception 含合成秘密/路径 | 日志和观测只含安全错误类型/提示 | integration 意外异常 |
| R03 编码协商 | 请求头与异常压缩响应 | 明确 identity，异常压缩被拒且上限仍有效 | tools 协议头与压缩边界 |

命令、最终结果、源指纹、独立审查与人工业务验收状态在 `docs/plans/implementation_pagt_001.md` 和 `docs/reviews/2026-10-11-PAGT-001-*.md` 记录。不能用 E3 数量替代 S01–S04 的 E2 证据。
