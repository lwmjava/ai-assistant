# 实现说明：日志脱敏与界面错误提示

## 完成的行为

进程启动时会把日志过滤器挂上。子 logger 的记录不会经过根 logger 自己的过滤器，所以安装函数同时挂到已有 handler，并在之后新增的 handler 上补同一层。重复调用不会叠两层。

过滤器先把消息和回溯格式化出来，再做脱敏，然后清空参数，避免百分号再次插值。Uvicorn 访问日志的格式化器要拆开客户端、方法、路径、协议版本和状态码这五个参数，所以这类记录只脱敏参数里的字符串，保留这五个位置。回溯格式化之后的文本和调用栈文本都走同一套替换。脱敏过程自己出错时，这条日志改成「日志已省略」，原文不留下；访问日志出错时改成五个占位参数，避免格式化再抛错。

带空格的口令整段换成 `***`。`password=secret123&user=alice` 仍只遮住口令，`alice` 留下。`Bearer` 后面的 JWT 和 `sk-` 长密钥整段换成 `***`。

登录失败、对话生成失败、流式 `error` 事件、知识库上传失败，正文里如果出现 `Traceback (most recent call last)` 或 `File "...", line`，页面改成「操作失败，请稍后重试」。其它业务短句继续显示。流式连接中断仍是「连接中断」，不经过这层替换。知识库的删除、重建、发布没有改。

## 代码位置

- `app/security/log_sanitizer.py`：无引号字段值可以包含空格，在逗号、`&`、`}`、引号或换行处停下。
- `app/security/log_redaction.py`：日志过滤器 `RedactingLogFilter` 和安装函数 `install_log_redaction`。
- `app/main.py`：导入应用时调用安装函数。
- `frontend/src/lib/http.ts`：`hideStackTrace`。
- `frontend/src/pages/Login.tsx`：登录失败。
- `frontend/src/pages/Chat.tsx`：一次性生成失败和流式生成失败的提示。
- `frontend/src/hooks/useChatStream.ts`：流式 `error` 事件。`onError` 仍写入「连接中断」。
- `frontend/src/pages/Knowledge.tsx`：上传失败。

## 验证

```text
python -m pytest tests/ -k "security or log" -q --tb=line
32 passed, 1 skipped, 433 deselected, 1 warning
exit 0

cd frontend; npm run typecheck
exit 0

python -m ruff check app/security/log_sanitizer.py app/security/log_redaction.py app/main.py tests/test_log_redaction.py
All checks passed
exit 0
```

登录接口本身不记录口令。对应用例在登录失败时额外写了一条包含所提交口令的日志，用来确认过滤器会把它去掉。上传失败走现有的「保存源文件失败」日志，文件名里放入口令和 `sk-` 密钥。对话失败由测试替换对话方法，把请求里的令牌写进日志和回溯。这三条的响应正文都不含 `Traceback`。

打开了 `http://127.0.0.1:5173/login`，登录页能显示。没有在浏览器里提交密码，因此没有点出错密码、上传失败、对话失败和连接中断的页面文案。前端没有测试运行器，替换函数没有在后端再写一套当作已覆盖。接口侧的失败响应不含 `Traceback`，由上面的 pytest 覆盖。

访问日志在保留五个参数后可以正常打出，例如 `GET /api/health HTTP/1.1` 200。

## 没做的事

没有重写错误码，响应仍是 `{detail: ...}`。没有把内部异常类名或回溯返回给浏览器。没有改审计详情的存储格式。没有给其它页面统一换错误文案。SEC-003、SEC-004 未实现。

`docs/AI辅助开发迭代指导.md` 和 `docs/plans/plan_delivery_2027-03-25.md` 仍把本项写成未实现或下一项。这两份不在本任务允许修改的路径里，没有改。
