# 实现说明：迁移命令与创建系统管理员

> 日期：2026-10-01
> 计划：`docs/plans/plan_d3.md` 的 CLI-001 节
> 结果：空库可以迁移到当前版本。命令创建的系统管理员可以登录。重复创建、过短密码和重叠创建会被拒绝。输出和日志不含密码。

## 完成的行为

安装本仓库后，`ai-assistant migrate` 把数据库升级到当前 Alembic head。没有待执行迁移时成功退出，并说明已经是最新。`--yes` 跳过确认。`python -m app.cli` 调用同一入口。`python -m app.core.migration` 仍然只做迁移检查、升级和历史，不创建管理员。

`ai-assistant admin create-superuser --username <name>` 从 `INITIAL_ADMIN_PASSWORD` 读取密码。密码至少 8 位。创建在一个数据库事务里完成：写入前统计已有 `system_admin`，已有则拒绝；用户名冲突则拒绝。SQLite 使用 `BEGIN IMMEDIATE`，同一库上重叠的两次创建最多成功一次。PostgreSQL 路径使用事务级咨询锁，本批没有在 PostgreSQL 上执行。成功和失败的日志只写用户名。标准输出和标准错误不打印密码。

环境变量启动引导和 `/setup` 没有改。管理接口仍可以创建多名系统管理员，命令本身不增加「全局只能有一个」的数据库约束。

成员表不在迁移脚本里，由应用启动时建表补上。创建命令在写入前补齐这张表，再把成员关系与用户放进同一次提交。

## 代码位置

- 命令入口：`app/cli.py`
- 创建事务：`app/services/auth_service.py` 的 `create_cli_superuser`
- 迁移仍走：`app/core/migration.py` 的 `_cli_migrate`。脚本目录按 `alembic.ini` 的位置解析，不随当前工作目录变化
- 安装入口：`pyproject.toml` 的 `[project.scripts]`
- 用法：`README.md` 快速开始第 1 步和第 3 步
- 能力矩阵：`docs/product/as-is-capability-matrix.md` 第 1 节
- 测试：`tests/test_cli.py`

## 验证

解释器是 conda 环境 `ai-assistant`：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`（Python 3.12.0）。

默认临时目录 `data/pytest-tmp/run` 被占用，清理时报 WinError 5，用例没有开始执行。下面这次改用单独的临时目录，退出码 0。

```text
python -m pytest tests/test_cli.py -v --tb=short --basetemp data/pytest-tmp/cli-001b
6 passed in 25.54s
exit 0
```

```text
python -m ruff check app/cli.py app/services/auth_service.py app/core/migration.py tests/test_cli.py
All checks passed!
exit 0
```

`pip install -e . --no-deps` 后，用临时库 `data/pytest-tmp/cli-script/empty.db` 调用已安装的 `ai-assistant.exe`（该脚本目录当时不在 PATH 上，所以用了完整路径）：

- `migrate --yes` 退出码 0，`alembic_version` 为 `d7c2a91e4b18`
- 再执行一次迁移，输出含 `Database is up to date`，退出码 0
- `admin create-superuser --username cli-admin` 退出码 0
- 再用另一个用户名创建，退出码 1，系统管理员人数仍为 1
- 上述输出里没有测试用的密码

测试子进程的工作目录和 `DATABASE_URL` 都指向临时文件，没有使用 `data/ai_assistant.db`。

## 没做的事

- 没有 `init`、`start`、`stop`、`status`、`logs`
- 没有从命令参数读取密码，也没有强制首次登录改密
- 没有做 PostgreSQL 迁移演练
- 没有改 `/setup` 和环境变量引导
- 没有跑全量 pytest、`mypy app/` 或前端构建
