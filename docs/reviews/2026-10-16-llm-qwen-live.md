# 千问真实合成对话验收

执行者：主 Agent `/root`；用户于 2026-10-16 明确指定千问并允许使用 `.env` 中已有密钥。方案 plan_llm_live_probe_20261016.md；没有修改 `.env`、输出密钥、调用另一供应商或发送业务文件。普通 pytest 与 CLI init 的隔离边界保留。

## 最终实际结果

Windows，指定 conda Python 3.12.0，PYTHONUTF8=1/PYTHONIOENCODING=utf-8：

```powershell
D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -u -m tests.manual_qwen_probe --execute
```

最终执行退出 0，日志宿主临时目录 platform-qwen-live-20261016-r3.log。经真实 ChatService.chat 调用现有 AgentPipeline；临时 SQLite、合成主体和公开测试问题，随后新 Session 确认完整助手回复保存，模型标识 qwen-plus，回复包含“验收成功”。只注入无工具注册表和 Mock Embedding 作为本次无工具场景隔离；不关闭安全或模型预算，不证明工具/检索质量。

| 请求 | 真实 HTTP | 输入 tokens | 输出 tokens | 总 tokens |
|---|---|---|---|---|
| 1 | 200 | 173 | 1 | 174 |
| 2 | 200 | 166 | 222 | 388 |
| 3 | 200 | 185 | 1 | 186 |
| 4 | 200 | 260 | 3 | 263 |
| 5 | 200 | 154 | 3 | 157 |
| 合计 | 五次成功 | 938 | 230 | 1168 |

上述 Token 为供应商实际 usage 字段，不是客户端估计；费用以供应商账单为准。最终验收脚本要求所有请求确有 HTTP 200、回复含合成验收词且完整持久化，不能仅凭 HTTP 200、落库或固定失败文案判成功。上限八次，每次输出 256 tokens；没有自动重试。

官方 [模型说明](https://help.aliyun.com/zh/model-studio/qwen-plus) 支持本次部署/模型的文本窗口声明；[兼容接口说明](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope) 支持现有 DashScope 地址。只在验收子进程按部署+模型登记，未永久更改默认模型/能力表。没有据文本能力推断视觉支持。

## 设施纠正与证据限制

初次执行因本地事件循环 socket 被验收脚本自己的边界错误拒绝，零模型发送记录，退出 1；已允许 loopback 事件循环。第二次输出 passed，但缺少真实 HTTP 状态，属于验收脚本成功条件不足，不能视为模型成功；该轮两次发送尝试未获得响应状态，不能据此证明费用或外发总量。随后补齐严格成功条件和逐请求受控传输错误记录，并兼容 DNS 的 bytes 主机名，才取得上表最终实际成功结果。没有把设施错误归为业务断言失败，也没有把早期错误标记用作验收。

源码测试输入由 platform_batch_final_inputs_20261016.json 中 tests/manual_qwen_probe.py 与相关 LLM/Agent/Service 指纹绑定。最终全量回归结果另记。本页仅证明千问一家；用户没有指定并授权第二家真实供应商，原卡至少两家切换仍未齐，不关闭 LLM-001，也不证明生产、多实例或真实视觉质量。
