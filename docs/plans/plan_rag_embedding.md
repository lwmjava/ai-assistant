# 信息嵌入（Embedding）节点打磨实现方案

## 1. 目标与边界

**目标**：把 embedding 从「可用」提升为「企业级可插拔、可治理、可观测」，覆盖模型生命周期管理、稳定性、成本三个维度。

**边界**：仅动 `embeddings` 层及其与 `app/rag/service.py`、`app/rag/import_jobs.py` 的衔接，不改向量存储检索逻辑本身（那是下一阶段）。

## 2. 现状问题清单

| # | 问题 | 位置 | 影响 |
|---|------|------|------|
| 1 | 无 Provider 注册机制，硬编码 mock/openai | `app/rag/embeddings/factory.py` | 开源可插拔性差 |
| 2 | 分块不记录嵌入模型/维度 | `app/models/rag.py` DocumentChunk | 换模型后旧向量静默失效 |
| 3 | 无「重嵌入」任务，reparse 去重会跳过重嵌入 | `app/rag/import_jobs.py` | 模型漂移无法治理 |
| 4 | `embed()` 裸调 httpx，无重试/限流 | `app/rag/embeddings/openai_compatible.py` | 抖动/限流导致摄取整体失败 |
| 5 | `_embed_and_store` 无回滚补偿 | `app/rag/service.py` | 产生半成品脏数据 |
| 6 | 无成本/延迟/错误率指标 | 全层 | 商业化成本失控 |

## 3. 方案设计

### 3.1 Provider Registry（可插拔，问题 #1）

将 `factory.py` 从硬编码改为「内建映射 + 外部注册」：

```python
# factory.py 新增
_registry: dict[str, Callable[[], EmbeddingProvider]] = {}

def register_embedding_provider(name: str, factory: Callable[[], EmbeddingProvider]) -> None:
    _registry[name.strip().lower()] = factory

def get_embedding_provider() -> EmbeddingProvider:
    if _override is not None:
        return _override
    provider = settings.EMBEDDING_PROVIDER.strip().lower()
    # 1) 先查外部注册（插件）
    if provider in _registry:
        return _registry[provider]()
    # 2) 内建映射：ollama 显式走 OpenAI 兼容
    if provider in {"openai", "ollama"}:
        return _build_openai_compatible()
    if provider == "mock":
        return MockEmbeddingProvider(dim=settings.EMBEDDING_DIM)
    # 3) 未配置 Key 的降级逻辑保持不变
    ...
```

要点：

- `ollama` 从「隐式落到 openai 分支」改为显式映射，澄清语义。
- 注册表用模块级 `dict` + 锁即可（当前场景低并发）。

### 3.2 嵌入元数据（模型漂移治理，问题 #2）

在 `DocumentChunk` 增加两个字段：

```python
# DocumentChunk 新增
embedding_model: str | None = Field(default=None)  # 生成该向量所用的模型名
embedding_dim: int | None = Field(default=None)    # 该向量实际维度
```

配套：

- `_embed_and_store` 写入时记录 `self._embedding.model` / `self._embedding.dim`。
- 在 `RAGService.search`（或 backend 检索前）做一致性校验：

```python
# service.py 新增
def _embedding_signature_matches(self) -> bool:
    """判断当前配置的嵌入签名与历史数据是否一致。"""
    current = (self._embedding.model, self._embedding.dim)
    stale = self.session.exec(
        select(DocumentChunk).where(
            DocumentChunk.tenant_id == self.tenant_id,
            (DocumentChunk.embedding_model != current[0])
            | (DocumentChunk.embedding_dim != current[1]),
        ).limit(1)
    ).first()
    if stale:
        logger.warning("检测到嵌入模型/维度不匹配，建议触发 REEMBED: current=%s", current)
    return stale is None
```

> 一致性键用 `model:dim` 二元组；不引入 `embedding_version`，避免过度设计。

### 3.3 Re-embed 任务（重嵌入治理，问题 #3）

复用现有 `ImportJob` 体系，新增任务类型，与 `REPARSE` 平行：

**模型层**（`app/models/rag.py`）：

```python
class ImportSourceType(StrEnum):
    FILE = "file"
    URL = "url"
    REPARSE = "reparse"
    REEMBED = "reembed"          # 新增

class ImportJob(...):
    reembed_document_id: str | None = Field(default=None, index=True)  # 新增
```

**服务层**（`app/rag/service.py`）新增方法：

```python
async def reembed_document(self, document_id: str) -> int:
    """仅对一个文档的现有分块重新生成向量并回填，不重新切分/解析。"""
    rows = self.session.exec(
        select(DocumentChunk).where(
            DocumentChunk.document_id == document_id,
            DocumentChunk.tenant_id == self.tenant_id,
        )
    ).all()
    await self._embed_and_store(rows)  # 会同时回填 model/dim 元数据
    return len(rows)
```

**任务执行层**（`app/rag/import_jobs.py`）在 `_process_job` 增加分支：

```python
if job.source_type == ImportSourceType.REEMBED.value:
    rag = RAGService(session, job.tenant_id)
    n = await rag.reembed_document(job.reembed_document_id)
    job.status = ImportJobStatus.SUCCESS.value
    return
```

关键差异：**re-embed 直接取 `DocumentChunk.content` 重新嵌入，跳过解析与切分**，避免 reparse 的 `content_hash` 去重短路。

**入口**：新增 `create_reembed_job(session, user, document_id)`；「全量重嵌入」通过外层脚本遍历文档逐个建任务实现（不引入新的全量任务类型，保持最小）。

### 3.4 稳定性工程（问题 #4、#5）

**重试 + 退避**（`app/rag/embeddings/openai_compatible.py`）：

```python
async def embed(self, texts: list[str]) -> list[list[float]]:
    last_exc = None
    for attempt in range(self.max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(...)
            resp.raise_for_status()
            data = resp.json()["data"]
            ...
            return vectors
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {429, 500, 502, 503, 504} and attempt < self.max_retries:
                await asyncio.sleep(self.retry_backoff * (2 ** attempt))
                last_exc = exc
                continue
            raise
    raise last_exc
```

- 新增配置：`EMBEDDING_MAX_RETRIES: int = 3`、`EMBEDDING_RETRY_BACKOFF: float = 1.0`。
- 只对瞬时错误重试，4xx 业务错误不重试。

**摄取补偿**（`app/rag/service.py` 的 `_persist_document` / `_embed_and_store`）：`_embed_and_store` 抛错时回滚分块写入，保证不产生「文档落库但 embedding 缺失」的半成品：

```python
try:
    await self._embed_and_store(chunk_rows)
except Exception:
    self.session.rollback()
    raise
```

> 抽离事务边界，使失败时 DB 回退到摄取前状态，`ImportJob` 会记录 FAILED 待重试。

### 3.5 可观测性（问题 #6）

轻量方案，不引入重量级监控库：

- 结构化日志：每次 `embed()` 记录 `model / batch_size / 耗时 / 状态`。
- 用 `time.monotonic` 计时 + `logger.info("embed", extra={...})`。
- 预留指标钩子（后续接 Prometheus 时在 provider 层加 counter），Phase 1 仅日志。

## 4. 数据迁移

采用项目现有「alembic + 幂等补齐」双通道：

1. 新增 alembic 版本 `add_embedding_metadata`：
   - 为 `rag_document_chunks` 加 `embedding_model` / `embedding_dim`；
   - 为 `rag_import_jobs` 加 `reembed_document_id`（加索引）。
2. 同步在 `app/core/migration.py` 的 `_ensure_rag_schema_columns` 中补齐对应 `ALTER TABLE ... ADD COLUMN` 与 `CREATE INDEX IF NOT EXISTS`，兼容历史 SQLite 开发库（参照现有 `parent_id/strategy` 的补齐写法）。

## 5. 实施步骤（拆 4 个 PR）

1. **PR1 — Provider Registry**：改造 factory + 显式 ollama 映射，补单测。
2. **PR2 — 嵌入元数据**：模型字段 + 迁移 + 写入记录 + 检索前校验，补单测。
3. **PR3 — Re-embed 任务**：枚举/字段/服务方法/任务分支/创建入口，补端到端测试。
4. **PR4 — 稳定性与可观测**：重试退避 + 摄取回滚 + 结构化日志 + 配置项。

每个 PR 独立可测、可单独回滚，避免一次性大改动。

## 6. 测试要点

- **Registry**：注册自定义 provider 后 `get_embedding_provider` 正确返回；`ollama` 落到 OpenAI 兼容实例。
- **元数据**：`_embed_and_store` 后 chunk 的 `embedding_model/dim` 正确；跨模型签名不一致时 `_embedding_signature_matches` 返回 False 且产生告警。
- **Re-embed**：`reembed_document` 只刷新向量、不改变 content/strategy；`content_hash` 不变也能生成新向量（区别于 reparse）。
- **重试**：mock 429/5xx 后按退避重试、4xx 不重试。
- **回滚**：embed 抛错后不残留无 embedding 的 chunk。

## 7. 风险与回滚

- **迁移兼容**：新增列均 `nullable` + 默认补齐，历史库零数据损失；回滚仅下掉对应 alembic 版本的 `downgrade`。
- **re-embed 成本**：全量重嵌入会消耗外部 API 配额，需在入口加「文档数/块数确认」与限速，避免误触发大规模调用。
- **回归**：改动聚焦 embeddings，检索路径（`hybrid_search`）逻辑不变，回归风险低。