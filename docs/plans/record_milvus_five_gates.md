# Milvus 五条门槛核对

> 日期：2026-09-29
> 结论：通过。客户端 `pymilvus==2.5.11`，服务端 `milvusdb/milvus:v2.5.11`。嵌入 `text-embedding-v3`，维度 1024，不是 Mock。默认向量库保持 `local`。Milvus 不标成 `Implemented`。

## 本次环境

- 系统：Windows。解释器 conda 环境 `ai-assistant`（`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`，Python 3.12.0）。
- Docker 引擎 28.5.1。独立 Compose 项目名 `aigates`。覆盖文件 `docker-compose.gate.local.yml` 只发布 `127.0.0.1:19530:19530`，不改 `image`。
- 客户端：`pymilvus==2.5.11`，写入可选依赖 `pyproject.toml` 与 `requirements.txt` 注释，不在主依赖里。
- 服务端：已提交镜像 `milvusdb/milvus:v2.5.11`。健康检查通过后 `127.0.0.1:19530` 可连接。
- 单独 SQLite：`data/milvus_gate_check.db`。进程内 `RAG_VECTOR_STORE=milvus`，结束后脚本打印 `ROLLBACK RAG_VECTOR_STORE=local`。

```text
scripts/milvus_five_gates.py --database-url sqlite:///./data/milvus_gate_check.db
{"pymilvus": "2.5.11"}
{"embedding_model": "text-embedding-v3", "embedding_dim": 1024, "embedding_mock": false}
GATE 1 ok
GATE 2 ok
GATE 4 ok
GATE 3 ok
ROLLBACK RAG_VECTOR_STORE=local
{"all_passed": true}
exit 0

pytest tests/test_vectorstore_policy.py -v --tb=short
1 passed
exit 0
```

同一次输出中的五条字段：

| # | 结果字段 |
|---|---|
| 1 | `result=pass`，`chunk_count=1`，`hit_document_ids` 含刚写入的 `document_id` |
| 2 | `result=pass`，`collection_old_ids=[]`，检索结果不含旧主键 `1f90ed052c6e4ba3ad0357c53ea23a0c`。旧标记检索返回了另一个分块 `867fbb8c9d384d3f9b4de7b2279c0438` |
| 3 | `result=pass`，`deleted=1`，`before=1`，`remaining=0` |
| 4 | `result=pass`，`other_hit_document_ids=[]` |
| 5 | `result=pass`。停止容器后拷贝卷，再 `down -v` |

## 逐条结果

| # | 门槛 | 结果 |
|---|---|---|
| 1 | 摄取后可以用查询命中刚写入的向量 | 通过 |
| 2 | 重解析后，旧向量的标识不再被检索命中 | 通过 |
| 3 | 删除后向量被清理，删除计数与删除前的分块数一致 | 通过 |
| 4 | 其它租户的文档不会命中 | 通过 |
| 5 | 失败报告，进程把 `RAG_VECTOR_STORE` 设回 `local`，停止 `aigates` 的 milvus 后拷贝卷到 `data/gate-backup/milvus`，再 `down -v` | 通过（核对卷备份，不是生产备份演练） |

备份在容器停止后用 `docker cp aigates-milvus-1:/var/lib/milvus data/gate-backup/milvus` 完成，随后 `docker compose -p aigates ... down -v`。拷贝 56 个文件、128417153 字节。`data/` 已被忽略。

## 回滚

脚本打印 `ROLLBACK RAG_VECTOR_STORE=local`。`tests/test_vectorstore_policy.py` 证明默认仍是 `local`。独立项目 `aigates` 的容器和卷已删除。没有改 `docker-compose.yml` 和冻结检索基线。

## 此前同日的失败

第一次用 `pymilvus==2.4.7` 时，`create_index` 在客户端参数名上失败，第 1 到第 4 条未执行。把客户端升到 `2.5.11` 后，同一关键字 `params` 仍失败。改为 `index_params` 后，服务端拒绝 `AUTOINDEX` 上的 `nlist`。去掉非 `IVF` 索引的 `nlist` 后，上面这次通过。
