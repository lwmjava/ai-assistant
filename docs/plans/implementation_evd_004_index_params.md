# 实现说明：修正 create_index 参数并重跑 Milvus 五条

> 日期：2026-09-29
> 任务：`EVD-004`
> 结果：五条通过。清点表第 13 行改为完成。`tasks.yaml` 中本任务标为 `done`。默认向量库仍是 `local`。Milvus 不标成 `Implemented`。

## 完成的行为

按 `docs/plans/plan_evd_004_index_params.md` 执行。可选依赖保持 `pymilvus==2.5.11`，镜像保持 `milvusdb/milvus:v2.5.11`。

1. `MilvusVectorStore._ensure_index` 把 `collection.create_index` 的关键字从 `params` 改为 `index_params`。
2. 实跑返回 `only metric type can be passed when use AutoIndex`。默认索引是 `AUTOINDEX`，因此 `nlist` 只在索引类型名包含 `IVF` 时写入。`AUTOINDEX` 只传 `index_type` 与 `metric_type`。
3. 核对脚本要求的客户端版本改为 `2.5.11`。
4. 独立项目 `aigates` 只启动 `milvus`。单独库 `data/milvus_gate_check.db` 上五条通过。嵌入是 `text-embedding-v3`，维度 1024，不是 Mock。
5. 停止容器后 `docker cp` 到 `data/gate-backup/milvus`（56 个文件，128417153 字节），再 `down -v`。

`pytest tests/test_vectorstore_policy.py -v --tb=short`：1 passed，退出码 0。`ruff check app/rag/vectorstore/milvus.py` 无该文件报错。当前解释器没有 `mypy` 可执行文件，类型检查未跑。

```text
scripts/milvus_five_gates.py --database-url sqlite:///./data/milvus_gate_check.db
{"pymilvus": "2.5.11"}
{"embedding_model": "text-embedding-v3", "embedding_dim": 1024, "embedding_mock": false}
GATE 1 ok
GATE 2 ok
GATE 4 ok
GATE 3 ok
ROLLBACK RAG_VECTOR_STORE=local
all_passed true
exit 0
```

第 2 条：旧主键 `1f90ed052c6e4ba3ad0357c53ea23a0c` 已不在集合中，也不在检索结果里。用旧标记检索时返回了另一个分块 id `867fbb8c9d384d3f9b4de7b2279c0438`。脚本只要求旧主键不再出现，该条按此通过。

## 代码位置

- 索引调用：`app/rag/vectorstore/milvus.py` 的 `_ensure_index`
- 核对脚本：`scripts/milvus_five_gates.py`
- 核对记录：`docs/plans/record_milvus_five_gates.md`
- 清点表：`docs/plans/record_delivery_16.md` 第 13 行

## 没做的事

没有把默认向量库改为 `milvus`。没有把 Milvus 标成 `Implemented`。没有把 `pymilvus` 放进主依赖。没有改已提交镜像，没有改冻结检索基线。这不是生产备份演练。
