# 实现说明：重跑 Milvus 五条门槛并记录结果

> 日期：2026-09-29
> 任务：`EVD-004`
> 结果：本条标为 `blocked`。原因是 `pymilvus==2.4.7` 与镜像 `milvusdb/milvus:v2.5.11` 在 `create_index` 调用失败。第 1 到第 4 条未执行。清点表第 13 行保持未完成。`tasks.yaml` 中本任务不标 `done`。默认向量库仍是 `local`。

## 完成的行为

按 `docs/plans/plan_delivery_16_evidence.md` 的 EVD-004 一节执行。没有修改 `app/`、`docker-compose.yml`、`.env.example`、`requirements.txt`、`pyproject.toml` 或冻结检索基线。没有把 `pymilvus` 写入主依赖。覆盖文件不改镜像。没有执行 `docker compose up` 启动 `app` 或 `db`。

1. 解释器安装 `pymilvus==2.4.7`。该版本 import 还需要 `setuptools<81` 与 `marshmallow<4`，只装在当前解释器。
2. 新建 `scripts/milvus_five_gates.py`。应用不导入它。
3. `.gitignore` 忽略 `docker-compose.gate.local.yml`。该文件只给 `milvus` 加上 `127.0.0.1:19530:19530`。
4. `docker compose -p aigates -f docker-compose.yml -f docker-compose.gate.local.yml up -d milvus`，健康检查为 healthy。编排解析用当次临时 env 提供 JWT 与模型密钥，值不写入仓库和说明。
5. 单独库 `data/milvus_gate_check.db` 上运行核对脚本。嵌入为 `text-embedding-v3`，不是 Mock。TCP `127.0.0.1:19530` 已通。`MilvusVectorStore._connect` 在 `create_index` 失败：`GrpcHandler.create_index() got multiple values for argument 'params'`。脚本退出码 3，`blocked=version_mismatch`。
6. 脚本已把进程 `RAG_VECTOR_STORE` 设回 `local`。随后停止 `aigates-milvus-1`，`docker cp` 到 `data/gate-backup/milvus`（18 个文件，128164793 字节），再 `down -v`。没有在容器运行中拷贝。

`pytest tests/test_vectorstore_policy.py -v --tb=short`：1 passed，退出码 0。`settings.RAG_VECTOR_STORE` 打印为 `local`。

## 代码位置

- 核对脚本：`scripts/milvus_five_gates.py`
- 忽略规则：`.gitignore` 中的 `docker-compose.gate.local.yml`
- 核对记录：`docs/plans/record_milvus_five_gates.md`
- 清点表：`docs/plans/record_delivery_16.md` 第 13 行仍为未完成，证据改为本次输出

## 没做的事

没有改 Milvus 适配或摄取来换通过。没有把镜像改成 2.4。没有把默认向量库改成 milvus。没有把 Milvus 标成 `Implemented`。没有把 skip 或 Mock 写成通过。五条没有全部通过，因此第 13 行不改为完成，本任务不标 `done`。
