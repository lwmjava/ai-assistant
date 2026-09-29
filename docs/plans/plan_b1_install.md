# B1：可安装

> 状态：已拆入 `tasks.yaml`（INST-001～INST-003 `done`）
> 来源：交付排期 B1
> 日期：2026-09-25
> 截止：2026-10-30

一家企业在配置 JWT 和一个模型密钥后能把系统启动起来，健康检查能看出数据库和向量库是否连通，并能打开已构建的控制台。

## 任务

| 编号 | 内容 | 状态 |
|---|---|---|
| INST-001 | `/health` 报告数据库与当前向量库连通性；任一失败则整体不是 ok | done |
| INST-002 | Compose 带上 Milvus Lite；启动路径要求 JWT 与一个模型密钥，不再使用仓库内默认口令 | done，方案见 `plan_inst_002_compose.md` |
| INST-003 | 镜像或编排带上已构建的控制台，同一进程或编排可以打开 | done，方案见 `plan_inst_003_console.md` |

## 非目标

不核对向量库五条闭环门槛，那是 C6。不把默认向量库改为 Milvus。不实现租户、用户或问答页面，那是 B2、B3。不把真实密钥写入仓库。

## 验收

三件都完成后，按文档化步骤配置 JWT 与模型密钥即可启动，健康检查能区分数据库和向量库故障，控制台来自构建产物。

## 验证结果（2026-09-29，EVD-002）

解释器：conda 环境 `ai-assistant`（`D:\install\anaconda3\envs\ai-assistant\python.exe`，Python 3.12.0；pytest 8.3.4，ruff 0.8.4）。

`pytest tests/ -k health -v`，退出码 0：`10 passed, 2 skipped, 443 deselected, 2 warnings in 30.25s`。`-k health` 命中 `tests/test_health.py` 里的全部用例，因此摘要里包含 `test_auth_me_requires_token` 与 `test_login_invalid_credentials`。两条警告是 Starlette `anyio.abc.BlockingPortal` 的 `DeprecationWarning`，以及 `app/agents/tools/sandbox/sandbox.py` 中 `ast.NameConstant` 的 `DeprecationWarning`。

`ruff check app/api/routes/health.py`，退出码 0：`All checks passed!`
