# DeepSeek 实机验收

日期：2026-10-17。对应 `docs/plans/plan_llm001_two_vendor_live_20261017.md`。只验证第二家 DeepSeek，未重跑千问，未改业务代码、测试、依赖、永久配置或 `tasks.yaml`。

## 执行

技术 HEAD `d5311557c07273137104105f961cc20fe0e281b1`。指定解释器 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`。凭据只从进程环境 `Deepseek_Key` 读取，脚本屏蔽工作区 `.env`，报告不记录密钥、提示全文或回复全文。

```powershell
D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -u docs/evaluations/llm001_two_vendor_live_20261017.py deepseek --execute
```

退出码 0。新进程、独立合成 SQLite、合成用户与租户，经原 `ChatService.chat` 调用。端点 `https://api.deepseek.com`，请求模型 `deepseek-flash`。RAG 开启但空库加 Mock Embedding，只隔离检索，不证明检索质量。技能、安全与预算保持打开。

## 结果

机器记录：`docs/reviews/llm001_deepseek_live_20261017.json`。账本：`data/llm001-two-vendor-attempts-20261017.json`。

| 次序 | 流式 | HTTP | 返回模型 | usage |
|---|---|---|---|---|
| 1 | 否 | 200 | deepseek-flash | 输入 184 / 输出 27 / 合计 211 |
| 2 | 是 | 200 | 报告未解析到 | 报告未解析到 |

两次请求体分别 867 与 442 字节，`max_tokens` 均为 128。脚本按保守上界每次记 0.09984 元，两次合计低于 0.2 元。第二次是回复路径，流式分片没有留下模型和 usage；不以估算补写。

新 Session 复核：用户消息已保存；助手消息状态为 complete，模型标识为 `deepseek-flash`，正文与当次回复一致，长度为 8，含「你好」。摘要 `510340ada2dd43de1323c5e974878bdd5035745232ee63e6fd4bef643b43576d`。

## 边界

这只证明 DeepSeek 这一家经产品链被选中并完成一次合成对话。写本页时千问尚未用同一脚本重跑。随后同脚本对千问执行并通过，见 `docs/reviews/2026-10-17-llm-qwen-live.md`。账本禁止自动重跑已记录的一家。本页不证明生产、多实例或检索质量。
