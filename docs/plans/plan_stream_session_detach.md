# 流式对话结束后刷新已脱离的会话

> 状态：已实现。说明见 `docs/plans/implementation_stream_session_detach.md`。
> 来源：2026-10-02 本机 `uvicorn app.main:app --reload` 上，`POST /api/chat/stream` 已返回 200，随后日志为「流式对话失败」，`session.refresh(conv)` 抛出 `Instance '<Conversation>' is not persistent within this Session`。页面显示「生成失败，请稍后重试」。不新开任务卡。

## 目标

流式对话正常生成结束后，事件流里不再出现「生成失败，请稍后重试」。助手消息仍以 `complete` 写入，来源事件仍发出。反思仍能读到刚写入的回复。

## 现状

`chat_stream` 先交出会话编号。路由在 `app/api/routes/chat.py` 用 `await stream.__anext__()` 取走这一条，再返回 `EventSourceResponse`。这段提前取事件是 2026-10-01 配额提交 `5796678` 加上的，用来在开流前把超限改成 JSON 429。

FastAPI 0.115 在端点函数返回后就退出 `yield` 依赖。`get_session` 的 `with Session()` 随之 `close()`。SQLAlchemy 2.0 默认的 `close()` 会摘掉身份映射里的对象，但 Session 还能继续写入。后面的助手消息是新对象，所以 `_persist_assistant` 可以提交。紧接着的 `session.refresh(conv)` 仍使用摘掉之前的那个 `Conversation`，于是抛出上述异常。路由接住后向前端发送「生成失败，请稍后重试」。

`session.refresh(conv)` 本身写于 2026-09-27，当时整段生成都在同一次请求会话里。停止路径已经另开会话写入，因为断开时请求会话可能已经解除绑定。成功路径没有按同样的约束处理。

限流拒绝在进入生成前返回，不会执行这次刷新。

## 方案

助手消息提交之后，若 `conv` 仍属于这次请求的 Session，保持原来的 `refresh`，再交给反思。若已经脱离，按事先留下的 `conversation_id` 重新取出，再交给反思。取不到则跳过反思，不因此让事件流失败。不改变停止路径，不改变配额和限流的返回方式。

## 非目标

不把请求 Session 改成在整段 SSE 期间保持打开。不改反思的开关、内容和异步方式。不改助手消息的字段。不处理限流 429。

## 验收

在生成器交出会话编号后关闭请求 Session，再把剩余事件读完：没有异常，事件里有 `done`，没有被路由换成的失败文案，库里有一条 `status=complete` 的助手消息。Session 仍绑定该对象时，刷新行为与现在相同。
