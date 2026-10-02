# 实现说明：流式对话结束后不再刷新已脱离的会话

> 日期：2026-10-02
> 计划：`docs/plans/plan_stream_session_detach.md`
> 结果：流式对话写完回复后，事件流继续发出结束和来源，不再因为刷新已脱离的会话而变成「生成失败，请稍后重试」。

## 问题记录

2026-10-02，本机 `uvicorn app.main:app --reload` 上发送流式对话。`POST /api/chat/stream` 已返回 200，随后日志为「流式对话失败」：

```text
sqlalchemy.exc.InvalidRequestError: Instance '<Conversation>' is not persistent within this Session
```

位置是 `app/services/chat_service.py` 里回复提交之后的 `session.refresh(conv)`。路由接住异常，页面对这条流显示「生成失败，请稍后重试」。同一条日志里更早的 429 是限流拒绝，那条路径不会执行这次刷新。

`session.refresh(conv)` 写于 2026-09-27。当时整段生成都在同一次请求会话里，成功路径可以刷新。2026-10-01 配额提交 `5796678` 让路由先取走第一条事件（会话编号），再返回 SSE，以便超限时直接给 JSON 429。FastAPI 0.115 在端点函数返回后就关闭 `yield` 依赖。`get_session` 的 `Session.close()` 会摘掉已加载对象，但这个 Session 还能继续写入。助手消息是新对象，所以已经提交；原来的 `Conversation` 已经不在这次 Session 里，刷新就失败。

## 完成的行为

助手消息仍由原来的请求 Session 提交，状态为 `complete`。提交之后，对象还在这次 Session 里时继续 `refresh`，再交给反思。对象已经脱离时，按事先留下的会话编号重新取出；取不到就跳过反思，事件流仍发出来源和结束。停止生成仍走原来的独立会话，不经过这次刷新。

## 代码位置

- `app/services/chat_service.py`：`_conversation_after_reply`
- `tests/test_stream_session_detach.py`

## 验证

修复前，同一测试文件里两条失败：服务层在交出会话编号后 `session.close()`，再读剩余事件，异常就是上面的 `InvalidRequestError`；经路由的流式响应正文里有「生成失败，请稍后重试」，且没有 `done`。

修复后：

```text
D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -m pytest tests/test_stream_session_detach.py -v --tb=line --basetemp data/pytest-tmp/stream-detach-final
```

3 passed，退出码 0。关闭请求 Session 后，事件里有 `done`，助手消息内容为「已经写完」、状态为 `complete`。Session 仍持有该对象时同样写完。路由返回的流里没有「生成失败，请稍后重试」。

`ruff check tests/test_stream_session_detach.py`：All checks passed。`app/services/chat_service.py` 原有的引号注解 F821 未改。

## 没做的事

没有让请求 Session 在整段 SSE 期间保持打开。没有改反思开关、配额或限流。没有在浏览器里再发一条真实对话；上面的路由测试覆盖的是页面收到的同一条 SSE。本机 `uvicorn --reload` 会自行载入 `chat_service.py` 的改动。
