# 实现说明：按 README 走完首次运行并记录结果

> 日期：2026-09-29
> 任务：`EVD-003`
> 结果：单独空库上走完 README 五步，并看到助手回复。清点表第 15 行改为完成。`tasks.yaml` 中本任务标为 `done`。下一项是 `EVD-004`。

## 完成的行为

按 `docs/plans/plan_delivery_16_evidence.md` 的 EVD-003 一节，用单独 SQLite `data/readme_walk.db` 走 README「快速开始」五步。没有修改 `.env`、README 或页面。没有使用 `data/ai_assistant.db` 或 `data/test_ai_assistant.db`。口令没有写入说明。

启动前在该 Python 进程内设置 `DATABASE_URL=sqlite:///./data/readme_walk.db`，并把 `INITIAL_ADMIN_USERNAME` 与 `INITIAL_ADMIN_PASSWORD` 设为空。Windows 不会把空环境变量传给子进程；若按 README 使用 `uvicorn --reload`，子进程会读到 `.env` 里的初始管理员并自动建号，空库向导就走不成。因此这次在同一进程内清空这两项后启动，且没有使用 `--reload`。启动前 `needs_setup` 为 true，说明没有自动建管理员。

| 步骤 | 结果 |
|---|---|
| 填写环境 | 未改 `.env`。进程内数据库指向 `sqlite:///./data/readme_walk.db`，初始管理员两项为空 |
| 启动 | `GET /api/health` 的 `status` 为 `ok`。数据库与向量库为 `ok`，向量库后端为 `local`。`checks.llm.mode` 为 `real`。控制台在 `http://127.0.0.1:5173` |
| 建立管理员 | `/setup` 标题为「初始化管理员」。用户名 `readme-admin`，口令已提交。进入 `/chat`。随后 `needs_setup` 为 false。再打开 `/setup` 时地址变为 `/chat` |
| 注册 | `/register` 标题为「注册账号」。用户名 `readme-member`，口令已提交。进入 `/chat`，顶栏为 `readme-member` |
| 发送第一条消息 | 在 `/chat` 发送「你好」。页面出现该条用户消息和助手回复，回复末尾标着 `deepseek-chat` |

`checks.llm.mode` 为 `real`，这次回复来自已配置的模型。页面路径与 README 一致，没有因为文案和页面对不上而停止。

清点表第 15 行改为完成。`tasks.yaml` 中本任务改为 `done`。`AGENTS.md` 的下一项改为 `EVD-004`，条数改为 108 条（54 done / 16 ready / 38 backlog）。完成项改为 15 项，未完成改为第 13 项。80% 未达到。

## 代码位置

- 逐步结果：`docs/plans/plan_auth_c1.md`「README 首次运行（2026-09-29）」。
- 清点表：`docs/plans/record_delivery_16.md` 第 15 行结论改为完成。
- 本任务没有修改 `README.md`、`app/`、`frontend/` 或 `.env`。

## 没做的事

没有实现 `ai-assistant init`。没有把 Docker 安装路径当成这次步骤。没有把这次真实模型回复写成检索质量或上线指标。

`data/readme_walk.db` 留在本机，该目录已被忽略。没有修改 `docs/plans/plan_delivery_16_evidence.md`。该文件不在本任务允许路径内，开头仍写「四条均未实现」。`EVD-001`～`EVD-003` 已完成。
