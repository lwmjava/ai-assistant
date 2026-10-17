# API-001 验收记录

日期：2026-10-17。用户要求按当前候选验收页面请求前缀、前端四门、全量 pytest，以及同提交 CI。未改业务实现，未提交、推送、部署，未使用工作区 `.env` 中的真实账号。状态保持 `in_review`。

## 候选

- HEAD `7d8bcbc0c61f3418ec91a78ea029317888a271c8`
- 分支 `codex/platform-batch-20261015`
- 验收开始时工作区干净
- 解释器 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`，Python 3.12.0
- Node v22.20.0，Ruff 0.8.4，mypy 1.14.1
- 平台 Windows

相对 `origin/codex/platform-batch-20261015` 为 ahead 2、behind 11。该 SHA 能在 GitHub 上查到，但 Actions 运行数为 0，commit status 数为 0。本机没有 `gh`。

## 页面请求

隔离服务只监听 `127.0.0.1:8027`，数据库为 `data/api001-page/case.db`。进程环境覆盖为 development、mock 模型、mock Embedding、访问令牌 2 分钟。空库引导出合成账号 `admin` / `system` / `culture`，角色分别为 system_admin、tenant_admin、member。页面走 `127.0.0.1:5175`，代理指向该服务。没有使用已经在跑的 8000 端口。

浏览器以租户管理员 `system` 完成：登录、首次改密、等待令牌进入刷新窗口后发送「验收问候」、导出本租户对话。记录只含方法、路径和状态，见 `data/api001-page/requests.json`。

| 操作 | 请求 | 状态 |
|---|---|---|
| 登录 | POST `/api/v1/auth/login`，随后 GET `/api/v1/auth/me` | 200 |
| 首次改密 | POST `/api/v1/auth/change-password`，随后 GET `/api/v1/auth/me` | 200 |
| 进入对话 | GET `/api/v1/health`、`/api/v1/auth/memberships`、`/api/v1/chat/conversations` | 200 |
| 刷新后发送 | POST `/api/v1/chat/stream` 先 401，POST `/api/v1/auth/refresh` 200，再 POST `/api/v1/chat/stream` | 200 |
| 授权导出 | POST `/api/v1/export/conversations` | 200 |

这 15 次调用的路径都在 `/api/v1` 下。回复正文标明 mock-model，只证明请求路径，不证明模型质量。隔离服务上 `GET /api/v1/health` 为 200，`GET /api/health` 为 404。现场 OpenAPI 共 65 条 path，全部以 `/api/v1/` 开头。验收后已停止 8027 和 5175。

## 前端四门

在 `frontend` 执行，日志 `data/api001-frontend.log`，终端退出码 0。

| 命令 | 退出码 | 结果 |
|---|---|---|
| `npm run typecheck` | 0 | 通过 |
| `npm test` | 0 | 15 files，153 passed |
| `npm run lint` | 0 | 通过 |
| `npm run build` | 0 | 9.29 秒完成 |

## 全量 pytest、Ruff、mypy

```text
DATABASE_URL=sqlite:///./data/api001-acceptance-pytest.db
python -m pytest --basetemp data/pytest-tmp/api001-acceptance -q --tb=line
```

日志 `data/api001-acceptance-pytest.log`：2530 passed，3 skipped，3 warnings，696.93 秒。汇总为零失败。3 个 skipped 仍是 Windows 上未证明的平台用例，不能写成 Linux 通过。

`python -m mypy app/` 输出 `Success: no issues found in 211 source files`，日志 `data/api001-mypy.log`。

删除前 `python -m ruff check .` 退出码 1，日志 `data/api001-ruff.log`。3 个问题都在 `docs/evaluations/llm001_two_vendor_live_20261017.py`：两处 import 排序，一处行宽 122。该一次性脚本已删除。删除后同一命令退出码 0，输出 `All checks passed!`。

## 结论

页面路径、OpenAPI 前缀、前端四门和全量 pytest 已在当前 HEAD 上取到。还不能关闭：

1. 该提交没有 Backend、Frontend、Agent official baseline 的成功运行和 URL。删除脚本后的 Ruff 通过只在工作区执行，还没有进入最终提交的 CI。
2. Windows 的 3 个 skipped 没有 Linux 证据。镜像健康探针没有在容器里实跑，现有 CI 也不会构建该镜像。

`API-001` 保持 `in_review`。
