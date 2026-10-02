# 实现说明：限流提示与倒计时

> 日期：2026-10-02
> 计划：`docs/plans/plan_d3.md` 的 LIMIT-001 节
> 结果：打开限流后，打满当前桶的下一次对话会带上等待秒数。对话页在秒数大于 0 时倒计时，并在结束前禁用发送。默认开关仍关闭。

## 完成的行为

补充速率大于 0 且本次请求被拒绝时，等待秒数是 `ceil((本次消耗 - 当前 token) / 补充速率)`，至少 1 秒，写入 `SecurityContext.retry_after_seconds`。速率小于或等于 0 时拒绝，该字段留空，不做除法。桶的补充公式在速率大于 0 时没有改。

非流式对话返回 429，正文是 `{"code":"rate_limited","retry_after_seconds":整数或 null}`。只有秒数不为空时才带 `Retry-After`。流式对话先发 `rate_limit` 事件，数据为 `{"retry_after_seconds": N 或 null}`，再发原有错误文案「请求过于频繁，请稍后再试」，然后返回，不写新的用户消息。未超限的请求不发 `rate_limit` 事件。

对话页收到大于 0 的秒数时，在输入框上方显示剩余秒数，发送按钮不可用，到 0 后提示消失、按钮恢复。秒数缺失或为 null 时只显示拒绝说明，不出现会自己走完的倒计时。配额超限仍用原来的配额说明，不进倒计时。

默认 `SECURITY_RATE_LIMIT=false`，默认速率和容量未改。README 写明计数只在本进程、重启即清空，以及「完成 `OPS-001` 之前不要水平扩展」。

## 代码位置

- `app/security/rate_limiter.py`：拒绝时计算等待秒数
- `app/security/types.py`：`retry_after_seconds`
- `app/services/chat_service.py`：流式先发 `rate_limit`
- `app/api/routes/chat.py`：非流式 429 正文和 `Retry-After`
- `frontend/src/pages/Chat.tsx`、`frontend/src/components/chat/Composer.tsx`、`frontend/src/lib/rate-limit.ts`
- `README.md`

## 验证

```text
D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -m pytest tests/test_rate_limit_retry.py -v --tb=line --basetemp data/pytest-tmp/limit-001b
```

7 passed，退出码 0。其中容量 1、每秒补充 1 个时，第二次非流式对话得到 `retry_after_seconds` 为 1，并带 `Retry-After: 1`，用户消息数不增加。速率为 0 时第二次为 429，`retry_after_seconds` 为 null，响应没有 `Retry-After`，进程没有因除零退出。未超限的流式响应没有 `rate_limit` 事件。超限的流式响应先有 `rate_limit` 再有错误文案，且没有新的用户消息。

`ruff check` 覆盖 `app/security/rate_limiter.py`、`app/security/types.py`、`app/api/routes/chat.py`、`tests/test_rate_limit_retry.py`，All checks passed。`app/services/chat_service.py` 里原有的引号注解 F821 未改。

`cd frontend; npm run typecheck` 退出码 0。

浏览器：临时库，限流打开、容量 1、补充速率 0.01。第一条发送没有倒计时。紧接着的第二条出现「发送过于频繁，请在 100 秒后再试。倒计时结束前不能发送。」，发送按钮为禁用。数字随时间下降，到 0 后提示消失，发送按钮恢复可用。这条验证用的是临时进程和 `data/pytest-tmp/limit-ui.db`，验证后已停掉，没有改 `data/ai_assistant.db`。

## 没做的事

没有换成滑动窗口或 Redis。没有按 IP 再加一套限流。知识库页没有倒计时。没有把多实例限流写成已经生效。默认开关没有打开。临时库上的流式回复在落库后 `session.refresh` 报过「Instance is not persistent」，页面显示「生成失败，请稍后重试」；限流发生在这步之前，倒计时不依赖这次回复成功。没有把这个刷新失败收进本任务。
