# EVD-004 续：修正 create_index 参数名并重跑五条

> 日期：2026-09-29
> 任务：`EVD-004`
> 批准：在上一轮核对记为 `blocked` 之后，把可选客户端改为 `pymilvus==2.5.11` 仍复现同一异常。已确认异常来自 `Collection.create_index` 的关键字 `params` 与形参 `index_params` 撞名。按该结论执行本文件。本文件只放行这一处调用修正，并据此重跑五条。

## 目标

让 `MilvusVectorStore._ensure_index` 按 `pymilvus` 2.5.11 的形参名传入索引参数，然后在已提交镜像 `milvusdb/milvus:v2.5.11` 上重跑五条门槛。

## 现状

- `pyproject.toml` 可选依赖与 `requirements.txt` 注释已是 `pymilvus==2.5.11`。解释器已安装该版本。
- 用关键字 `params=` 调用时，2.5.11 仍抛 `GrpcHandler.create_index() got multiple values for argument 'params'`。
- 用 `index_params` 或第二个位置参数传入时，该撞名消失。
- `scripts/milvus_five_gates.py` 在版本不是 `2.4.7` 时直接 `blocked`，因此现在的 2.5.11 解释器到不了 `create_index`。
- 默认 `RAG_VECTOR_STORE` 仍是 `local`。镜像仍是 `milvusdb/milvus:v2.5.11`。

## 方案

1. `app/rag/vectorstore/milvus.py` 的 `collection.create_index` 把关键字 `params` 改为 `index_params`。字典内容不改。
2. 核对脚本要求的客户端版本改为 `2.5.11`。失败说明里的版本号与实际安装版本一致。
3. 用独立项目 `aigates` 只启动 `milvus`，健康后在 `data/milvus_gate_check.db` 上运行 `scripts/milvus_five_gates.py`。
4. 停止容器后把卷拷到 `data/gate-backup/milvus`，再 `down -v`。
5. 五条全部通过且备份存在时，清点表第 13 行改为完成，本任务标 `done`。否则第 13 行保持未完成，本任务不标 `done`。

## 非目标

不把 `pymilvus` 放进主依赖。不改已提交编排，不在覆盖文件里改镜像。不把默认向量库改为 `milvus`。不把 Milvus 标成 `Implemented`。不改冻结检索基线。

## 实跑后的索引参数

2026-09-29 第一次重跑已越过参数名撞车，服务端返回 `only metric type can be passed when use AutoIndex`。默认 `MILVUS_INDEX_TYPE` 是 `AUTOINDEX`，索引参数里的 `nlist` 只留给索引类型名包含 `IVF` 的情况。`AUTOINDEX` 只传 `index_type` 与 `metric_type`。

## 验收

五条都有本次输出。全部通过且停止容器后的备份存在时，`tests/test_vectorstore_policy.py` 仍证明默认是 `local`。任一未执行或失败时，第 13 行保持未完成。
