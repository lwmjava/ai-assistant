# RAG 多技术栈后端接入方案

- 分支：`feat/rag-backend`（自 `main` 切出）
- 状态：**提交 1–3 已完成**（native / LangChain / LlamaIndex 三套骨架）
- 记录时间：2026-09-03
- 已确认默认（开工）：
  - 请求级覆盖：全局配置 + ingest/search **请求体可选字段**，不用 header
  - 配置值：仅 `native` / `langchain` / `llamaindex`，**不加 `auto`**
  - 切分对比：同一套向量库，切换切分策略需 **重摄取**（方案 B）；chunk 不按后端打多套标签
  - 本轮不接聊天附件解析器（文件→文本是另一轴）

---

## 1. 目标与范围

让 `app/rag/` 支持**自研（native）/ LangChain / LlamaIndex 三套实现并存**，通过配置或业务规则决定某次请求走哪套。

本轮范围：

1. 把 **LangChain 真正接进来跑通**（切分 + 嵌入接口 + 检索编排）
2. 把 **LlamaIndex 的接口位置、目录结构、工厂分支留好**，后续填实现不改架构

不在本轮范围：评测体系（MRR/NDCG/A/B）、重排、query 改写、多路召回。这些在后端分层稳定后再做，否则每换一套后端评测口径就要重来。

### 为什么三套共存

项目是开源项目，多技术栈并存本身就是目的之一——用于横向比较不同框架在同一批数据上的表现。同时企业实际业务中也确实存在同一项目内多机制并存的情况（历史包袱、团队分工、场景差异）。

---

## 2. 已确认的三项决策

| 决策项 | 选择 | 影响 |
|---|---|---|
| LangChain 接入深度 | **全链路一次接完**（切分 + 嵌入 + 检索编排） | 一步看到完整效果，但变动面大，需分提交隔离风险 |
| 向量存储 | **共用项目 `VectorStore`** | 三套共用一份向量，结果可直接横向比较，切换后端无需重建数据 |
| 评测基线 | **先接入，评测后续补** | 起步快，但接完无法量化判断效果差异，需接受"暂时凭观察" |

---

## 3. 现状基线（实地核查）

`app/rag/` 共 14 个文件 / 963 行。关键接口签名如下（写适配器时以此为准）：

```python
# app/rag/vectorstore/base.py
@dataclass
class ChunkResult:
    id: str
    content: str
    source: str | None
    document_id: str
    score: float

class VectorStore(ABC):
    async def add(self, chunks: list) -> None: ...
    async def hybrid_search(
        self,
        query_embedding: list[float],
        query_tokens: list[str],
        tenant_id: str,
        top_k: int,
        rrf_k: int = 60,          # ← 实现有，ABC 无，见 §4
    ) -> list[ChunkResult]: ...
    async def delete_by_document(self, document_id: str, tenant_id: str) -> int: ...
    async def count(self, tenant_id: str) -> int: ...

# app/rag/ingestion.py
def split_text(text: str, chunk_size: int = 500, chunk_overlap: int = 64) -> list[str]: ...

# app/rag/retriever.py
class HybridRetriever:
    def __init__(self, embedding_provider, vector_store, tenant_id,
                 top_k: int = 5, rrf_k: int = 60) -> None: ...
    async def retrieve(self, query: str, plan: str) -> str:
        """返回拼接好的上下文文本（不是 ChunkResult 列表）"""
```

`RAGService` 现状：

- `__init__` **已支持注入** `embedding_provider` / `vector_store` —— 加后端无需改其公开签名，Agent 的 Retriever 钩子零感知
- `service.py:24-26` **模块级直接 import** `tokenize` / `split_text` / `HybridRetriever` —— 这是策略路由的卡点，阶段 0 必须改成可注入
- 配置项仅 4 个：`RAG_CHUNK_SIZE` / `RAG_CHUNK_OVERLAP` / `RAG_TOP_K` / `RAG_HYBRID_RRF_K`

已有成熟约定可复用：**可选 extras + 工厂函数 + 函数内延迟导入**（`milvus` / `mcp` / `workflow` 全是这个套路）。LangChain 沿用即可，不装依赖时零影响。

---

## 4. 核查中发现的一处接口不一致（顺手对齐）

`VectorStore.hybrid_search` 的**抽象与实现签名不同步**：

| 位置 | 签名 |
|---|---|
| `base.py`（ABC） | `(query_embedding, query_tokens, tenant_id, top_k)` — **4 个，无 `rrf_k`** |
| `local.py:119` | `(..., top_k, rrf_k: int = 60)` — 5 个 |
| `milvus.py:180` | `(..., top_k, rrf_k: int = 60)` — 5 个 |

调用点 `retriever.py:37` 与 `service.py:117` 都传 5 个参数，因实现类有默认值兜底**当前不会崩**。

危害不在运行时，在于：新后端实现者照着 ABC 写 `hybrid_search` 会漏掉 `rrf_k`，届时调用点直接 `TypeError`。

处理：阶段 0 把 `rrf_k: int = 60` 补进 ABC 签名（纯文档性改动，无行为变化）。

---

## 5. 架构设计

### 5.1 分层

```
api/routes/rag.py
        │
        ▼
  RAGService ──────► RagBackend（策略层，新增）
   （编排：主库读写、       │
    租户隔离、审计）        ├── native.py       自研：split_text + hybrid_search
                          ├── langchain_backend.py
                          └── llamaindex_backend.py
                                   │
                                   ▼
                    共用底层：EmbeddingProvider + VectorStore（不动）
```

关键：**后端只负责「切分」与「检索」两个纯能力**，主库读写、租户隔离、审计埋点全部留在 `RAGService`。

### 5.2 目录结构

```
app/rag/backend/              新增
├── __init__.py               导出 RagBackend / get_rag_backend / BackendNotAvailableError
├── base.py                   RagBackend 抽象
├── native.py                 现有 split_text + hybrid_search 包装（默认后端）
├── langchain_backend.py      LangChain 实现，函数内延迟导入
├── llamaindex_backend.py     SentenceSplitter / MarkdownNodeParser + BasePydanticVectorStore 只读适配
└── factory.py                按配置/上下文路由 + ImportError 降级
```

**不动的文件**：`embeddings/`、`vectorstore/`、`ingestion.py`、`retriever.py`、`api/routes/rag.py`。
**仅微调**：`service.py`（委托给 backend、`tokenize` 改为可注入）、`core/config.py`、`.env.example`、`pyproject.toml`、`requirements.txt`、`README.md`。

### 5.3 关于 `HybridRetriever` 的归置

`HybridRetriever.retrieve()` 现在返回**拼接好的文本**，而 `RAGService.search()` 返回 `list[ChunkResult]`。两者粒度不同，抽象层必须选一个。

决定：**`RagBackend.retrieve()` 返回 `list[ChunkResult]`（结构化）**，理由——

- 结构化信息（score / document_id / source）是后续做重排、阈值过滤、去重的前提，返回字符串等于把这些能力全部锁死
- 文本拼接属于**呈现策略**，与用哪套后端无关

因此：

- `native` 后端直接调 `vector_store.hybrid_search(...)`，不再复用 `HybridRetriever.retrieve()`
- 文本拼接抽成共享函数 `format_context(chunks) -> str`
- `HybridRetriever` 保留，改为持有 backend 引用：`retrieve(query, plan)` = `backend.retrieve(...)` → `format_context(...)`，`plan` 拼接逻辑留在这一层

---

## 6. 核心取舍：读写分离

**LangChain 侧的 `VectorStore.add_texts()` 显式抛 `NotImplementedError`**，写入路径一律不交给框架。

理由不是保守，是数据一致性。`RAGService.ingest_text` 承担了：

- `Document` / `DocumentChunk` 主库写入
- `tenant_id` 隔离
- `chunk_count` 回写
- 级联删除与向量清理
- 审计埋点

若让 LangChain 走它自己的 `add_texts`，就绕开了主库：文档列表查不到、删除清不掉、三套后端还会产生三份不同的数据布局——**直接违背"做比较"的初衷**。

一句话概括：**读可以多样化，写必须单一。**

---

## 7. 接口定义

```python
class RagBackend(ABC):
    """RAG 后端策略：只暴露切分与检索两个纯能力。

    嵌入与存储由三套后端共用，由外部注入，后端不得自行创建。
    """

    name: str

    @abstractmethod
    async def split(self, text: str, *, chunk_size: int, overlap: int) -> list[str]:
        """切分为块文本列表。"""

    @abstractmethod
    async def retrieve(
        self, query: str, *, tenant_id: str, top_k: int
    ) -> list[ChunkResult]:
        """返回按相关度降序的分块，无结果返回空列表。"""
```

抽象面刻意做窄——**嵌入和落库必须三套共用**，这是整个设计里最关键的一条。若各用各的存储，向量空间不同、文档要重建，比较就无从谈起。

### 7.1 嵌入适配器

```python
class _EmbeddingAdapter(Embeddings):        # 项目 EmbeddingProvider → LangChain
    def __init__(self, provider: EmbeddingProvider) -> None:
        self._provider = provider

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._provider.embed(texts)

    async def aembed_query(self, text: str) -> list[float]:
        return (await self._provider.embed([text]))[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError("同步嵌入会阻塞事件循环，请使用 aembed_documents")

    def embed_query(self, text: str) -> list[float]:
        raise NotImplementedError("同步嵌入会阻塞事件循环，请使用 aembed_query")
```

### 7.2 向量库适配器

```python
class _VectorStoreAdapter(LCVectorStore):   # 项目 VectorStore → LangChain
    def add_texts(self, texts, metadatas=None, **kwargs):
        raise NotImplementedError("写入统一走 RAGService，避免绕过主库与租户隔离")

    async def _asimilarity_search_with_score(self, query, k, **kwargs):
        vec = (await self._emb.embed([query]))[0]
        hits = await self._store.hybrid_search(
            query_embedding=vec,
            query_tokens=self._tokenizer(query),
            tenant_id=self._tenant_id,
            top_k=k,
            rrf_k=self._rrf_k,
        )
        return [
            (Document(page_content=h.content,
                      metadata={"chunk_id": h.id, "document_id": h.document_id,
                                "source": h.source}),
             h.score)
            for h in hits
        ]
```

**同步方法一律抛 `NotImplementedError`，不用 `asyncio.run()` 兜底**——在 FastAPI 的事件循环里调 `asyncio.run()` 会直接抛 `RuntimeError`。这个坑很常见，提前堵死。

---

## 8. 依赖策略

### 8.1 不装 `langchain` 主包

```toml
[project.optional-dependencies]
langchain = [
    "langchain-core>=0.3,<0.4",
    "langchain-text-splitters>=0.3,<0.4",
]
llamaindex = [
    "llama-index-core>=0.12,<0.13",
]
```

LCEL 编排（`Runnable` / `|` 操作符）在 `langchain-core` 里就有，**不装 `langchain` 主包能省掉一大堆用不上的 integration 依赖**。

### 8.2 版本必须锁 minor

硬性要求。langchain 0.1 → 0.2 → 0.3 拆包变动很大（`langchain.schema` 已迁到 `langchain_core`）。**写 `>=0.3,<0.4`，绝不写 `>=0.3`**。

### 8.3 同步到 `requirements.txt`

按 AGENT.md §11 文档同步规则，`pyproject.toml` 与 `requirements.txt` 必须同步，新增 extras 不能只改一处。

---

## 9. 中文切分（最大的落地风险点）

`RecursiveCharacterTextSplitter` 默认 separators 是 `["\n\n", "\n", " ", ""]`，**中文没有空格，等于几乎不切分**，会产出整段超长块直接进入嵌入环节。必须显式传中文标点：

```python
separators=[
    "\n\n", "\n",
    "。", "！", "？", "；", "，", "、",
    ".", "!", "?", ";", ",", " ", "",
]
```

另外两条约束：

1. **第一阶段 `length_function` 保持 `len`（字符数）**，与现有 `RAG_CHUNK_SIZE=500` 对齐。若改成 token 计数，块会显著变小，与 native 就不可比了 —— 等有评测基线后再动。
2. LangChain 的 TextSplitter 是**同步 CPU 密集**操作，大文档要在 async 里用 `asyncio.to_thread()` 包一层，否则阻塞事件循环。

---

## 10. 五个必须避开的坑

| # | 坑 | 规避方式 |
|---|---|---|
| 1 | 中文切分失效（默认 separators 无中文标点） | 显式传中文 separators，见 §9 |
| 2 | 版本漂移（0.1/0.2/0.3 拆包变动大） | 锁 minor：`>=0.3,<0.4` |
| 3 | 同步接口阻塞事件循环 | 一律用 `asimilarity_search` / `aembed_*`；同步方法抛 `NotImplementedError` |
| 4 | LangSmith 遥测外发 | 显式设 `LANGCHAIN_TRACING_V2=false` |
| 5 | 依赖只改一处 | `pyproject.toml` + `requirements.txt` 同步（AGENT.md §11） |

补充一条本项目的：LlamaIndex 的数据单元是 `Node`（带 `node_id`/`embedding`）而非 `Document`，且 `VectorStoreIndex` 默认**自己管嵌入与存储**。要共用项目存储，得实现 `BasePydanticVectorStore` 再走 `VectorStoreIndex.from_vector_store()` —— 口子现在就按这个形态留。

---

## 11. 实施计划：三个提交

| # | 内容 | 新增依赖 | 门禁 |
|---|---|---|---|
| **1** | `RagBackend` 抽象 + native 包装 + factory + ABC 补 `rrf_k` + `service.py` 改为委托 / `tokenize` 可注入 + `format_context` 抽取 | **零** | 118 测试全绿、**行为零变化** |
| **2** | LangChain 全链路：中文切分器 + 两个适配器 + 检索编排 + `RAG_BACKEND` 配置 + 请求级覆盖 + 缺依赖降级 | langchain extras | 不装包时 118 全绿；装包后新测试通过 |
| **3** | LlamaIndex 骨架与 factory 分支 + extras 声明 | 仅声明，不装包 | 结构对称，不装包不报错；装包后 split/retrieve 测试通过 |

**提交 1 单独出来的原因**：在引入任何第三方依赖之前先锁死回归。它是纯重构——抽象层立住、行为不变，后面两步就都在安全网里做。

---

## 12. 验收清单

- [ ] 不装 langchain：默认走 native，**118 测试全绿**（硬门禁）
- [ ] 装了 langchain、配 `RAG_BACKEND=langchain`：新测试通过
- [ ] **不装 langchain 却配了 `RAG_BACKEND=langchain`**：降级到 native + 一条 warning，不崩
- [ ] 同一文档两种切分的块数 / 块长可对比输出
- [ ] 两套后端 `retrieve()` 返回结构完全一致（`list[ChunkResult]`）
- [ ] `add_texts()` 被调用时抛 `NotImplementedError` 且错误信息明确
- [ ] 开发中无 LangSmith 外发请求（`LANGCHAIN_TRACING_V2=false`）
- [ ] `pyproject.toml` 与 `requirements.txt` 均已同步
- [ ] README 配置项表补充 `RAG_BACKEND` / `RAG_LANGCHAIN_SPLITTER` / `RAG_LLAMAINDEX_SPLITTER`

---

## 13. LlamaIndex 口子的对称映射

提交 3 按此表留签名，后续填实现不改架构：

| 项目抽象 | native | LangChain | LlamaIndex |
|---|---|---|---|
| `split()` | `split_text` | `TextSplitter.split_text()` | `SentenceSplitter` / `MarkdownNodeParser` |
| `EmbeddingProvider` | `MockEmbedding` 等 | `langchain_core.embeddings.Embeddings` | `BaseEmbedding` |
| `VectorStore` | `LocalVectorStore` / `MilvusVectorStore` | `langchain_core.vectorstores.VectorStore` | `BasePydanticVectorStore` |
| `retrieve()` | `hybrid_search` | `VectorStoreRetriever`（骨架阶段委托 `hybrid_search`） | `hybrid_search`（骨架阶段；后续可换 `VectorIndexRetriever`） |

---

## 14. 未决事项（已拍板）

1. **请求级覆盖**：全局 `RAG_BACKEND` + 请求体可选字段（ingest / search）。不用 HTTP header。聊天附件本轮不接。
2. **配置值命名**：`native` / `langchain` / `llamaindex` 三选一，**不加 `auto`**。
3. **评测体系**：提交 2 完成后先输出同一文档 native vs LC 的块数/块长；MRR/NDCG 仍后置。
4. **共用向量 vs 切分**：写入切分随当前后端变化，检索共用 `hybrid_search`。切换后端比较切分效果时必须重摄取，不在同一张 chunk 表里并存两套块。
