# 实现说明：统一错误码端到端

> 日期：2026-10-16
> 计划：`docs/plans/plan_err_001_20261016.md`
> 结果：八种命名错误码可从对应入口触发，响应使用统一错误体；前端按固定文案呈现。访问令牌过期后刷新成功时不提示错误。

## 完成的行为

命名失败的响应主体为 `{ "error": { "code", "message", "retry_after", "request_id" } }`。`request_id` 与响应头 `X-Request-ID` 相同，页面不展示。旧的 `detail`、`code` 和配额数值字段仍保留。

| 场景 | 结果 |
|------|------|
| 刷新令牌过期或无效 | 401 `session_expired`，前端清会话并提示重新登录 |
| 管理员撤销后的旧刷新令牌 | 401 `token_revoked`，与过期分开 |
| 正确密码登录已禁用用户，或凭据访问已禁用用户 | 403 `user_disabled`；错误密码仍是普通 401 |
| 非系统管理员访问已停用租户 | 403 `tenant_suspended`，全屏说明后退出；系统管理员例外保留 |
| 限流 | 429 `rate_limited`。有效秒数时发送按钮禁用并显示「N 秒后可重试」；0、负数、非数字和过大值不倒计时 |
| 配额用尽 | 429 `quota_exceeded`，保留用量数值，不新增消息或源文件 |
| 模型不可用 | 非流式 502；流式为一条错误事件。助手消息状态为 `failed`，两种路径都不触发反思 |
| 上传超过上限 | 413 `file_too_large`，文案写出上限字节数 |
| 刷新成功 | 200，响应没有 `error`，页面不提示错误 |

前后端对限流和超限使用同一句：「请求过于频繁，请稍后重试」「上传文件超过上限」。流式限流在送出前改写成这句，响应正文里不再出现「请稍后再试」。

## 代码位置

- `app/api/errors.py`：错误目录、统一响应和 OpenAPI 示例
- `app/api/routes/auth.py`、`app/api/deps.py`：登录、刷新和当前用户
- `app/api/routes/chat.py`、`app/services/chat_service.py`、`app/agents/pipeline.py`：限流、配额、模型故障和反思跳过
- `app/services/chat_attachments.py`：会话附件超限
- `frontend/src/lib/api-errors.ts`、`frontend/src/lib/http.ts`、`frontend/src/pages/Chat.tsx`、`frontend/src/components/chat/Composer.tsx`
- `README.md`、`docs/swagger.json`

## 验证

解释器：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`，Python 3.12.0。分支 `codex/platform-batch-20261015`，HEAD `873db9c0fb243b0847b4be57242c8f333f4f0b1e`。检查时工作区还有本批其他未提交改动，下面的命令覆盖当时的完整工作区。

```text
ruff check .
退出码 0

mypy app/
退出码 0，211 source files

pytest -q --tb=line --basetemp data/pytest-tmp/err001-final
2525 passed, 3 skipped，退出码 0，567.78s

frontend: npm run typecheck && npm test && npm run lint && npm run build
退出码 0。Vitest 15 files, 153 tests passed。构建成功。
```

先前全量里 5 个失败已按现行契约改断言后重跑：禁用用户为 403 `user_disabled`，超限附件为 413，配额响应保留原数值并带统一错误体，登录审计用户挂在有效租户上。该次失败发生在改断言之前，不能当作最终结果。

## 没做的事

没有在浏览器里逐条操作九种场景。发送按钮的「N 秒后可重试」由组件渲染测试覆盖。没有跑 Linux 或 Ubuntu CI；本次没有进程、信号或文件系统平台分支。没有提交。活动运行内部仍保存限流原文，对外响应已改写。没有另开独立审核。
