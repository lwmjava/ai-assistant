# PAGT-001 审查修复计划

2026-10-11；原计划 `plan_pagt_001_20261011.md` 不变，用户已授权本卡范围内修复与文档。

| 缺陷 | 证据与影响 | 正式回归与最小修复 | 复核 |
|---|---|---|---|
| F01 P2：新增工具拒绝参数仍被注册器回显 | 独立验证 `test_tool_registry_does_not_echo_rejected_sensitive_arguments` 实际失败：base.py 的参数展示复制完整输入，含非法 api_key 和绝对主机路径；工具安全错误不能消除入口泄漏 | 先保留失败测试，仅对 web_search/file_ops 使用固定脱敏参数摘要；意外异常只记错误类型并返回固定错误。不改旧四工具行为 | 验证 Agent 重跑真实注册器与 Agent 入口，独立审查复核 |
| F02：响应编码约束与请求默认协商冲突 | 根 Agent 读取 web_search：拒绝非 identity 压缩响应，但 httpx 默认请求允许 gzip 等，合法服务可能按请求压缩而被拒绝 | 请求明确 Accept-Encoding: identity；正式协议请求头测试，继续对异常压缩响应安全拒绝，保持体积上限 | 实现 Agent 修复、验证/审查复核 |
| F03 P2：深 JSON 越过分类错误边界 | 独立审查新增 deep_json 三反例实际3 failed：3001字符配置虽在大小限制内，却让 json.loads 抛 RecursionError，default_tools/health 异常；远端深JSON未返回约定分类错误 | 保留正式失败反例，在两个JSON解析点将 RecursionError 转 ValueError/invalid_response；不吞取消，不放宽体积或解析门禁 | 独立审查复跑三失败反例及完整入口集，再扩大回归 |

每次修复后重跑受影响专项与旧工具/Agent 兼容；最终源稳定后追加 Prompt 登记。未决 P1/P2 不得关闭，不能把安全修复推给下一卡。
