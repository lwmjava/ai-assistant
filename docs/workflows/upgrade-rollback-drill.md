# 隔离升级回滚演练记录

日期：2026-11-12（Asia/Shanghai）。工作区：`E:\culture\SmartCustomerServiceSystem\ai-assistant-ops003`，解释器为 conda 环境 `ai-assistant`。本次只使用一次性容器和卷，没有启动或删除已有的 `ai-assistant_*`、`nfr003-*`、`rag015-*` 资源，也没有对生产数据或仓库 `data/knowledge` 做恢复。口令与连接串未写入本记录。本记录不测量、也不证明 RTO 或 RPO 已经达标。

## 命令与环境

先执行 `bash scripts/upgrade.sh --check`（退出码 0）。该检查覆盖升级计划 dry-run、生产项目拒绝、受保护卷拒绝，以及隔离上传目录替换，不连接已有数据库，也不改已有卷。

随后在隔离环境做一次跨版本升级与回滚：

- 镜像：本地已有的 `postgres:16-alpine`（PostgreSQL 16.15），容器数据目录使用 tmpfs，`--pull=never`。
- 隔离项目/容器：`upgd0015136ca6c9` / `upgd-pg-0015136ca6c9`。
- 升级前版本夹具：schema 修订 `rev_drill_pre`，应用标记 `0.1.0-drill`，上传文件与三份向量卷中的探针均为升级前内容。
- 升级后版本夹具：在停写并完成升级前备份后，迁移为修订 `rev_drill_post`、应用标记 `0.2.0-drill`，并增加不兼容字段 `extra_flag`；同时写入升级后上传文件与卷探针，用于确认回滚会撤掉这些变更。
- 向量侧按备份说明对 `milvusdata`、`etcddata`、`miniodata` 三份一次性卷做归档恢复；卷内写入探针文件，未启动真实 Milvus 服务，也未查询集合行数。未启动生产应用镜像。

升级与回滚通过 `scripts/upgrade_rollback.py` 完成，等价于文档中的 `scripts/upgrade.sh` / `scripts/rollback.sh`。回滚使用 `--confirm-isolated-target`，并按升级前备份恢复 PostgreSQL、上传目录和向量卷。备份与结果位于被忽略的 `data/validation/upgrade-drill-0015136ca6c9/`。

## 结果

升级前备份时间：2026-11-12 00:15:21（Asia/Shanghai）。`manifest.json` 包含 postgres、uploads、milvus 三项。演练在 2026-11-12 00:15:13 开始，00:15:32 结束，退出码 0，状态 `passed`。

| 组件 | 升级前备份产物 | SHA256 | 核验 |
|---|---|---|---|
| PostgreSQL | `postgres/database.dump`（custom，2566 字节，pg_dump 16.15） | `ce31cfe536857166a52dcaabc7cb9fa4016b20d90921670f1efeb750a3e0c8d9` | 升级后修订为 `rev_drill_post` 且存在 `extra_flag`；回滚后修订回到 `rev_drill_pre`，标记回到 `upgrade-pre-marker`，`extra_flag` 列消失 |
| 上传目录 | `uploads/knowledge.tar.gz`（209 字节，1 个文件） | `cbcc0c4a02b9e5e023d493d666e1af333c5b9b9d6d5a724d12cbd1d280630779` | 升级后出现 `post.txt`；回滚后该文件消失，`tenant-drill/marker.txt` 与升级前一致 |
| 向量卷 | `milvus/*.tar.gz`（三份探针归档） | milvusdata `c713cee1dcbf5316383293afe68e2d6338c821c2b7ee6bd7e7437642c9f25fc4`；etcddata `c713cee1dcbf5316383293afe68e2d6338c821c2b7ee6bd7e7437642c9f25fc4`；miniodata `af34ce5c71808af11c4619d1973929e575d9d7f853a94da8c9b3edd97a77d853` | 升级后三份卷中的 `probe.txt` 均为 `volume-post`；回滚后均回到 `volume-pre` |

结束后一次性容器和 `upgd0015136ca6c9_*` 卷已删除；原有 `ai-assistant_pgdata`、`ai-assistant_milvusdata`、`ai-assistant_etcddata`、`ai-assistant_miniodata` 等卷仍在，对应已有容器状态未改变。

## 回滚

隔离容器与卷已在演练结束时删除。源环境是一次性实例，不涉及生产回滚。若演练目录不再需要，删除 `data/validation/upgrade-drill-0015136ca6c9/` 即可。

## 未覆盖

- 没有对运行中的应用镜像或真实 Milvus 集合做健康检查与检索核验。
- 没有在完整生产编排或已有生产卷上演练。
- 没有测量 RTO/RPO；本次墙钟不能代表生产数据规模，也不能用来宣称已达到恢复目标。
- 不兼容字段只记录在本演练夹具中。仓库若尚未建立 `CHANGELOG.md` 的 Breaking 区约定，正式发布文档仍须另行补齐。

## 与 RTO/RPO 目标的关系

本次演练证明：在隔离环境中可以按“停写、升级前备份、迁移、启动验证”完成一次跨版本升级，并按升级前备份把 schema、上传文件和向量卷探针恢复到升级前状态。数据只是探针级夹具，未在完整生产编排上计时或做检索核验，因此不能用本记录核算或宣称已达到 [RTO/RPO 目标](rto-rpo.md)。
