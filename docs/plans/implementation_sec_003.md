# 实现说明：Milvus 五条门槛核对

## 完成的行为

按 ADR-0002 第 5 节写下五条可重复命令和本次结果。第 1 到第 4 条是「未执行」：当前解释器没有 `pymilvus`，`127.0.0.1:19530` 在 2 秒内连接超时。第 5 条写下了失败原因和回滚：进程里的 `RAG_VECTOR_STORE` 设回 `local` 即可，配置默认值本来就是 `local`。

总结论是未通过。默认向量库保持 `local`。没有把 Milvus 标成 `Implemented`。没有把 skip 写成通过。

核对时确认：现有摄取不会调用 `MilvusVectorStore.add`，接口删除是软删除，不调用 `delete_by_document`。因此以后不能只用现有上传或删除接口宣称这五条通过。准备执行的命令要求摄取后显式 `add`，删除核对使用 `delete_by_document` 的返回条数。本说明不补这些调用。

## 代码位置

- 核对记录：`docs/plans/record_milvus_five_gates.md`。
- 没有修改 `app/core/config.py`、`.env.example`、`docker-compose.yml`、`app/rag/vectorstore/milvus.py` 和已冻结检索基线。

## 验证

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

## 每条结论

| # | 结果 |
|---|---|
| 1 摄取后可检索 | 未执行 |
| 2 重解析后旧向量不可检索 | 未执行 |
| 3 删除后向量清理且计数一致 | 未执行 |
| 4 其它租户不命中 | 未执行 |
| 5 失败报告与回滚说明 | 通过（只表示说明已写入） |

总结论：未通过。默认仍是 `local`。

## 没做的事

没有安装 `pymilvus`，没有把 Milvus 端口发布到宿主机，没有改默认向量库，没有改 Milvus 适配，没有做备份演练。SEC-004 未实现。
