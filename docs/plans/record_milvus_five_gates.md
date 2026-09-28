# Milvus 五条门槛核对

> 日期：2026-09-28
> 结论：未通过。默认向量库保持 `local`。Milvus 不标成 `Implemented`。
> 第 1 到第 4 条结果是「未执行」。第 5 条的失败报告和回滚说明写在本文。skip 不算通过。

## 本次环境

- 系统：Windows，Python 3.12.7（Anaconda，MSC v.1929 64 bit）。
- 工作目录：仓库根目录。
- 没有安装 `pymilvus`，没有把 `19530` 发布到宿主机，也没有改 `RAG_VECTOR_STORE` 的默认值。

已执行的检查：

```text
python -c "import pymilvus"
ModuleNotFoundError: No module named 'pymilvus'
exit 1

python -c "import socket; s=socket.socket(); s.settimeout(2)
try:
    s.connect(('127.0.0.1', 19530)); print('open')
except Exception as exc:
    print(type(exc).__name__ + ': ' + str(exc))
finally:
    s.close()"
TimeoutError: timed out
exit 0

python -c "from app.core.config import settings; print(settings.RAG_VECTOR_STORE)"
local
exit 0

python -m pytest tests/test_vectorstore_policy.py -v --tb=short
1 passed
exit 0
```

编排里的 `milvus` 服务没有 `ports`。`19530` 只出现在应用容器的 `MILVUS_URI: http://milvus:19530`（`docker-compose.yml`）。`requirements.txt` 里 `pymilvus==2.4.7` 是注释。`pyproject.toml` 把它放在可选依赖 `milvus`，不在默认 `dependencies`。镜像按 `requirements.txt` 安装，因此镜像里也没有这份依赖。`.env.example` 第 92 行是 `RAG_VECTOR_STORE=local`。`app/core/config.py` 的默认值同样是 `local`。

## 代码事实（写命令时用到）

本地向量库的 `add` 是空操作，分块已经在 `DocumentChunk` 里。Milvus 的 `add` 才会 `upsert` 到集合。当前摄取把向量写进 `DocumentChunk.embedding`，没有调用 `MilvusVectorStore.add`。检索在 `RAG_VECTOR_STORE=milvus` 时走集合的 `search`。因此只跑现有上传或 `ingest_text`，不能证明摄取后能在 Milvus 里命中。

接口上的删除是软删除，只写 `deleted_at`，不调用 `delete_by_document`。检索会滤掉已软删文档。只看检索接口不能证明向量已从集合里清掉。第 3 条要调用 `MilvusVectorStore.delete_by_document`，把返回条数和删除前的分块数对比，再按主键查询集合。

本记录不补 `add` 的调用，不改 `app/rag/vectorstore/milvus.py`。

## 准备执行的命令

下面整段只在同时满足这些条件时执行：已安装 `pymilvus==2.4.7`，进程能连上 Milvus，并且只在该进程里把 `RAG_VECTOR_STORE` 设为 `milvus`。使用单独的 SQLite 文件，不要用现有业务库。嵌入若解析为 Mock，结果只说明链路，不能写成检索质量或门槛通过。任一断言失败就停止，按第 5 条把该进程的 `RAG_VECTOR_STORE` 设回 `local`。

```powershell
$env:RAG_VECTOR_STORE = "milvus"
$env:MILVUS_URI = "http://127.0.0.1:19530"
$env:DATABASE_URL = "sqlite:///./data/milvus_gate_check.db"
$env:ENV = "development"
python -c "import pymilvus; print(pymilvus.__version__)"
```

然后在同一环境变量下运行一段异步脚本，顺序固定：

1. 用 `RAGService.ingest_text` 写入含唯一标记的文本，读出该文档的 `DocumentChunk`。对这批分块调用 `MilvusVectorStore.add`。再用 `RAGService.search` 查询该标记，命中的 `document_id` 必须是刚写入的文档。
2. 记下旧分块主键。用 `reindex_document_in_place` 换成另一段唯一标记，再对新分块调用 `add`。集合查询和 `search` 都不得再返回旧主键。
3. 记下当时的分块数。调用 `delete_by_document`，返回值必须等于该分块数。随后按 `document_id` 查询集合，结果必须为空。
4. 用另一个 `tenant_id` 建第二个 `RAGService`，检索第一个租户的标记。命中的 `document_id` 不得属于第一个租户。对照本地向量库的读语义：其它租户的文档不会命中。
5. 无论成败，在该进程去掉 `RAG_VECTOR_STORE` 或设回 `local`。不要修改 `app/core/config.py` 和 `.env.example`。

本次没有执行这段脚本。原因是 `import pymilvus` 失败，并且 `127.0.0.1:19530` 在 2 秒内没有连上。

## 逐条结果

| # | 门槛 | 结果 |
|---|---|---|
| 1 | 摄取后可以用查询命中刚写入的向量 | 未执行 |
| 2 | 重解析后，旧向量的标识不再被检索命中 | 未执行 |
| 3 | 删除后向量被清理，删除计数与删除前的分块数一致 | 未执行 |
| 4 | 其它租户的文档不会命中 | 未执行 |
| 5 | 失败报告，以及把 `RAG_VECTOR_STORE` 改回 `local` 的回滚说明 | 通过（只表示说明已写在本文） |

第 1 到第 4 条没有通过证据。总结论是未通过。第 5 条通过不把总结论改成通过。

ADR-0002 第 5 节还写了备份。本次没有做备份演练，不把备份记成通过。

## 回滚

失败时把进程环境变量 `RAG_VECTOR_STORE` 设回 `local`，或去掉该变量。默认值本来就是 `local`。不删除 Local 索引，不改 `evals/reports/rag-v0.1-baseline-20260919.json`。

## 默认配置

`app/core/config.py` 与 `.env.example` 里的 `RAG_VECTOR_STORE` 仍是 `local`。`tests/test_vectorstore_policy.py` 通过。
