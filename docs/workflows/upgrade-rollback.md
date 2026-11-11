# 升级与回滚

本文说明单机生产编排的停机升级流程，以及如何用升级前备份回到升级前状态。这不是不停机升级，也不是蓝绿部署。内部 RTO/RPO 目标见 [恢复时间与数据丢失目标](rto-rpo.md)；本流程不测量、也不证明这两个目标已经达标。

操作前必须明确目标环境。脚本会拒绝已知的生产项目名（包括 `ai-assistant`、`production`、`prod` 等），也不会把恢复写入仓库工作区里的 `data/knowledge`。未经单独授权，不得对生产项目、已有生产卷或生产数据库做升级或回滚。命令不是本次会话的执行授权。不要把环境文件、连接串、口令或备份内容打印到聊天、终端日志或 Git。

数据备份与隔离恢复的细则以 [生产备份与恢复](backup-restore.md) 为准。升级前必须留下一份完整备份（PostgreSQL、上传目录、Milvus 卷）。

## 升级流程

升级按以下顺序执行，中途允许写请求进入会导致备份与迁移不一致。

1. **停写**  
   停止应用服务（生产编排中的 `app`），不再接受新的用户写入。依赖服务可保持运行，但向量卷归档仍须按备份说明在需要时停止 `milvus`、`etcd` 和 `minio`。

2. **升级前备份**  
   使用 `scripts/backup.sh` 生成完整备份，并核对 `manifest.json` 与校验和。没有这份备份，不得继续迁移，也无法按本文回滚。

3. **迁移**  
   在停写后执行数据库迁移。应用侧命令为 `python -m app.cli migrate --yes`（等价于将 schema 升到当前版本）。跨版本不兼容变更须写入 `CHANGELOG.md` 的 Breaking 区后再升级；本仓库若尚未建立该文件，先在升级记录中写明不兼容点，再按发布文档约定补记。

4. **启动**  
   启动应用及必要依赖。若镜像或应用版本有变化，启动的是目标版本，而不是升级前的版本。

5. **验证**  
   至少核对：应用健康检查通过；数据库迁移修订与目标版本一致；升级前抽样的业务行、上传文件和向量数据仍可访问。验证失败时不要继续导入新数据，按下面的回滚步骤处理。

在已确认的隔离目标上，可用脚本串联前四步（验证仍须人工或按演练核对）：

```sh
bash scripts/upgrade.sh upgrade \
  --env-file /secure/assistant-stage.env \
  --project assistant-stage \
  --output /secure/backups/pre-upgrade \
  --migrate-command 'python -m app.cli migrate --yes' \
  --confirm-isolated-target
```

无额外参数的检查只验证安全拒绝和 dry-run，不连接数据库，也不改任何卷：

```sh
bash scripts/upgrade.sh --check
```

## 回滚流程

回滚的目标是：服务与数据都回到升级前状态。依据是升级前那份完整备份，而不是手工反向执行迁移。

1. **停写**  
   停止已升级的应用，避免回滚过程中继续写入。

2. **按备份恢复**  
   恢复 PostgreSQL、上传目录和 Milvus 卷。PostgreSQL 使用逻辑恢复（可在同一隔离库上以 `--clean` 回到备份时刻的 schema 与数据）。上传目录和 Milvus 卷若已非空，只允许在已确认的隔离目标上，且卷名必须属于该项目，才能替换后恢复。已知生产项目名和受保护的已有卷（例如 `ai-assistant_*`）会被拒绝。

3. **启动升级前版本**  
   启动升级前所使用的应用镜像或版本。只恢复数据却继续运行新版本，在有 Breaking 变更时通常不能视为回滚成功。

4. **验证**  
   核对迁移修订、抽样业务数据、上传文件和向量探针或集合状态与升级前备份一致，且升级后新增的数据或字段已经消失。

```sh
bash scripts/rollback.sh \
  --from /secure/backups/pre-upgrade/<timestamp> \
  --env-file /secure/assistant-stage.env \
  --project assistant-stage \
  --upload-dir /tmp/isolated-knowledge \
  --confirm-isolated-target
```

## 安全约束

- 不要在生产项目上做破坏性演练。隔离演练应使用一次性项目名、独立上传目录和一次性卷。
- 禁止把 `docker compose down -v` 或删除已有生产卷当作常规回滚。
- 回滚失败时保留现场并排查，不要清理未确认归属的卷。
- 开发默认 SQLite 的步骤仍见仓库根目录 README，不能代替本文的跨版本演练。

隔离演练记录见 [隔离升级回滚演练记录](upgrade-rollback-drill.md)。该记录证明脚本可以在一次性环境里完成一次跨版本升级并回滚到升级前状态；它不是生产项目演练，也不能用来宣称 RTO 或 RPO 已经达标。
