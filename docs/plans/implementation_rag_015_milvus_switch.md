# RAG-015：Local / Milvus 切换验收实施记录

## 1. 范围与约束

本轮只新增切换验收设施和证据，不修改 `app/`。默认后端继续是 `local`；真实写操作必须显式执行：

```bash
python scripts/milvus_switch_check.py --apply --milvus-uri http://127.0.0.1:19530
```

不带 `--apply` 时脚本只打印计划，不创建数据库、集合或报告。脚本每次使用全新临时 SQLite 和唯一 Milvus 集合前缀，结束时只删除本次集合；不会读写 `.env`、生产数据库或冻结基线 `evals/reports/rag-v0.1-baseline-20260919.json`。2026-09-29 的 `scripts/milvus_five_gates.py` 未作为本次切换证据。

## 2. Milvus standalone 启动方式

目标镜像固定为 `milvusdb/milvus:v2.5.11`，与项目可选依赖 `pymilvus==2.5.11` 对齐。单容器使用内嵌 etcd 和本地对象存储，端口 `19530` 提供 SDK 连接、`9091` 提供健康检查：

```bash
docker run -d --name rag015-milvus \
  -p 19530:19530 -p 9091:9091 \
  --security-opt seccomp:unconfined \
  -e ETCD_USE_EMBED=true \
  -e ETCD_DATA_DIR=/var/lib/milvus/etcd \
  -e ETCD_CONFIG_PATH=/milvus/configs/embedEtcd.yaml \
  -e COMMON_STORAGETYPE=local \
  -v rag015-milvus-data:/var/lib/milvus \
  -v "$PWD/deploy/milvus/embedEtcd.yaml:/milvus/configs/embedEtcd.yaml:ro" \
  -v "$PWD/deploy/milvus/user.yaml:/milvus/configs/user.yaml:ro" \
  milvusdb/milvus:v2.5.11 milvus run standalone
```

`docker-compose.yml` 同步暴露 `${MILVUS_PORT:-19530}` 与 `${MILVUS_HEALTH_PORT:-9091}`，因此也可单独执行 `docker compose up -d milvus`。应用容器内仍连接 `http://milvus:19530`，宿主机验收连接 `http://127.0.0.1:19530`。

本机第一次、第二次及第三次执行 `docker info` 时 Docker Desktop Linux engine named pipe 均不存在；未等待超过五分钟，先完成脚本、单测和 local 实测。最终容器实测状态见第 4 节，以实际命令输出和 JSON 报告为准。

## 3. 验收脚本判定

`scripts/milvus_switch_check.py` 固定使用三份短文本和四个查询，`MockEmbeddingProvider(dim=64)` 仅用于可重复链路对照。

1. **默认 local**：通过 HTTP 上传接口写入三份语料，再通过 HTTP 检索；必须命中目标文档。并从 SQL 读取向量，核对非零向量 L2 范数为 1。
2. **切换 Milvus**：只修改当前进程的 `settings.RAG_VECTOR_STORE`，上传同一正文（文件名加后端前缀以避免重复上传去重），随后按 SQL 生成的 chunk id 直接查询 Milvus 集合。`DocumentChunk` 存在但集合无对应 id 时明确失败，不能用 SQL 行冒充 Milvus 写入证据。
3. **切回 local**：同时要求原 local SQL 分块仍存在、HTTP 检索状态不是 `no_hit`、原目标文档仍命中。这样可以区分“数据还在且能查到”和“数据丢失却被当成空知识库”。

自动 Milvus 写入判定完成后，脚本才允许显式调用 `MilvusVectorStore.add()` 补种同一批 SQL 分块，用于第 5 节的跨库观察。该补种动作在报告中标为 `counts_as_upload_write_proof=false`，不计作路径 2 通过。

## 4. 三条路径实测

证据文件：`evals/reports/rag-015-local-milvus-switch-20261007.json`。

| 路径 | 结果 | 证据摘要 |
|---|---|---|
| 默认 local 上传 → 检索 | 通过 | 上传 3 个文档、SQL 3 个分块，检索状态 `ok`，目标文档命中；Mock 向量 L2 范数为 1 |
| 切换 Milvus 后上传 → 直接查集合 | 阻塞（Docker 尚未就绪时的当前报告） | TCP `127.0.0.1:19530` 被拒绝；没有伪造通过结果 |
| 切回 local | 通过 | SQL 仍有 3 个原 local 分块，检索状态 `ok`，目标文档仍命中，且 `data_vs_empty_distinguished=true` |

当前报告总结果为 `fail_or_blocked`，不能据此宣称 Milvus 切换已经通过。Docker 可用后须重跑同一 `--apply` 命令覆盖本次**非冻结**报告，并更新本节容器状态和路径 2 的真实结果。

## 5. 跨库检索差异

Docker 未就绪时跨库部分按 `blocked` 记录，没有生成虚假的 Milvus 命中。真实运行时报告会逐查询保存：

- `only_local` / `only_milvus`：逻辑语料命中集合差异；
- `local_order` / `milvus_order` 与 `rank_differences`：排序差异；
- `score_range`：RRF 结果分数范围；
- `similarity_range`：后端返回的稠密相似度范围。

比较使用正文 SHA-256 映射的 `logical_id`，不会把两次上传必然不同的数据库主键误报为命中集合差异。重点是保留差异，不把“一致”作为通过条件。

## 6. 本轮不能闭合的验收点

以下问题必须修改 `app/`，本轮因并行改动约束只记录、不修复：

1. **上传写链路未调用 `MilvusVectorStore.add()`**：`RAGService._persist_document()` 只提交 `DocumentChunk`，没有把向量写入外部集合。因此即使 Milvus 可连接，普通上传预计仍会出现 SQL 有分块、Milvus 集合无向量，路径 2 应真实失败。
2. **Milvus `similarity` 仍固定为 `1.0`**：`app/rag/vectorstore/milvus.py` 没有把 COSINE hit distance/score 转为真实相似度，跨库 `similarity_range` 会暴露为 `[1.0, 1.0]`，不能作为真实相似度通过证据。
3. **生产写入未显式执行通用 L2 归一化**：本次 Mock provider 自带归一化，所以样本范数校验能通过；但通用摄取路径保存 provider 原始向量，`EMBEDDING_NORMALIZATION=l2` 当前主要参与索引身份，不能证明任意生产 provider 都在写入前归一化。

后续修复需在同一事务/补偿语义下接入外部 `add()`，使用批准的 `COSINE` metric 返回值填充真实 `similarity`，并在写入边界统一 L2 归一化；随后重跑本脚本，要求三条路径全部 `pass`。

## 7. 假 Milvus 反例（证明脚本不会恒真）

Docker 不可用期间，最大的风险不是「跑不了 Milvus」，而是**脚本恒真**：无论 Milvus 侧发生什么都判通过。因此新增一组假 Milvus 反例：真实走 SQL、真实走融合与 HTTP 面，只把集合 IO 换成内存假库（或裸 TCP 监听），逐个场景断言脚本必须**非 0 退出且留下可诊断错误**。

```bash
python scripts/milvus_switch_check.py --apply --counterexample \
  --report evals/reports/rag-015-fake-milvus-counterexamples-20261007.json
```

8 个场景全部被捕获（`detected_count=8`、`undetected_scenarios=[]`、`result=all_detected`），且每个场景都撞在自己的判据上：

| 场景 | 假象 | 退出码 | 判据（节选） |
| --- | --- | --- | --- |
| `bare_listener_not_milvus` | 19530 有监听但不是 Milvus | 1 | `MilvusException: Fail connecting to server on 127.0.0.1:19531` |
| `collection_exists_but_zero_rows` | 集合存在但 0 行 | 1 | `Milvus 集合缺少上传产生的向量…；仅有 DocumentChunk 不能算 Milvus 写入通过` |
| `collection_rows_mismatch_dim` | 向量条数少于 SQL 分块 | 1 | 同上（`missing=[…]` 列出缺的 chunk id） |
| `collection_rows_match_but_dim_wrong` | 条数对、维度错（32 vs 64） | 1 | `Milvus 集合向量维度 [32] 与登记索引 dim=64 不一致` |
| `milvus_search_returns_zero_hits` | 有向量但检索零命中 | 1 | `已有 3 条已向量化分块，检索却零命中：不得把检索故障当成空知识库` |
| `dense_only_sparse_all_zero` | 只写稠密、BM25 词项没落库 | 1 | `没有任何分块带 BM25 词项：稀疏侧没有生效（稠密单路伪装成混合检索）` |
| `collection_has_stale_identity_vectors` | 集合里残留旧身份向量（5 vs 3） | 1 | `当前租户的向量条数 5 与 SQL 已向量化分块数 3 不一致：旧身份残留 / 清理失败` |
| `similarity_is_placeholder` | `similarity` 写死 1.0 | 1 | `Milvus 侧 similarity 恒为 1.0（3 条命中），仍为占位值` |

前两个「旧身份残留」与「稀疏侧为空」一开始是**漏检**的（脚本 exit 0），据此补了两条判据后才收敛：只按上传 id 查集合会漏掉集合里的多余向量（改为同时做租户级条数核对）；只看命中数看不出 BM25 有没有生效（改为核对 SQL 侧带词项的分块数）。

## 8. 自动化与变异验证

正常测试使用全新数据库与全新 basetemp：

```bash
DATABASE_URL=sqlite:///./data/test_rag015_fake_b2.db \
  python -m pytest tests/test_rag_015_milvus_switch.py \
  --basetemp=C:/Users/123/AppData/Local/Temp/rag015-fake-bt-b2 -q
```

结果：`20 passed`（7 条判据级用例 + 5 条新增判据用例 + 8 条假 Milvus 反例用例，后者每个都在独立子进程里跑真实脚本）。

变异均在验证后还原（`diff -u` 与变异前快照逐字节一致）：

1. 把“Milvus 必须包含全部 SQL chunk id”的缺失判断改成恒假，`test_milvus_presence_requires_rows_from_milvus_not_sql_only` 与 `test_milvus_presence_rejects_partial_collection_write` 共 **2 条变红**。
2. 把“local SQL 有数据时禁止 `no_hit`”判断改成恒假，`test_switch_back_rejects_silent_no_hit_when_local_data_exists` 共 **1 条变红**。
3. 把“集合条数必须与 SQL 分块数相等”放宽成 `observed < expected`（即多于预期也算通过），`collection_has_stale_identity_vectors` 场景脚本 **exit 0（漏检）**，对应用例 `test_stale_vectors_break_row_count_against_sql` 与 `test_fake_milvus_counterexample_must_not_pass[collection_has_stale_identity_vectors]` **2 条变红**。
4. 把“稀疏侧为空”判断改成恒假，`dense_only_sparse_all_zero` 场景脚本 **exit 0（漏检）**，对应用例 `test_sparse_side_all_zero_is_reported_as_fake_hybrid` 与 `test_fake_milvus_counterexample_must_not_pass[dense_only_sparse_all_zero]` **2 条变红**。

恢复后再次运行全套单测和 Ruff，必须保持全绿。
