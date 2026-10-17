# 千问实机验收

日期：2026-10-17。对应 `docs/plans/plan_llm001_two_vendor_live_20261017.md`。在同一有界脚本上补跑千问，使它与当天的 DeepSeek 记录使用当前工作区代码。未改业务代码、测试、依赖、永久配置。

## 执行

技术 HEAD `d5311557c07273137104105f961cc20fe0e281b1`。`556a9dd` 至该 HEAD 的 `app/llm`、对话服务、Agent 与 `app/core/config.py` 无差异。指定解释器 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`。凭据只从进程环境 `DASHSCOPE_API_KEY` 读取，脚本屏蔽工作区 `.env`。

```powershell
D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -u docs/evaluations/llm001_two_vendor_live_20261017.py qwen --execute
```

退出码 0。新进程、独立合成 SQLite、合成用户与租户，经原 `ChatService.chat` 调用。端点 `https://dashscope.aliyuncs.com/compatible-mode/v1`，请求模型 `qwen-plus`。RAG 开启但空库加 Mock Embedding，只隔离检索，不证明检索质量。

## 结果

机器记录：`docs/reviews/llm001_qwen_live_20261017.json`。

| 次序 | 流式 | HTTP | 返回模型 | usage |
|---|---|---|---|---|
| 1 | 否 | 200 | qwen-plus | 输入 162 / 输出 1 / 合计 163 |
| 2 | 是 | 200 | qwen-plus | 报告未解析到 |

两次请求体分别 862 与 437 字节，`max_tokens` 均为 128。脚本按保守上界每次记 0.09984 元。流式回复没有 usage，不以估算补写。

新 Session 复核：用户消息已保存；助手消息状态为 complete，模型标识为 `qwen-plus`，正文与当次回复一致，长度为 8，含「你好」。摘要 `510340ada2dd43de1323c5e974878bdd5035745232ee63e6fd4bef643b43576d`。

## 边界

`docs/reviews/2026-10-16-llm-qwen-live.md` 是更早的五次请求，不代替本页。本页只证明千问这一家。DeepSeek 见 `docs/reviews/2026-10-17-llm-deepseek-live.md`。不证明生产、多实例或检索质量。
