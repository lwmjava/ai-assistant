# RAG 高阶分块模式扩展 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有多模式切分基础上，继续扩展固定字符数、递归、格式感知、版式感知、命题分块五类模式，并为结构化/版式化文档建立可持续演进的解析结果模型。

**Architecture:** 先在现有 `app/rag/chunking/` 纯文本策略层内补齐 `fixed_chars` 与 `recursive` 两种低成本高收益能力；再升级 `ParsedDocument` 为“纯文本 + 结构块/版式块”的双通道模型，让 `format_aware` 与 `layout_aware` 直接消费解析层保留下来的结构信息；最后以现有 `semantic` 为基础演进 `proposition`，先做轻量规则 + embedding 版，避免过早引入高成本 LLM 依赖。

**Tech Stack:** Python 3.12、FastAPI、SQLModel、pytest、现有 `EmbeddingProvider` 抽象、现有 `app.rag.document_parsers` 解析层、可选 `tiktoken`。

---

## 文件结构映射

**新增：**

- `app/rag/chunking/strategies/fixed_chars.py` — 固定字符数切分
- `app/rag/chunking/strategies/recursive.py` — 递归切分
- `app/rag/chunking/strategies/format_aware.py` — 基于结构块的格式感知切分
- `app/rag/chunking/strategies/layout_aware.py` — 基于版面块的版式感知切分
- `app/rag/chunking/strategies/proposition.py` — 命题分块
- `tests/test_document_parser_blocks.py` — 结构块/版式块解析测试

**修改：**

- `app/rag/chunking/base.py` — 补充策略参数，必要时引入结构块输入协议
- `app/rag/chunking/factory.py` — 注册新策略并更新 `auto` 路由规则
- `app/rag/chunking/__init__.py` / `strategies/__init__.py` — 导出新策略
- `app/rag/document_parsers/base.py` — 为 `ParsedDocument` 增加 `blocks`，定义结构块/版式块模型
- `app/rag/document_parsers/service.py` — 保留并透传 `blocks`
- `app/rag/document_parsers/pdf.py` — 输出页码/版面块（可先做最小版）
- `app/rag/document_parsers/office.py` — 输出 `pptx/docx/xlsx` 结构块
- `app/rag/service.py` — 支持摄取 `ParsedDocument` 或统一内部入口，给格式/版式策略传递结构输入
- `tests/test_chunking.py` — 新策略单测
- `tests/test_rag.py` — 摄取与检索回归
- `app/core/config.py` / `.env.example` / `README.md` — 策略配置与说明

---

## 实施顺序

1. **`fixed_chars`**：补齐最稳定的 baseline 与 debug 模式
2. **`recursive`**：补齐生产通用主策略
3. **解析结果模型升级**：让 `ParsedDocument` 保留结构/版式块
4. **`format_aware`**：消费结构块，真正按文档逻辑结构切分
5. **`layout_aware`**：消费版面块，处理 PDF/OCR/双栏/表格页
6. **`proposition`**：在 `semantic` 之上做主题/命题边界增强

---

## Task 1: 固定字符数分块（`fixed_chars`）

**Files:**
- Create: `app/rag/chunking/strategies/fixed_chars.py`
- Modify: `app/rag/chunking/factory.py`
- Modify: `app/rag/chunking/strategies/__init__.py`
- Test: `tests/test_chunking.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_chunking.py` 追加：

```python
async def test_fixed_chars_strategy_splits_by_length() -> None:
    from app.rag.chunking.base import ChunkParams
    from app.rag.chunking.strategies.fixed_chars import FixedCharsChunkingStrategy

    chunks = await FixedCharsChunkingStrategy().split(
        "0123456789",
        params=ChunkParams(chunk_size=4, chunk_overlap=0),
    )
    assert [c.text for c in chunks] == ["0123", "4567", "89"]


async def test_fixed_chars_strategy_supports_overlap() -> None:
    from app.rag.chunking.base import ChunkParams
    from app.rag.chunking.strategies.fixed_chars import FixedCharsChunkingStrategy

    chunks = await FixedCharsChunkingStrategy().split(
        "0123456789",
        params=ChunkParams(chunk_size=4, chunk_overlap=1),
    )
    assert [c.text for c in chunks] == ["0123", "3456", "6789"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_chunking.py::test_fixed_chars_strategy_splits_by_length tests/test_chunking.py::test_fixed_chars_strategy_supports_overlap -q`

Expected: FAIL，`FixedCharsChunkingStrategy` 尚不存在。

- [ ] **Step 3: 写最小实现**

实现 `FixedCharsChunkingStrategy`：

- `name = "fixed_chars"`
- 按 `chunk_size` 固定窗口切分
- `chunk_overlap > 0` 时步长为 `chunk_size - chunk_overlap`
- 返回 `Chunk(text=..., index=...)`
- 空文本返回空列表

并在 `factory.py` 注册：

```python
registry.register("fixed_chars", lambda **kw: FixedCharsChunkingStrategy())
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_chunking.py::test_fixed_chars_strategy_splits_by_length tests/test_chunking.py::test_fixed_chars_strategy_supports_overlap -q`

Expected: PASS

- [ ] **Step 5: 回归现有切分测试**

Run: `pytest tests/test_chunking.py -q`

Expected: PASS，旧策略不回归。

---

## Task 2: 递归分块（`recursive`）

**Files:**
- Create: `app/rag/chunking/strategies/recursive.py`
- Modify: `app/rag/chunking/base.py`
- Modify: `app/rag/chunking/factory.py`
- Modify: `app/rag/chunking/strategies/__init__.py`
- Test: `tests/test_chunking.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_chunking.py` 追加：

```python
async def test_recursive_strategy_prefers_natural_boundaries() -> None:
    from app.rag.chunking.base import ChunkParams
    from app.rag.chunking.strategies.recursive import RecursiveChunkingStrategy

    text = "第一段第一句。第一段第二句。\n\n第二段第一句。第二段第二句。"
    chunks = await RecursiveChunkingStrategy().split(
        text,
        params=ChunkParams(chunk_size=16, chunk_overlap=0),
    )
    assert chunks
    assert any("第一段" in c.text for c in chunks)
    assert any("第二段" in c.text for c in chunks)


async def test_recursive_strategy_falls_back_to_char_split() -> None:
    from app.rag.chunking.base import ChunkParams
    from app.rag.chunking.strategies.recursive import RecursiveChunkingStrategy

    text = "abcdefghij"
    chunks = await RecursiveChunkingStrategy().split(
        text,
        params=ChunkParams(chunk_size=4, chunk_overlap=0),
    )
    assert [c.text for c in chunks] == ["abcd", "efgh", "ij"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_chunking.py::test_recursive_strategy_prefers_natural_boundaries tests/test_chunking.py::test_recursive_strategy_falls_back_to_char_split -q`

Expected: FAIL，`RecursiveChunkingStrategy` 尚不存在。

- [ ] **Step 3: 写最小实现**

实现 `RecursiveChunkingStrategy`，推荐分隔符优先级：

1. `\n\n`
2. `\n`
3. `。！？!?`
4. `，；,;`
5. 字符级硬切（回退到 `fixed_chars`）

实现要求：

- 单块长度 `<= chunk_size` 时直接返回
- 超长时按当前层分隔符递归拆分
- 当前层无法有效拆开时进入下一层
- 最后一层退化到固定字符数切分

如 `ChunkParams` 需要自定义分隔符，可新增：

```python
separators: list[str] | None = None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_chunking.py::test_recursive_strategy_prefers_natural_boundaries tests/test_chunking.py::test_recursive_strategy_falls_back_to_char_split -q`

Expected: PASS

- [ ] **Step 5: 将 `auto` 路由纳入递归策略候选**

修改 `factory.py` 的 `_auto_route`：

- 对无显式标题、无明显结构但正文较长的文本，优先路由到 `recursive`
- 保留当前 `structured` / `token_aware` / `paragraph` 路由规则

Run: `pytest tests/test_chunking.py -q`

Expected: PASS

---

## Task 3: 升级解析结果模型，保留结构块/版式块

**Files:**
- Modify: `app/rag/document_parsers/base.py`
- Modify: `app/rag/document_parsers/service.py`
- Modify: `app/rag/document_parsers/__init__.py`
- Test: `tests/test_document_parser_blocks.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_document_parser_blocks.py`：

```python
from app.rag.document_parsers.base import ParsedDocument


def test_parsed_document_blocks_default_empty() -> None:
    doc = ParsedDocument(
        text="正文",
        title="标题",
        source="a.docx",
        extension="docx",
        content_type=None,
    )
    assert doc.blocks == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_document_parser_blocks.py::test_parsed_document_blocks_default_empty -q`

Expected: FAIL，`ParsedDocument` 尚无 `blocks` 字段。

- [ ] **Step 3: 写最小实现**

在 `app/rag/document_parsers/base.py` 中新增结构块模型，推荐：

```python
@dataclass(slots=True)
class ParsedBlock:
    type: str
    text: str
    order: int
    page: int | None = None
    section_path: list[str] = field(default_factory=list)
    bbox: tuple[float, float, float, float] | None = None
    metadata: dict[str, str | int | float | bool | None] = field(default_factory=dict)
```

并给 `ParsedDocument` 增加：

```python
blocks: list[ParsedBlock] = field(default_factory=list)
```

同时更新 `service.py` 透传 `blocks`，保持旧调用方只依赖 `text` 也不受影响。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_document_parser_blocks.py::test_parsed_document_blocks_default_empty -q`

Expected: PASS

- [ ] **Step 5: 补一条服务透传测试**

在 `tests/test_document_parser_blocks.py` 继续追加 parser service 测试，验证 `parse_upload(...)` 返回的 `ParsedDocument.blocks` 未被抹掉。

Run: `pytest tests/test_document_parser_blocks.py -q`

Expected: PASS

---

## Task 4: 基于格式分块（`format_aware`）

**Files:**
- Create: `app/rag/chunking/strategies/format_aware.py`
- Modify: `app/rag/chunking/base.py`
- Modify: `app/rag/chunking/factory.py`
- Modify: `app/rag/document_parsers/office.py`
- Modify: `app/rag/document_parsers/pdf.py`
- Modify: `app/rag/service.py`
- Test: `tests/test_document_parser_blocks.py`
- Test: `tests/test_chunking.py`
- Test: `tests/test_rag.py`

- [ ] **Step 1: 先让解析器输出最小结构块**

最小落地范围：

- `docx`：标题段、普通段落、表格单元格
- `pptx`：页标题、正文、备注
- `xlsx/csv`：表头 + 行组
- `pdf`：先只做页级/段级块，不强依赖 bbox

要求每个 block 至少带：

- `type`
- `text`
- `order`
- `page`（能提供时）
- `section_path`（能提供时）

- [ ] **Step 2: 写失败测试**

在 `tests/test_chunking.py` 追加最小策略测试：

```python
async def test_format_aware_strategy_uses_blocks() -> None:
    from app.rag.chunking.base import ChunkParams
    from app.rag.chunking.strategies.format_aware import FormatAwareChunkingStrategy
    from app.rag.document_parsers.base import ParsedBlock

    strategy = FormatAwareChunkingStrategy()
    chunks = await strategy.split_blocks(
        [
            ParsedBlock(type="heading", text="一、总则", order=0, section_path=["一、总则"]),
            ParsedBlock(type="paragraph", text="这里是正文。", order=1, section_path=["一、总则"]),
        ],
        params=ChunkParams(chunk_size=200, chunk_overlap=0),
    )
    assert len(chunks) == 2
    assert chunks[0].metadata["block_type"] == "heading"
```

如果策略层暂时仍只接受 `text`，则本任务需先把基类扩成“双入口”：

- `split(text, *, params)`
- `split_blocks(blocks, *, params)`

或新增一个结构输入上下文对象。

- [ ] **Step 3: 写最小实现**

`FormatAwareChunkingStrategy` 规则：

- 一个 block 优先成为一个 chunk
- 超长 block 再交给 `recursive` 二次切分
- chunk metadata 写入：
  - `block_type`
  - `page`
  - `section_path`
  - `source_parser`

`RAGService` 调整建议：

- 新增统一入口 `ingest_parsed_document(parsed, ...)`，供上传解析链路调用
- `ingest_text(...)` 保留，继续走纯文本策略
- 当 `strategy in ("format_aware", "layout_aware")` 且有 `parsed.blocks` 时，优先走结构输入

- [ ] **Step 4: 增加集成测试**

在 `tests/test_rag.py` 增加：

- 摄取结构化文档后，`DocumentChunk.strategy == "format_aware"`
- `DocumentChunk.chunk_metadata` 包含 `block_type/section_path/page`

Run: `pytest tests/test_document_parser_blocks.py tests/test_chunking.py tests/test_rag.py -q`

Expected: PASS

---

## Task 5: 基于版式分块（`layout_aware`）

**Files:**
- Create: `app/rag/chunking/strategies/layout_aware.py`
- Modify: `app/rag/document_parsers/base.py`
- Modify: `app/rag/document_parsers/pdf.py`
- Modify: `app/rag/document_parsers/office.py`
- Modify: `app/rag/service.py`
- Test: `tests/test_document_parser_blocks.py`
- Test: `tests/test_chunking.py`
- Test: `tests/test_rag.py`

- [ ] **Step 1: 扩展 block 版面字段**

在 `ParsedBlock` 继续稳定这些字段：

- `page`
- `bbox`
- `metadata["reading_order"]`
- `metadata["layout_role"]`（title/body/table/caption/note 等）

- [ ] **Step 2: 写失败测试**

在 `tests/test_chunking.py` 追加：

```python
async def test_layout_aware_strategy_respects_reading_order() -> None:
    from app.rag.chunking.base import ChunkParams
    from app.rag.chunking.strategies.layout_aware import LayoutAwareChunkingStrategy
    from app.rag.document_parsers.base import ParsedBlock

    blocks = [
        ParsedBlock(type="text", text="右栏第二段", order=2, page=1, metadata={"reading_order": 2}),
        ParsedBlock(type="text", text="左栏第一段", order=1, page=1, metadata={"reading_order": 1}),
    ]
    chunks = await LayoutAwareChunkingStrategy().split_blocks(
        blocks,
        params=ChunkParams(chunk_size=100, chunk_overlap=0),
    )
    assert [c.text for c in chunks] == ["左栏第一段", "右栏第二段"]
```

- [ ] **Step 3: 写最小实现**

`LayoutAwareChunkingStrategy` 行为建议：

- 先按 `page` 分组
- 页面内按 `reading_order` 排序
- 不同 `layout_role` 可决定是否合并：
  - 标题与紧随正文可合并
  - 表格块单独保留
  - 图注/备注单独保留或小范围拼接
- 超长块再交给 `recursive`

解析层最小改造：

- `pdf.py`：先为文本层 PDF 产出 `page + order`，bbox 可暂缺
- OCR 回流结果若暂时没有 bbox，也至少给 `page` 与线性顺序
- `pptx` 图片页/PDF OCR 结果可先做页级块，不要求一次性补齐精确 bbox

- [ ] **Step 4: 增加集成测试**

在 `tests/test_rag.py` 中补：

- 双栏顺序不乱
- PDF/OCR 页块 metadata 带 `page`
- `strategy == "layout_aware"`

Run: `pytest tests/test_document_parser_blocks.py tests/test_chunking.py tests/test_rag.py -q`

Expected: PASS

---

## Task 6: 命题分块（`proposition`）

**Files:**
- Create: `app/rag/chunking/strategies/proposition.py`
- Modify: `app/rag/chunking/base.py`
- Modify: `app/rag/chunking/factory.py`
- Test: `tests/test_chunking.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_chunking.py` 追加：

```python
async def test_proposition_strategy_splits_on_topic_shift() -> None:
    from app.rag.chunking.base import ChunkParams
    from app.rag.chunking.strategies.proposition import PropositionChunkingStrategy

    class FakeEmbedding:
        async def embed(self, texts):
            return [
                [1.0, 0.0],
                [0.9, 0.1],
                [0.0, 1.0],
            ]

    strategy = PropositionChunkingStrategy(FakeEmbedding())
    text = "产品支持多租户。权限按租户隔离。下面介绍报表导出。"
    chunks = await strategy.split(
        text,
        params=ChunkParams(chunk_size=200, similarity_threshold=0.5),
    )
    assert len(chunks) == 2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_chunking.py::test_proposition_strategy_splits_on_topic_shift -q`

Expected: FAIL，`PropositionChunkingStrategy` 尚不存在。

- [ ] **Step 3: 写最小实现**

第一版不要上 LLM，只做轻量版：

- 复用 `semantic` 的句切分与 embedding
- 在相邻句向量相似度之外，再叠加命题信号：
  - “首先/其次/最后/因此/但是/结论”等转折词
  - 标题句与正文句过渡
  - 列表项起始标记
- 命中明显主题漂移时断块

建议新增参数：

```python
topic_shift_threshold: float | None = None
max_sentences_per_chunk: int | None = None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_chunking.py::test_proposition_strategy_splits_on_topic_shift -q`

Expected: PASS

- [ ] **Step 5: 评估回归**

至少增加 3 组文本型测试：

- 同主题连续段不应过度切分
- 结论段/转折段应独立
- 列表型论点尽量保持成组

Run: `pytest tests/test_chunking.py -q`

Expected: PASS

---

## Task 7: 配置、README 与验收回归

**Files:**
- Modify: `app/core/config.py`
- Modify: `.env.example`
- Modify: `README.md`

- [ ] **Step 1: 扩展配置说明**

将 `RAG_CHUNK_STRATEGY` 说明扩成：

- `fixed_chars`
- `recursive`
- `format_aware`
- `layout_aware`
- `proposition`

并保留已有：

- `structured`
- `paragraph`
- `sliding_window`
- `token_aware`
- `semantic`
- `parent_child`
- `auto`

- [ ] **Step 2: README 补“适用场景”**

在现有“文档切分策略”小节中，为新增策略写一句场景说明：

- `fixed_chars`：baseline/debug/压测
- `recursive`：通用长文正文
- `format_aware`：docx/pptx/xlsx/json/xml/csv 等结构化文档
- `layout_aware`：pdf/ocr/双栏/表格页
- `proposition`：主题/论点强切换的长文

- [ ] **Step 3: 全量回归**

Run: `pytest tests/test_chunking.py tests/test_document_parser_blocks.py tests/test_rag.py tests/test_rag_backend.py -q`

Expected: PASS

Run: `ruff check app/rag/chunking app/rag/document_parsers app/rag/service.py tests/test_chunking.py tests/test_document_parser_blocks.py tests/test_rag.py`

Expected: `All checks passed!`

---

## 每一项的测试策略摘要

- **`fixed_chars`**：块边界、overlap、空文本、短文本
- **`recursive`**：自然边界优先、无法自然拆分时回退字符级、中文长文稳定性
- **`format_aware`**：解析器是否产出结构块、结构块是否映射为 chunk、metadata 是否保留结构信息
- **`layout_aware`**：页内阅读顺序、双栏顺序、表格/备注区隔离、OCR 页块 metadata
- **`proposition`**：主题漂移断块、转折/结论边界、不过度切分

---

## 风险与实施注意事项

- **关键边界 1：** 当前 `RAGService.ingest_text(...)` 只接纯文本；若不补结构输入入口，`format_aware/layout_aware` 会在 parser 之后丢失信息。
- **关键边界 2：** `pdf.py` 与 `office.py` 当前主要输出 `text + metadata`，需要分阶段补 `blocks`，不要试图一次性把所有解析器都做成满配。
- **关键边界 3：** `proposition` 不宜一开始引入 LLM，先做可测、可回归的轻量规则 + embedding 版本。
- **关键边界 4：** `auto` 路由不要过早接入 `format_aware/layout_aware`，应先只在“调用方明确传入结构化解析结果”时启用，避免纯文本入口误判。

---

## 建议的分批落地

**第一批：**

- Task 1 `fixed_chars`
- Task 2 `recursive`

**第二批：**

- Task 3 解析结果模型升级
- Task 4 `format_aware`

**第三批：**

- Task 5 `layout_aware`
- Task 6 `proposition`
- Task 7 配置/README/全量回归
