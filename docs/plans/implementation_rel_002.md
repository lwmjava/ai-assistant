# 实现说明：默认 SQLite 的备份步骤与一次恢复

> 日期：2026-10-02
> 计划：`docs/plans/plan_d4.md` 的 REL-002
> 结果：README 写明用 Python 标准库 `backup` 备份默认 SQLite。临时库上的恢复文件能启动应用，`GET /api/health` 为 `ok`，`alembic_version` 与源库相同。开发库 `data/ai_assistant.db` 的修改时间没有变化。临时目录已删除。

## 完成的行为

README「备份与恢复」放在「快速开始」之后。步骤要求先停掉占用数据库的进程，再用 `sqlite3.Connection.backup`，不把只复制主文件写成步骤。备份文件不得提交、不得写入日志。`data/knowledge` 不在这次备份里。向量库为 `local` 时向量在同一个 SQLite 中；改成 Milvus 后，只恢复 SQLite 不会带回 Milvus 里的数据。

## 恢复记录

工作目录：仓库根目录 `E:\culture\SmartCustomerServiceSystem\ai-assistant`。数据库文件放在系统临时目录，名为 `source.db` 和 `restored.db`。解释器是 conda 环境 `ai-assistant` 的 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`。没有把连接串或密钥写入本说明。

1. 进程环境把数据库指到 `source.db`，在仓库根目录执行 `python -m app.cli migrate --yes`。退出码 0，输出含 `Migrations complete.`
2. 在 `source.db` 写入标记表 `restore_probe`，标记值为 `restore-marker`。此时 `alembic_version` 为 `e4b7a2c85d01`。源库旁边没有 `-wal` 或 `-shm`。
3. 用标准库 `backup` 写入 `restored.db`。恢复文件里的标记仍是 `restore-marker`，`alembic_version` 仍是 `e4b7a2c85d01`。
4. 另起进程，数据库指到 `restored.db`，执行 `python -m uvicorn app.main:app --host 127.0.0.1 --port 18765`。`GET /api/health` 返回 HTTP 200，`status`、`checks.database.status`、`checks.vector_store.status` 都是 `ok`，向量库后端是 `local`。随后停止该进程。
5. 回滚：删除系统临时目录。进程刚停时 Windows 仍占用 `restored.db`，第一次删除失败；文件释放后再次删除成功。现在该临时目录已经不在。

演练前后 `data/ai_assistant.db` 都存在，修改时间相同。没有打开或复制这个文件。

## 代码位置

- `README.md` 的「备份与恢复」

没有新增备份脚本。

## 验证

按上面的命令在临时目录执行一次。迁移退出码 0，健康检查 HTTP 200 且 `status` 为 `ok`，两边修订号都是 `e4b7a2c85d01`，标记值为 `restore-marker`，临时目录已删除，开发库修改时间未变。

## 没做的事

没有备份生产库。没有写 `scripts/backup.sh`。没有做 PostgreSQL、Milvus 或上传目录备份，没有测量恢复时间。没有把以前的 Milvus 卷拷贝当作这次记录。
