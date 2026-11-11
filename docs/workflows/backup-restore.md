# 生产备份与恢复

本文说明如何备份和恢复 PostgreSQL、知识库上传目录和 Milvus 向量数据。开发默认 SQLite 的步骤仍见仓库根目录 README，不能代替本文的演练。本文不配置异地复制、加密备份或定时调度。

操作前必须明确目标环境。未经单独授权，不得把恢复写入生产项目、生产卷或仓库工作区里的 `data/knowledge`。命令不是本次会话的执行授权。不要把环境文件、连接串、口令或备份内容打印到聊天、终端日志或 Git。

## 覆盖范围

生产编排（`docker-compose.prod.yml`）中的三份数据分别为：

| 数据 | 来源 | 备份产物 |
|---|---|---|
| PostgreSQL | Compose 服务 `db`，容器内 `pg_dump -Fc` | `postgres/database.dump` |
| 上传文件 | 应用卷 `appdata` 中的 `/app/data/knowledge`，对应源码中的知识库源文件目录 | `uploads/knowledge.tar.gz` 与 `uploads/files.json` |
| 向量数据 | 卷 `milvusdata`、`etcddata`、`miniodata` 的一致性归档 | `milvus/milvusdata.tar.gz`、`milvus/etcddata.tar.gz`、`milvus/miniodata.tar.gz` |

每次备份还会写入 `manifest.json`（含校验和，不含凭据）。命名卷本身不是备份。只恢复数据库不会带回上传文件或 Milvus 数据。

若知识库目录在宿主路径上而不是 Compose 卷中，使用 `--upload-dir` 指向该目录。不要把日志卷或整个 `data/` 目录当作上传目录备份。

## 备份

在仓库根目录执行。必须显式提供隔离的 `--env-file` 和 Compose 项目名，不要依赖工作区 `.env`。

```sh
bash scripts/backup.sh --dry-run
```

无额外参数的 `--dry-run` 只打印计划并检查编排文件是否存在，不读取环境文件，不连接数据库，也不写备份。实时备份示例：

```sh
bash scripts/backup.sh \
  --env-file /secure/assistant.prod.env \
  --project assistant-stage \
  --output /secure/backups
```

脚本会在输出目录下创建时间戳子目录。备份 Milvus 前会停止 `milvus`、`etcd` 和 `minio`，归档完成后再启动；PostgreSQL 使用逻辑备份，不必为此停库。若需要在已停止的服务上操作，可加 `--skip-compose-control`。可用 `--postgres-container` 和 `--upload-dir` 显式指定容器或宿主目录，用于隔离演练。

成功后核对该目录中的 `manifest.json` 与各产物校验和。备份目录与数据库同样敏感，不进仓库、不进日志。

## 恢复

恢复只允许写入事先确认的隔离空目标。必须同时给出 `--confirm-isolated-target`。名为 `assistant`、`production`、`prod`、`ai-assistant`、`aiassistant`、`aigates` 的项目会被拒绝。目标卷或上传目录必须为空；脚本不会自动清库或删卷。覆盖已有重要数据或恢复到生产前必须另获明确授权。

```sh
bash scripts/restore.sh \
  --from /secure/backups/<timestamp> \
  --env-file /secure/assistant-restore.env \
  --project assistant-restore-stage \
  --confirm-isolated-target
```

建议恢复顺序：先恢复 PostgreSQL 和上传目录，再恢复 Milvus 卷，然后启动 etcd、MinIO、Milvus 和应用。恢复后至少核对：

1. 数据库迁移修订（`alembic_version`）与备份来源一致，用户/租户/对话等业务行可查询。
2. 上传目录文件数量与 `uploads/files.json` 中的路径和校验和一致。
3. Milvus 集合可连接，集合名与向量条数符合来源预期；如使用生产应用，再做健康检查与一次检索/业务核验。

宿主目录恢复示例（仅用于隔离目录，禁止指向仓库 `data/knowledge`）：

```sh
bash scripts/restore.sh \
  --from /secure/backups/<timestamp> \
  --upload-dir /tmp/isolated-knowledge \
  --skip-postgres \
  --skip-milvus \
  --confirm-isolated-target
```

## 回滚与安全

- 备份失败时不要使用不完整目录做恢复；删除或隔离未完成的输出即可，不影响源环境。
- 恢复失败时保留目标环境并排查，禁止用 `down -v` 或删除命名卷作为常规回滚。
- 不要在生产项目上做破坏性演练。隔离演练应使用一次性项目名和独立环境文件。
- RTO/RPO 数字不在本说明范围内，需要单独的指标演练。

## 本地检查

```sh
bash scripts/backup.sh --dry-run
python scripts/backup_restore.py self-check
```

`self-check` 只在临时目录验证上传归档、恢复安全拒绝和 dry-run，不访问生产数据，也不替代下面的三份真实演练。

隔离演练记录见 [隔离备份恢复演练记录](backup-restore-drill.md)。该记录证明脚本可以对 PostgreSQL、上传目录和 Milvus 相关卷完成一次可恢复备份；它不是生产项目演练，也不包含运行中 Milvus 集合的检索核验。
