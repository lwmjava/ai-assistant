# 隔离备份恢复演练记录

日期：2026-11-11（Asia/Shanghai）。工作区：`E:\culture\SmartCustomerServiceSystem\ai-assistant-nfr010`，解释器为 conda 环境 `ai-assistant`。本次只使用一次性容器和卷，没有启动或删除已有的 `ai-assistant_*`、`nfr003-*`、`rag015-*` 资源，也没有对生产数据或仓库 `data/knowledge` 做恢复。口令与连接串未写入本记录。

## 命令与环境

先执行 `bash scripts/backup.sh --dry-run`（退出码 0）和 `python scripts/backup_restore.py self-check`（退出码 0）。随后在隔离环境做三份数据的实时备份与恢复：

- 镜像：本地已有的 `postgres:16-alpine`（PostgreSQL 16.15），容器数据目录使用 tmpfs，`--pull=never`。
- 来源项目/容器：`bakdsrc613f687b3978` / `bakd-pg-src-613f687b3978`。
- 目标项目/容器：`bakddst613f687b3978` / `bakd-pg-dst-613f687b3978`。
- 上传目录为临时宿主路径，不使用仓库知识库目录。
- 向量侧按文档对 `milvusdata`、`etcddata`、`miniodata` 三份卷做归档恢复；卷内写入探针文件，未启动真实 Milvus 服务，也未查询集合行数。

备份与恢复通过 `scripts/backup_restore.py` 完成，等价于文档中的 `scripts/backup.sh` / `scripts/restore.sh`。恢复使用 `--confirm-isolated-target`。备份目录位于被忽略的 `data/validation/backup-drill-613f687b3978/`。

## 结果

备份时间：2026-11-11 23:51:30（Asia/Shanghai）。`manifest.json` 包含 postgres、uploads、milvus 三项，退出码 0。

| 组件 | 产物 | SHA256 | 恢复核验 |
|---|---|---|---|
| PostgreSQL | `postgres/database.dump`（custom，1641 字节，pg_dump 16.15） | `4ef38cb16140ca9efb8eec66b003c60734ca24398b0b914ad97943837d4b1259` | 目标库 `restore_probe.marker` 为 `backup-restore-marker` |
| 上传目录 | `uploads/knowledge.tar.gz`（211 字节，1 个文件） | `be51786eb5495517212bab197039e72b694a38fee247bd8d1244d5b4f009628d` | 目标目录 `tenant-drill/marker.txt` 内容一致 |
| 向量卷 | `milvus/*.tar.gz`（三份探针归档） | milvusdata `8d9623dc97585f0aa42f59e5ba5d8c327775651930b844188b54be384dd98f27`；etcddata `77e2c7b726eccd12f8634360778367f6d62790ee9a06ee36a7ddb1c5e2ceadfe`；miniodata `117b2671c80dd9ddfab333d6474f197c0ce33bec1648781bc3eb2f657d65463a` | 三个目标卷中的 `probe.txt` 均与来源一致 |

演练状态 `passed`。结束后一次性容器和 `bakdsrc*` / `bakddst*` 卷已删除；原有 `ai-assistant_pgdata`、`ai-assistant_milvusdata`、`ai-assistant_etcddata`、`ai-assistant_miniodata` 等卷仍在。

## 回滚

隔离容器与卷已在演练结束时删除。源环境是一次性实例，不涉及生产回滚。若备份目录不再需要，删除 `data/validation/backup-drill-613f687b3978/` 即可。

## 未覆盖

- 没有对运行中的 Milvus 做 collection 级备份或检索核验。
- 没有在完整生产编排项目上演练，也没有测量 RTO/RPO。
- 没有使用仓库工作区中的真实知识库文件或已有数据库卷。

## 与 RTO/RPO 目标的关系

本次演练证明三份数据在隔离环境可按脚本恢复，但没有记录恢复开始和结束时间，数据只是探针级夹具，并且未在完整生产编排上启动真实 Milvus 做检索核验。因此不能用本记录核算或宣称已达到 [RTO/RPO 目标](rto-rpo.md)。

后续若要对照目标判定是否合格，记录中须写出：最近一次完整备份的 `manifest.json` 时间戳、宣布开始恢复的时间、三项核验及应用健康检查通过的时间（均为 Asia/Shanghai），再按该说明计算 RPO 与 RTO。
