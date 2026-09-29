# 实现说明：补记安装三项的验证输出

> 日期：2026-09-29
> 任务：`EVD-002`
> 结果：健康检查与编排配置达到验收。前端构建退出码为 2。清点表第 4 行保持未完成。`tasks.yaml` 中本任务仍为 `ready`，不标 `done`。

## 完成的行为

按 `docs/plans/plan_delivery_16_evidence.md` 的 EVD-002 一节执行健康检查、`docker compose config` 和前端构建，并把输出写入三份安装计划。没有执行 `docker compose up`，没有修改编排文件，没有把探针值写入说明。

解释器：conda 环境 `ai-assistant`（`D:\install\anaconda3\envs\ai-assistant\python.exe`，Python 3.12.0）。Docker 客户端 28.1.1。`docker compose config` 不需要引擎；当时引擎管道 `dockerDesktopLinuxEngine` 不存在，不影响这三条配置检查。

| 命令 | 退出码 | 输出摘要 |
|---|---|---|
| `pytest tests/ -k health -v` | 0 | 10 passed, 2 skipped, 443 deselected, 2 warnings in 30.25s |
| `ruff check app/api/routes/health.py` | 0 | All checks passed! |
| 缺少 `JWT_SECRET_KEY` 的 `docker compose config` | 1 | 插值失败，输出不含占位口令 `change-me-in-production-use-a-random-secret` |
| 缺少 `LLM_API_KEY` 的 `docker compose config` | 1 | 插值失败，输出不含该占位口令 |
| 两项都设置为非空探针的 `docker compose config` | 0 | 服务含 `db`、`milvus`、`app`；`RAG_VECTOR_STORE` 为 `local`；`MILVUS_URI` 为 `http://milvus:19530`；`SERVE_FRONTEND` 为 `"true"`；输出不含该占位口令 |
| `cd frontend; npm run typecheck` | 0 | `tsc --noEmit`，无输出 |
| `cd frontend; npm run build` | 2 | `src/lib/http.ts(3,42): error TS2307: Cannot find module '@/lib/access-token'`。`frontend/dist` 未生成 |

项目 `.env` 会被 Compose 自动读入。只把 PowerShell 变量设为空再执行 `docker compose config` 时，缺少 JWT 的那次退出码为 0，且展开结果含有上述占位口令。计入验收的三次改用系统临时目录里的 env 文件代替默认 `.env`，使被测变量真正处于未设置或仅为当次探针。临时文件已删除。

`npm run typecheck` 使用根 `tsconfig.json`。该文件 `files` 为空。`npm run build` 先执行 `tsc -b`，因此报出了类型检查没有报出的缺失模块。`frontend/src/lib/` 中没有 `access-token` 文件。

构建未通过，所以清点表第 4 行保持未完成，本任务不标 `done`。`tasks.yaml` 与 `AGENTS.md` 未改。下一项仍是 `EVD-002`。完成项仍是 13 项，未完成仍是 4、13、15。80% 未达到。编排配置成功没有写成 Milvus 五条已过。

## 代码位置

- 健康检查：`docs/plans/plan_b1_install.md`「验证结果（2026-09-29，EVD-002）」。
- 编排配置：`docs/plans/plan_inst_002_compose.md`「验证结果（2026-09-29，EVD-002）」。
- 前端类型检查、构建和 `SERVE_FRONTEND`：`docs/plans/plan_inst_003_console.md`「验证结果（2026-09-29，EVD-002）」。
- 清点表：`docs/plans/record_delivery_16.md` 第 4 行结论仍为未完成，证据改为本次输出。
- 本任务没有修改 `app/`、`frontend/`、`docker-compose.yml` 或 `.env`。

## 没做的事

没有执行 `docker compose up`，没有把构建失败改到通过，也没有补上 `access-token` 模块。修复构建需要另开任务；`frontend/` 不在本任务允许路径内。

没有修改 `docs/plans/plan_delivery_16_evidence.md`。该文件不在本任务允许路径内，开头仍写「四条均未实现」。`EVD-001` 已完成，`EVD-002` 本次未收口。
