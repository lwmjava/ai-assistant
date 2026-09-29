# Milvus 五条门槛核对

> 日期：2026-09-29
> 结论：未通过。本条记为 `blocked`。原因是 `pymilvus==2.4.7` 与已提交镜像 `milvusdb/milvus:v2.5.11` 调用失败。默认向量库保持 `local`。Milvus 不标成 `Implemented`。
> 第 1 到第 4 条结果是「未执行」。第 5 条写下失败报告、进程回滚，并在停止 `aigates` 容器后拷贝了核对卷。skip 与 Mock 都不算通过。本次嵌入不是 Mock。

## 本次环境

- 系统：Windows。解释器 conda 环境 `ai-assistant`（`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`，Python 3.12.0）。
- Docker 客户端与引擎 28.5.1。独立 Compose 项目名 `aigates`。覆盖文件 `docker-compose.gate.local.yml` 只发布 `127.0.0.1:19530:19530`，不改 `image`。
- 客户端：解释器内安装 `pymilvus==2.4.7`，未写入 `requirements.txt` / `pyproject.toml`。为让该版本能 import，解释器另装了 `setuptools<81` 与 `marshmallow<4`，同样不写入主依赖。
- 服务端：已提交镜像 `milvusdb/milvus:v2.5.11`。容器健康检查通过后，`127.0.0.1:19530` TCP 可连接。
- 单独 SQLite：`data/milvus_gate_check.db`。进程内 `RAG_VECTOR_STORE=milvus`，`MILVUS_URI=http://127.0.0.1:19530`。
- 嵌入：`text-embedding-v3`，维度 1024，`embedding_mock` 为 false。

```text
python -c "import pymilvus; print(pymilvus.__version__)"
2.4.7
exit 0

scripts/milvus_five_gates.py --database-url sqlite:///./data/milvus_gate_check.db
{"pymilvus": "2.4.7"}
{"embedding_model": "text-embedding-v3", "embedding_dim": 1024, "embedding_mock": false}
ROLLBACK RAG_VECTOR_STORE=local
{"blocked": "version_mismatch", "detail": "pymilvus==2.4.7 与 milvusdb/milvus:v2.5.11 调用失败：MilvusException: <MilvusException: (code=1, message=Unexpected error, message=<GrpcHandler.create_index() got multiple values for argument 'params'>)>"}
exit 3

python -c "from app.core.config import settings; print(settings.RAG_VECTOR_STORE)"
local
exit 0

pytest tests/test_vectorstore_policy.py -v --tb=short
1 passed
exit 0
```

TCP 连通之后，`MilvusVectorStore._connect` 在 `collection.create_index` 失败。失败点是 `pymilvus` 2.4.7 的 `GrpcHandler.create_index() got multiple values for argument 'params'`。没有改 `app/rag/vectorstore/milvus.py`，没有改镜像。按计划记为版本不匹配导致的 `blocked`，不记成第 1 到第 4 条业务断言失败。

## 代码事实

本地向量库的 `add` 是空操作。Milvus 的 `add` 才会 `upsert` 到集合。当前摄取把向量写进 `DocumentChunk.embedding`，没有调用 `MilvusVectorStore.add`。接口删除是软删除，不调用 `delete_by_document`。核对脚本在应用外显式调用这两项；本次在建索引阶段停止，没有执行到 add / 检索 / 删除。

应用不导入 `scripts/milvus_five_gates.py`。

## 逐条结果

| # | 门槛 | 结果 |
|---|---|---|
| 1 | 摄取后可以用查询命中刚写入的向量 | 未执行 |
| 2 | 重解析后，旧向量的标识不再被检索命中 | 未执行 |
| 3 | 删除后向量被清理，删除计数与删除前的分块数一致 | 未执行 |
| 4 | 其它租户的文档不会命中 | 未执行 |
| 5 | 失败报告，进程把 `RAG_VECTOR_STORE` 设回 `local`，停止 `aigates` 的 milvus 后拷贝卷到 `data/gate-backup/milvus`，再 `down -v` | 通过（说明与备份；不是生产备份演练） |

第 1 到第 4 条没有通过证据。总结论是未通过。第 5 条通过不把总结论改成通过。

备份在容器停止后用 `docker cp aigates-milvus-1:/var/lib/milvus data/gate-backup/milvus` 完成，随后 `docker compose -p aigates ... down -v`。拷贝约 18 个文件、128164793 字节。`data/` 已被忽略。这不是生产备份演练。

## 回滚

脚本在失败路径打印 `ROLLBACK RAG_VECTOR_STORE=local`。默认配置本来就是 `local`。独立项目 `aigates` 的容器和卷已删除。没有改 `app/core/config.py`、`.env.example`、`docker-compose.yml` 和冻结检索基线。

## 上次（2026-09-28）

当时解释器没有 `pymilvus`，`127.0.0.1:19530` 连接超时。第 1 到第 4 条也是未执行。没有做卷备份。
