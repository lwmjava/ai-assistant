# 实现说明：补记安装三项的验证输出

> 日期：2026-09-29
> 任务：`EVD-002`
> 结果：健康检查、编排配置、前端类型检查与构建退出码均为 0。清点表第 4 行改为完成。`tasks.yaml` 中本任务标为 `done`。下一项是 `EVD-003`。

## 完成的行为

按 `docs/plans/plan_delivery_16_evidence.md` 的 EVD-002 一节执行健康检查、`docker compose config` 和前端构建，并把输出写入三份安装计划。没有执行 `docker compose up`，没有修改编排文件，没有把探针值写入说明。

同一天早些时候 `npm run build` 退出码为 2，因为当时工作区没有 `frontend/src/lib/access-token.ts`。本次重跑时该文件已在工作区。`.gitignore` 第 13 行已改为 `/lib/`，该文件不再被忽略。本任务没有改 `.gitignore`，也没有把该文件加入版本库。

解释器：conda 环境 `ai-assistant`（`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`，Python 3.12.0；pytest 8.4.2，ruff 0.8.4）。文档里写的 `D:\install\anaconda3\envs\ai-assistant` 在本机不存在，使用的是同名环境的当前路径。Docker 客户端 28.5.1。`docker compose config` 不需要引擎；引擎管道 `dockerDesktopLinuxEngine` 不存在，不影响这三条配置检查。

| 命令 | 退出码 | 输出摘要 |
|---|---|---|
| `pytest tests/ -k health -v` | 0 | 10 passed, 2 skipped, 443 deselected, 1 warning in 3.63s |
| `ruff check app/api/routes/health.py` | 0 | All checks passed! |
| 缺少 `JWT_SECRET_KEY` 的 `docker compose config` | 1 | 插值失败，输出不含占位口令 `change-me-in-production-use-a-random-secret` |
| 缺少 `LLM_API_KEY` 的 `docker compose config` | 1 | 插值失败，输出不含该占位口令 |
| 两项都设置为非空探针的 `docker compose config` | 0 | 服务含 `db`、`milvus`、`app`；`RAG_VECTOR_STORE` 为 `local`；`MILVUS_URI` 为 `http://milvus:19530`；`SERVE_FRONTEND` 为 `"true"`；输出不含该占位口令 |
| `cd frontend; npm run typecheck` | 0 | `tsc --noEmit`，无输出 |
| `cd frontend; npm run build` | 0 | Vite 5.4.8，`built in 7.01s`，`frontend/dist` 已生成 |

项目 `.env` 会被 Compose 自动读入。计入验收的三次使用 `docker compose --env-file` 指向系统临时目录中的 env 文件，使被测变量真正处于未设置或仅为当次探针。临时文件已删除。

`npm run typecheck` 使用根 `tsconfig.json`。该文件 `files` 为空，且这次没有带 `-b`。`npm run build` 先执行 `tsc -b`。本次 `frontend/src/lib/access-token.ts` 存在，构建通过。

清点表第 4 行改为完成。`tasks.yaml` 中本任务改为 `done`。`AGENTS.md` 的下一项改为 `EVD-003`，条数改为 108 条（53 done / 17 ready / 38 backlog）。完成项改为 14 项，未完成改为 13、15。80% 未达到。编排配置成功没有写成 Milvus 五条已过。

## 代码位置

- 健康检查：`docs/plans/plan_b1_install.md`「验证结果（2026-09-29，EVD-002）」。
- 编排配置：`docs/plans/plan_inst_002_compose.md`「验证结果（2026-09-29，EVD-002）」。
- 前端类型检查、构建和 `SERVE_FRONTEND`：`docs/plans/plan_inst_003_console.md`「验证结果（2026-09-29，EVD-002）」。
- 清点表：`docs/plans/record_delivery_16.md` 第 4 行结论改为完成。
- 本任务没有修改 `app/`、`frontend/`、`docker-compose.yml` 或 `.env`。

## 没做的事

没有执行 `docker compose up`。没有把编排配置成功写成 Milvus 五条已过。

`frontend/src/lib/access-token.ts` 当前是未跟踪文件。`.gitignore` 的修改也还在工作区，未进入版本库。本任务允许路径不含这两处。干净检出在该文件入库前，`npm run build` 会再次因缺少 `@/lib/access-token` 失败。

没有修改 `docs/plans/plan_delivery_16_evidence.md`。该文件不在本任务允许路径内，开头仍写「四条均未实现」。`EVD-001` 与 `EVD-002` 已完成。
