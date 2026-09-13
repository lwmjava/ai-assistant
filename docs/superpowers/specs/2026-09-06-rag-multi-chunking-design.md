# RAG 多模式文档切分设计

## 1. 背景

当前 RAG 的文档切分只有一条硬编码路径：

- 核心是 `app/rag/ingestion.py` 的 `split_text`（句子切分 → 字符窗口 → 字符重叠 → 超长句硬切）。
- 近期补充了 `split_text_structured`（识别 Markdown 标题、保留章节前缀）。
- 三套后端 `native / langchain / llamaindex` 各自实现 `split()`，没有统一的切分策略抽象。
- `DocumentChunk` 是扁平结构，无父子关系、无 token 感知、无语义边界、无元数据溯源。

在企业知识库场景下，单一「字符窗口硬切」会带来明显问题：

- 英文 / 混合文档按字符切会导致实际 token 超限。
- 跨句、跨段、跨章节的硬切会破坏语义完整。
- 结构化文档（Markdown / 标题 / 段落）层级信息丢失。
- 检索命中后难以溯源到原文位置。
- 无法针对不同文档类型选择更合适的切分策略。

因此需要把「切分」升级为**可插拔的多模式策略层**，并支持父子文档与元数据溯源，以适配企业生产的复杂场景。

## 2. 目标与非目标

### 2.1 目标

本期需要完成：

1. 新建独立的**切分策略层** `app/rag/chunking/`，与现有解析层架构对齐。
2. 支持多种切分模式：
   - 段落切分
   - 滑动窗口切分
   - token 感知切分
   - 语义切分（复用现有 embedding）
   - 父子文档切分
   - 结构切分（迁移现有 Markdown 逻辑）
3. 切分结果从「纯文本块」升级为「带元数据的 `Chunk`」，支持父子关联与溯源。
4. 提供统一的模式选择机制（请求级 > 配置级 > auto 路由）。
5. `native` 后端通过统一接口调用切分策略层；`langchain / llamaindex` 保持自有 splitter，必要时通过适配器复用。
6. 父子文档：父块 = 子块的倍数粗切分块，子块 = 策略切出的细粒度小块；检索命中子块后返回父块上下文。
7. 保持现有 `RAG_CHUNK_SIZE` / `RAG_CHUNK_OVERLAP` 等配置对默认策略的兼容。

### 2.2 非目标

本期明确不做：

1. 引入专用外部语义分段模型 / API（语义切分复用现有 embedding，不新增外部依赖）。
2. 图表 / 图片内部结构的语义切分。
3. 检索后的重排序（rerank）机制。
4. 按租户动态绑定不同切分策略。
5. 分布式 / 独立切分服务（本期作为进程内模块，不拆服务）。

## 3. 关键决策

以下决策已在设计确认阶段锁定：

| 决策点 | 结论 |
|---|---|
| 架构定位 | 独立切分策略层，与解析层架构一致 |
| 父子文档粒度 | 父 = 子块倍数粗块（默认 2~4 倍），子 = 策略切出的细块 |
| 语义切分实现 | 复用现有 embedding provider 计算句子 / 块间相似度 |
| tokenizer 来源 | 优先 `tiktoken`（OpenAI 兼容），缺失时回退估算 |
| 父子检索返回 | 返回父块（粗块）文本 + 命中子块标记，命中去重 |

## 4. 架构设计

### 4.1 模块划分

新增目录：

```
app/rag/chunking/
├── __init__.py
├── base.py               # ChunkingStrategy 抽象 + Chunk 结果模型 + ChunkParams
├── registry.py           # 策略注册表（按 name 路由）
├── factory.py            # 按配置 / 请求返回策略
└── strategies/
    ├── __init__.py
    ├── paragraph.py      # 段落切分
    ├── sliding_window.py # 滑动窗口
    ├── token_aware.py    # token 感知
    ├── semantic.py       # 语义切分（embedding 相似度）
    ├── parent_child.py   # 父子文档
    └── structured.py     # 结构切分（迁移现有 Markdown 逻辑）
```

修改文件：

- `app/rag/backend/native.py`（改为委托切分策略层）
- `app/rag/service.py`（`ingest_text` 接入切分策略层与模式选择）
- `app/models/rag.py`（`DocumentChunk` 增加 `parent_id` / `strategy` / `metadata`）
- `alembic/versions/`（新增迁移）
- `app/core/config.py`（新增切分策略配置项）

### 4.2 统一接口

```python
@dataclass
class Chunk:
    text: str
    index: int
    parent_id: str | None = None
    metadata: dict = field(default_factory=dict)

@dataclass
class ChunkParams:
    chunk_size: int = 500
    chunk_overlap: int = 64
    # token 感知 / 语义切分 / 滑动窗口等专用参数可在此扩展

class ChunkingStrategy(ABC):
    name: str

    @abstractmethod
    async def split(self, text: str, *, params: ChunkParams) -> list[Chunk]:
        ...
```

要点：

- 切分结果统一为 `Chunk`（带 `parent_id` 与 `metadata`），为父子文档与溯源打基础。
- 策略不感知数据库 / 向量库，只负责「文本 → 块」，保持纯能力，便于独立测试。

### 4.3 registry / factory

- `registry`：`strategy name -> ChunkingStrategy` 的注册表。
- `factory`：按三级优先级解析出策略：
  1. 请求级 `strategy` 参数
  2. 配置级 `RAG_CHUNK_STRATEGY`
  3. `auto` 路由（按文档特征选择）

### 4.4 与现有 backend 的衔接

- `NativeRagBackend.split` 改为调用切分策略层的默认策略（保持 `chunk_size / overlap` 入参不变，内部映射到 `ChunkParams`）。
- `RAGService.ingest_text` 在拿到 `list[Chunk]` 后，负责把 `Chunk` 落为 `DocumentChunk`，并写入 `parent_id / strategy / metadata`。
- `langchain / llamaindex` 后端的 `split` 保持现状，不强制迁移，后续可按需通过适配器复用策略。

## 5. 各模式算法定义

### 5.1 段落切分（paragraph）

- 按空行 / 段落边界切分。
- 单个段落超过 `chunk_size` 时，段落内部再按句子边界切分。

### 5.2 滑动窗口（sliding_window）

- 固定 `window_size` 窗口 + `step` 步长。
- 不依赖句子边界，`step < window_size` 时产生重叠。
- 适合连续、无明确段落结构的文本。

### 5.3 token 感知（token_aware）

- 使用 tokenizer 估算 token 数，按 `max_tokens` 切分，重叠按 `overlap_tokens` 计。
- tokenizer 优先 `tiktoken`；缺失时回退估算：中文 1 字符 ≈ 1 token，英文 ≈ 4 字符 / token。
- 适合英文 / 多语言混合文档。

### 5.4 语义切分（semantic）

- 先把文本切成候选句 / 段。
- 用现有 embedding provider 计算相邻候选的向量相似度。
- 相似度低于 `similarity_threshold` 处断开为语义边界。
- 不新增外部模型依赖，复用现有 embedding。

### 5.5 父子文档（parent_child）

- 父块 = 子块的 N 倍粗切分块（N 默认 2~4，可配 `parent_ratio`）。
- 子块 = 由 `child_strategy`（默认段落 / 结构化切分）切出的细粒度小块。
- 父块由 `parent_strategy`（默认滑动窗口）按 `parent_ratio` 放大窗口切出。
- 每个子块记录 `parent_id` 指向所属父块。

### 5.6 结构切分（structured）

- 迁移现有 `split_text_structured` 的 Markdown 标题分节逻辑。
- 识别 `#{1,6}` 标题，节内复用段落 / 句子切分，子块保留标题前缀。

## 6. 调用方式与模式选择

### 6.1 统一调用入口

切分统一由 `RAGService.ingest_text` 触发，策略通过 `strategy` 参数指定：

```python
async def ingest_text(
    self,
    text: str,
    title: str,
    source: str | None,
    user_id: str,
    *,
    strategy: str | None = None,       # 切分策略名，覆盖全局配置
    chunk_params: dict | None = None,  # 策略专用参数（window_size / threshold / parent_ratio 等）
    ...
) -> Document:
```

### 6.2 三级优先级

1. **请求级**：`strategy` 参数，单次调用最高优先级。
2. **配置级**：`RAG_CHUNK_STRATEGY`，全局默认。
3. **auto 路由**：配置为 `auto` 时按文档特征自动选择。

### 6.3 各模式与参数

| 模式 | `strategy` 名 | 可传 `chunk_params` |
|---|---|---|
| 段落切分 | `paragraph` | `chunk_size`, `chunk_overlap` |
| 滑动窗口 | `sliding_window` | `window_size`, `step` |
| token 感知 | `token_aware` | `max_tokens`, `overlap_tokens` |
| 语义切分 | `semantic` | `similarity_threshold` |
| 父子文档 | `parent_child` | `parent_strategy`, `parent_ratio`, `child_strategy`, `child_params` |
| 结构切分 | `structured` | `chunk_size`, `chunk_overlap` |

### 6.4 复合策略（父子文档）嵌套调用

`parent_child` 是复合策略，内部嵌套调用两个子策略：

```python
chunk_params = {
    "parent_strategy": "sliding_window",  # 父块用粗切分
    "parent_ratio": 3,                    # 父块 = 子块大小的 3 倍
    "child_strategy": "paragraph",        # 子块用细切分
    "child_params": {"chunk_size": 300},  # 子块参数
}
```

内部流程：

1. 用 `child_strategy` 切出子块（细粒度）。
2. 用 `parent_strategy` + `parent_ratio` 切出父块（粗粒度，约为子块的 N 倍）。
3. 建立子块 → 父块的 `parent_id` 映射，写入 `Chunk.parent_id`。

### 6.5 auto 路由规则

当 `RAG_CHUNK_STRATEGY=auto` 时，按文档特征选择：

- 含 Markdown 标题 → `structured`
- 纯连续长文 → `sliding_window`
- 英文 / 多语言混合 → `token_aware`
- 其他 → `paragraph`

## 7. 存储 schema 变更

`DocumentChunk` 新增字段：

- `parent_id: str | None`：指向父块（父子文档）。
- `strategy: str | None`：记录本次切分使用的策略名。
- `metadata: str | None`（JSON）：章节 / 位置 / 溯源等扩展信息。

需要新增 Alembic 迁移。

## 8. 检索配合（父子文档）

- 检索默认命中**子块**（`parent_id is not None` 的块）。
- 命中后 retriever 按 `parent_id` 拉取**父块（粗粒度块）**。
- 多个子块命中同一父块时去重，避免同一父块重复注入。
- 返回上下文 = 父块（粗块）文本 + 命中子块标记（用于展示 / 高亮）。

## 9. 错误语义

- 空文本 / 无法切分：沿用 `ValueError("文本为空或无法切分为任何分块")`。
- 语义切分时 embedding 不可用 / 失败：复用现有 embedding 错误语义，并写入导入任务 trace。
- 未知策略名：抛出明确的「不支持的切分策略」错误，并给出可用策略列表。

## 10. 测试策略

### 10.1 策略单测

每个策略独立覆盖：

1. 段落切分：按段落边界切分、超长段回退句子切分。
2. 滑动窗口：窗口 / 步长 / 重叠正确。
3. token 感知：token 上限生效、缺失 tiktoken 时回退估算。
4. 语义切分：相似度阈值处断开、空结果语义。
5. 父子文档：父块 / 子块关系、`parent_id` 正确。
6. 结构切分：迁移后与原 `split_text_structured` 结果一致。

### 10.2 registry / factory 测试

1. 按 name 路由到正确策略。
2. 请求级 > 配置级 > auto 的优先级。
3. 未知策略名报错。

### 10.3 摄取回归

1. `ingest_text` 能消费 `list[Chunk]` 并正确写入 `parent_id / strategy / metadata`。
2. 默认策略下行为与改造前一致（向后兼容）。

### 10.4 检索回归（父子文档）

1. 命中子块时返回对应父块。
2. 多个子块命中同一父块时去重。

## 11. 落地顺序

1. `ChunkingStrategy` 抽象 + `Chunk` / `ChunkParams` + registry / factory（先搭骨架）。
2. 段落切分、滑动窗口、token 感知（规则型，低风险）。
3. 结构切分迁移到策略层，保持与现有 `split_text_structured` 等价。
4. 语义切分（复用 embedding）。
5. 父子文档 + `DocumentChunk` 迁移 + 检索配合。
6. `ingest_text` 接入切分策略层与模式选择。
7. 全量回归 + README / `.env.example` 配置同步。

## 12. 风险与缓解

### 风险 1：改造影响现有摄取行为

缓解：

- 默认策略保持与当前行为一致。
- 每个策略先补回归测试再替换。

### 风险 2：语义切分成本偏高

缓解：

- 复用现有 embedding，不新增外部依赖。
- 先对候选句做粗切分，再计算相邻相似度，控制 embedding 调用次数。

### 风险 3：父子文档的检索返回体积增大

缓解：

- 父块命中去重，仅返回一次。
- 后续可加父块截断 / 摘要策略，本期先保证正确性。

### 风险 4：tokenizer 缺失导致 token 感知不准

缓解：

- 优先 `tiktoken`，缺失时回退估算。
- 单测覆盖回退分支。

## 13. 完成定义

满足以下条件即视为多模式切分子项目完成：

1. `app/rag/chunking/` 策略层存在，六种模式可插拔。
2. `ingest_text` 消费 `list[Chunk]`，并写入 `parent_id / strategy / metadata`。
3. 模式选择机制（请求级 > 配置级 > auto）可用。
4. 父子文档检索能返回父块（粗块）文本并去重。
5. 默认策略下现有摄取行为不回归。
6. 相关自动化测试通过。
7. README / `.env.example` 补充切分策略配置说明。
