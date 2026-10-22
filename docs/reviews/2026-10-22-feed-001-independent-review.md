# FEED-001 只读独立审查

日期：2026-10-22。审查对象是会话失败反馈与点赞点踩（任务卡写作 FEED-001）。审查本身没有改业务代码；文末修复是随后按该结论做的。任务没有标成完成。

## 结论

审查当时不能关闭。发现 1 个 P2：生产循环自己抛错时，仍在进行的尝试会被写成 `stopped`，之后不能按 `api_error` 提交失败反馈。没有发现越权、跨租户写入或评论原文回显这一类 P1。该 P2 已在同日修复，见文末。Linux 和同一提交的 Ubuntu CI 仍未验证，任务保持审查中。

基线是分支 `codex/eval-001-report-query` 的 `00ea6ee691f0df1c178de5c05729fd810e81bf2c`，反馈实现仍在未提交改动里。

## P2：消费端异常被记成显式取消

`app/services/active_runs.py` 的 `_produce` 在 `finally` 里对反馈流调用 `aclose()`。`app/services/chat_feedback.py` 的 `track_stream` 把 `GeneratorExit` 和 `CancelledError` 都写成 `stopped`，再在 `finally` 里 `finish_session`。

流已经自己结束时，这个收尾是空操作，成功、超时和流内错误事件仍按原结果落库。生产循环在流还停在 `yield` 时自己抛错，`aclose()` 会把这次尝试改成 `stopped`。`submit_failure` 要求尝试结果与反馈类型一致，所以这次服务端失败不能再提交 `api_error`。界面也只在结果为 `api_error` 或 `no_response` 时给出失败反馈入口。

内存库探针（不写仓库数据库，退出码 0）：

- 只让 `async for` 的循环体抛 `RuntimeError`：生成器仍挂起，`finish_session` 未被调用，结果保持 `pending`。
- 同样抛错后再 `aclose()`：`finish_session` 收到 `stopped`，库中结果为 `stopped`。

这与 `_produce` 的收尾一致。循环体内的授权失败、单事件过大或字段解析错误都会走这条路径。显式 `task.cancel()` 写成 `stopped` 符合已批准约定。订阅循环在 `subscribe`，断开订阅不会进入 `track_stream`。名为 `test_stream_timeout_is_failure_and_disconnect_does_not_own_it` 的测试只断言超时后的 `no_response`，没有覆盖消费端异常后的 `aclose()`。

## 已核对且与锁定约定一致

- 提交人必须是记录和会话的主人。系统管理员不能替别人提交。别人访问返回“记录不存在”。
- 只有 `api_error` 和 `no_response` 能提交失败反馈。重复的失败反馈返回原行，不改评论。
- 评价只针对本人已成功完成的助手消息。同一条消息再次评价会更新。向下评价可以不带原因。
- 评论先过输入过滤再脱敏，长度上限 500。校验失败响应是固定的“反馈参数无效”，测试要求不回显评论原文。
- 列表和详情返回固定元数据与脱敏评论，契约里没有消息正文。租户管理员可看本租户其他人的反馈；系统管理员跨租户列表必须带 `tenant_id`。删除限于本人或系统管理员。
- 迁移在三张表任一有数据时拒绝降级。
- 助手消息在写入尝试结果前已经 `commit`。`finish` 开头的 `rollback` 不会撤掉已提交的消息。

## 既有日志

这些日志是本次审查前的工作区产物，本次没有重跑，也没有把它们和当前每个文件的哈希重新绑定：

- `data/validation/feed001-20261022/pytest-full-v4.log`：3329 passed，3 skipped，5 warnings，退出码 0，862 秒。
- 同目录 `ruff-final.exit` 与 `mypy-final.exit` 均为 0。
- 外部审查日志 `codex-review.log` 退出码 1，结束原因是用量上限，没有形成结论。

## 未验证

- Linux 上的进程、迁移和同一最终提交的 Ubuntu CI。
- 浏览器里的反馈入口。
- 上述全量日志与当前未提交文件逐字节一致。

## 修复

`track_stream` 只在 `CancelledError` 时把结果写成 `stopped`。`aclose()` 注入的 `GeneratorExit` 保留进入收尾时已经确定的结果：尚未结束的服务端失败保持默认的 `api_error`，已经写出的成功或超时结果不被改写。

回归用例 `test_consumer_failure_close_stays_api_error` 先失败，断言为 `stopped`；修复后通过，并确认可以提交 `api_error`。同次还跑了反馈三份测试和 `tests/test_sse_active_runs_review.py`：43 passed，1 warning，退出码 0。命令使用 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -m pytest`，`DATABASE_URL=sqlite:///./data/feed001-p2-fix.db`，`--basetemp data/t/feed001_p2`。这不是全量 pytest，也不能代替 Linux 与同一提交的 Ubuntu CI。
