# RAG-032 代码 Review 修复实施说明

任务映射：RAG-032原卡、ADR-0008；2026-10-08。Review 报告：`docs/reviews/2026-10-08-RAG-032索引身份代码Review.md`。

## 实际完成的行为

### H-R1 + M-R2：probe_retrieval_hits 跨线程 Session 不安全

**修复前**：`_probe_retrieval_hits(session, target)` 接收主线程 Session。当调用方有运行中的事件循环时（FastAPI 请求中调用激活），probe 被扔到 ThreadPoolExecutor 跑 `asyncio.run`，但传入的 session 是主线程的 Session——SQLAlchemy Session 不是线程安全的，存在连接竞争和 flush 可见性问题。

**修复后**：
- `_probe_retrieval_hits(target)` 不再接收主线程 Session，内部从 `app.core.database.engine` 新建独立 Session。
- **纯只读查询**：直接按 `target.id` 过滤分块，JOIN Document 做可见性过滤（`deleted_at IS NULL` 且 `is_current = True`），与 `LocalVectorStore.hybrid_search` 的候选集过滤一致。
- **numpy 余弦相似度自检**：把候选分块的向量加载为 L2 归一化矩阵，对前 `RETRIEVAL_PROBE_SAMPLE_SIZE` 条 sample 用其自身向量算余弦分数，看 sample 是否排进 top-k。
- **不写任何数据库状态**：不 flush、不 commit、不临时改 active/retired，因此不会与主事务的未提交写事务在 SQLite 上撞 `database is locked`。
- **不经过** `get_vector_store` / `resolve_read_index_for`：直接按 target.id 查询，不依赖 active 状态，解决了 M-R2 指出的「独立 Session 看不到未 commit 的切换」问题。
- `probe_retrieval_hits` 同步入口签名保持不变（仍接收 session 和 target），`activate_index` 调用处无需改动。

**业务语义等价性**：
- 有分块、文档在线、维度匹配 → hits ≥ 1 → 允许激活；
- 无分块 / 分块挂在离线文档上 / 维度漂移 → hits = 0 → 拒绝激活。

### M-R1：adopt_legacy_chunks 加 commit 参数

- 函数签名新增 `commit: bool = True` 关键字参数。
- 末尾 `session.commit()` 改为 `if commit: session.commit() else: session.flush()`，与 `activate_index` 的 commit 语义一致。
- `commit=False` 用于写入事务内部：登记随业务提交一起落库，避免中途提交半个事务。

### M-R4：health 异常分支 supported 降级

- `_embedding_index_view()` 的 view 初始化中 `"supported": True` 改为 `"supported": None`。
- except 分支显式 `view["supported"] = None`，避免索引查询异常时默认 True 误导运维。

## 文件及契约映射

| 文件 | 交付 |
|---|---|
| `app/rag/index_registry.py` | `_probe_retrieval_hits` 重写为独立 Session 只读 numpy 自检；`adopt_legacy_chunks` 新增 `commit` 参数 |
| `app/api/routes/health.py` | `_embedding_index_view` 的 `supported` 默认值改为 None，except 分支显式置 None |
| `tests/test_rag_032_index_identity.py` | 新增第 10 节 4 条补测：H-R1 事件循环 probe、M-R1 commit=False、M-R4 异常降级、M-R2 状态不泄漏 |

## 红绿和命令证据

统一解释器 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`。

| 实际命令 | 退出码 | 结果 |
|---|---:|---|
| `pytest tests/test_rag_032_index_identity.py -v` | 0 | 66 passed, 1 skipped in 7.63s |
| `ruff check app/rag/index_registry.py app/api/routes/health.py` | 0 | All checks passed |
| `mypy app/rag/index_registry.py app/api/routes/health.py` | 0 | Success: no issues found in 2 source files |

### 失败修复记录

首版 probe 修复在独立 Session 内临时写 active/retired 状态（flush 不 commit），导致 SQLite 报 `database is locked`——主 Session 的 `_apply_switch` 已 flush 持有写事务，独立 Session 的 flush 无法获取写锁。改为纯只读 numpy 自检后问题解决。

## 未做项与残余风险

- **Milvus 后端 probe**：当前 probe 直接查 SQLite 分块表并在内存算相似度，不经过 Milvus collection。这意味着 Milvus 路径的 probe 验证的是「SQLite 里的分块数据可读、维度匹配」，而非「Milvus collection 里的向量可检索」。Milvus 本身为 Partial（ADR-0002），真实 Milvus 检索验证留待 RAG-015。
- **不经过 vectorstore 的 BM25/RRF 融合**：probe 只做稠密向量自检，不验证 BM25 稀疏路和 RRF 融合。对于「激活前确认读路径通畅」的目标，稠密自检已能捕获维度不符、文档下线、空索引等关键故障。
- **M-R3（Milvus collection 缓存多实例一致性）**：按 Review 建议留待 RAG-015。
- **L-R1（身份构造 docstring）**：随文档维护，不阻塞本卡。
- **L-R2（_read_index_id 退化分支）**：测试桩保留，不构成生产绕过。

## 回滚方式

三个修改均为纯代码层修复，无数据库迁移、无配置变更、无外部依赖。如需回滚，还原 `app/rag/index_registry.py`、`app/api/routes/health.py` 和 `tests/test_rag_032_index_identity.py` 到修改前状态即可。
