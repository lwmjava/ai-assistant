# RAG 多模式文档切分 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 RAG 文档切分从单一字符窗口硬切升级为可插拔的多模式切分策略层，支持段落 / 滑动窗口 / token 感知 / 语义 / 父子 / 结构六种模式，并接入摄取与父子检索。

**Architecture:** 新建 `app/rag/chunking/` 策略层（`base.py` 抽象 + `strategies/` 实现 + `registry/factory` 分发），切分结果从纯文本块升级为带 `parent_id` / `metadata` 的 `Chunk`；`RAGService.ingest_text` 通过统一接口调用策略层，`DocumentChunk` 增加 `parent_id / strategy / metadata` 字段支撑父子文档与溯源。

**Tech Stack:** Python 3.12、FastAPI、SQLModel、pytest、现有 `EmbeddingProvider` 抽象、可选 `tiktoken`。

---

## 文件结构映射

**新建：**

- `app/rag/chunking/__init__.py` — 包导出
- `app/rag/chunking/base.py` — `Chunk` / `ChunkParams` / `ChunkingStrategy`
- `app/rag/chunking/strategies/__init__.py` — 策略导出
- `app/rag/chunking/strategies/paragraph.py` — 段落切分
- `app/rag/chunking/strategies/sliding_window.py` — 滑动窗口
- `app/rag/chunking/strategies/token_aware.py` — token 感知
- `app/rag/chunking/strategies/structured.py` — 结构切分（迁移现有 Markdown 逻辑）
- `app/rag/chunking/strategies/semantic.py` — 语义切分
- `app/rag/chunking/strategies/parent_child.py` — 父子文档
- `app/rag/chunking/registry.py` — 策略注册表
- `app/rag/chunking/factory.py` — 策略分发 + auto 路由
- `tests/test_chunking.py` — 切分策略单测
- `alembic/versions/<rev>_add_chunk_metadata.py` — `DocumentChunk` 迁移

**修改：**

- `app/models/rag.py` — `DocumentChunk` 增加字段
- `app/rag/service.py` — `ingest_text` 接入策略层
- `app/rag/backend/native.py` — `split` 委托策略层
- `app/rag/retriever.py` / `app/rag/service.py` — 父子检索配合
- `app/core/config.py` — 新增 `RAG_CHUNK_STRATEGY`
- `tests/test_rag.py` — 摄取回归
- `.env.example` / `README.md` — 配置说明

---

## Task 1: 切分策略层骨架（base.py）

**Files:**
- Create: `app/rag/chunking/__init__.py`
- Create: `app/rag/chunking/base.py`
- Test: `tests/test_chunking.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_chunking.py` 写入：

```python
"""多模式切分策略层测试。"""

import pytest

from app.rag.chunking.base import Chunk, ChunkParams


def test_chunk_defaults() -> None:
    chunk = Chunk(text="内容", index=0)
    assert chunk.parent_id is None
    assert chunk.metadata == {}


def test_chunk_params_defaults() -> None:
    params = ChunkParams()
    assert params.chunk_size == 500
    assert params.chunk_overlap == 64
    assert params.parent_ratio == 3
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_chunking.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.rag.chunking'`

- [ ] **Step 3: 写最小实现**

创建 `app/rag/chunking/base.py`：

```python
"""切分策略层统一协议与模型。

定义策略接口 ``ChunkingStrategy``、统一切分结果 ``Chunk`` 与策略参数
``ChunkParams``。切分结果带 ``parent_id`` / ``metadata``，为父子文档与溯源打基础。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(slots=True)
class Chunk:
    """一个切分结果块。"""

    text: str
    index: int
    parent_id: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass(slots=True)
class ChunkParams:
    """切分参数，通用字段 + 各策略专用字段（可空）。"""

    chunk_size: int = 500
    chunk_overlap: int = 64
    # 滑动窗口
    window_size: int | None = None
    step: int | None = None
    # token 感知
    max_tokens: int | None = None
    overlap_tokens: int | None = None
    # 语义切分
    similarity_threshold: float | None = None
    # 父子文档
    parent_strategy: str | None = None
    parent_ratio: int = 3
    child_strategy: str | None = None
    child_params: dict | None = None


class ChunkingStrategy(ABC):
    """切分策略统一接口。"""

    name: str

    @abstractmethod
    async def split(self, text: str, *, params: ChunkParams) -> list[Chunk]:
        """将文本切分为块列表，空输入返回空列表。"""
        raise NotImplementedError
```

创建 `app/rag/chunking/__init__.py`：

```python
"""RAG 多模式文档切分策略层。"""

from app.rag.chunking.base import Chunk, ChunkParams, ChunkingStrategy

__all__ = ["Chunk", "ChunkParams", "ChunkingStrategy"]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_chunking.py -q`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add app/rag/chunking/__init__.py app/rag/chunking/base.py tests/test_chunking.py
git commit -m "feat(chunking): add chunking strategy base model"
```

---

## Task 2: 段落切分策略

**Files:**
- Create: `app/rag/chunking/strategies/__init__.py`
- Create: `app/rag/chunking/strategies/paragraph.py`
- Test: `tests/test_chunking.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_chunking.py` 追加：

```python
from app.rag.chunking.strategies.paragraph import ParagraphChunkingStrategy


async def test_paragraph_splits_on_blank_lines() -> None:
    text = "第一段内容。\n\n第二段内容。\n\n第三段内容。"
    chunks = await ParagraphChunkingStrategy().split(
        text, params=ChunkParams(chunk_size=100)
    )
    assert len(chunks) == 3
    assert [c.text for c in chunks] == ["第一段内容。", "第二段内容。", "第三段内容。"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_chunking.py::test_paragraph_splits_on_blank_lines -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.rag.chunking.strategies'`

- [ ] **Step 3: 写最小实现**

创建 `app/rag/chunking/strategies/__init__.py`：

```python
"""切分策略实现集合。"""
```

创建 `app/rag/chunking/strategies/paragraph.py`：

```python
"""段落切分策略：按空行 / 段落边界切分。"""

from __future__ import annotations

from app.rag.chunking.base import Chunk, ChunkParams, ChunkingStrategy


def _hard_split(text: str, chunk_size: int) -> list[str]:
    """段落内超长时按字符硬切。"""
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


class ParagraphChunkingStrategy(ChunkingStrategy):
    """按空行分段，超长段落内部按字符硬切。"""

    name = "paragraph"

    async def split(self, text: str, *, params: ChunkParams) -> list[Chunk]:
        text = (text or "").strip()
        if not text:
            return []
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        chunks: list[Chunk] = []
        for para in paragraphs:
            pieces = (
                [para]
                if len(para) <= params.chunk_size
                else _hard_split(para, params.chunk_size)
            )
            for piece in pieces:
                chunks.append(Chunk(text=piece, index=len(chunks)))
        return chunks
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_chunking.py -q`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add app/rag/chunking/strategies/__init__.py app/rag/chunking/strategies/paragraph.py tests/test_chunking.py
git commit -m "feat(chunking): add paragraph strategy"
```

---

## Task 3: 滑动窗口切分策略

**Files:**
- Create: `app/rag/chunking/strategies/sliding_window.py`
- Test: `tests/test_chunking.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_chunking.py` 追加：

```python
from app.rag.chunking.strategies.sliding_window import SlidingWindowChunkingStrategy


async def test_sliding_window_overlaps_by_step() -> None:
    text = "0123456789"
    chunks = await SlidingWindowChunkingStrategy().split(
        text, params=ChunkParams(window_size=6, step=4)
    )
    assert [c.text for c in chunks] == ["012345", "456789"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_chunking.py::test_sliding_window_overlaps_by_step -q`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 写最小实现**

创建 `app/rag/chunking/strategies/sliding_window.py`：

```python
"""滑动窗口切分策略：固定窗口 + 步长，不依赖句子边界。"""

from __future__ import annotations

from app.rag.chunking.base import Chunk, ChunkParams, ChunkingStrategy


class SlidingWindowChunkingStrategy(ChunkingStrategy):
    """按 ``window_size`` 窗口、``step`` 步长滑动切分。"""

    name = "sliding_window"

    async def split(self, text: str, *, params: ChunkParams) -> list[Chunk]:
        text = (text or "").strip()
        if not text:
            return []
        window = params.window_size or params.chunk_size
        step = params.step or max(1, window - params.chunk_overlap)
        chunks: list[Chunk] = []
        start = 0
        while start < len(text):
            piece = text[start : start + window].strip()
            if piece:
                chunks.append(Chunk(text=piece, index=len(chunks)))
            start += step
        return chunks
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_chunking.py -q`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add app/rag/chunking/strategies/sliding_window.py tests/test_chunking.py
git commit -m "feat(chunking): add sliding window strategy"
```

---

## Task 4: token 感知切分策略

**Files:**
- Create: `app/rag/chunking/strategies/token_aware.py`
- Test: `tests/test_chunking.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_chunking.py` 追加：

```python
from app.rag.chunking.strategies.token_aware import TokenAwareChunkingStrategy


async def test_token_aware_uses_fallback_estimator() -> None:
    # 中文每字约 1 token，max_tokens=4 应切出两块。
    text = "甲乙丙丁戊己"
    chunks = await TokenAwareChunkingStrategy().split(
        text, params=ChunkParams(max_tokens=4)
    )
    assert len(chunks) == 2
    assert all(c.text for c in chunks)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_chunking.py::test_token_aware_uses_fallback_estimator -q`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 写最小实现**

创建 `app/rag/chunking/strategies/token_aware.py`：

```python
"""token 感知切分策略：按 token 数切分而非字符数。"""

from __future__ import annotations

import re

from app.rag.chunking.base import Chunk, ChunkParams, ChunkingStrategy

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数：中文 1 字符 ≈ 1 token，其余约 4 字符 / token。"""
    cjk = len(_CJK_RE.findall(text))
    other = len(text) - cjk
    return cjk + (other + 3) // 4


def _try_load_tokenizer():
    """优先加载 tiktoken，不可用则返回 None。"""
    try:
        import tiktoken  # noqa: PLC0415

        return tiktoken.get_encoding("cl100k_base").encode
    except Exception:  # noqa: BLE001 - tiktoken 可选
        return None


class TokenAwareChunkingStrategy(ChunkingStrategy):
    """按 token 上限切分，优先 tiktoken，缺失时回退估算。"""

    name = "token_aware"

    async def split(self, text: str, *, params: ChunkParams) -> list[Chunk]:
        text = (text or "").strip()
        if not text:
            return []
        max_tokens = params.max_tokens or max(1, params.chunk_size // 2)
        encode = _try_load_tokenizer()

        def count(value: str) -> int:
            return len(encode(value)) if encode else _estimate_tokens(value)

        chunks: list[Chunk] = []
        buf = ""
        for char in text:
            if count(buf + char) > max_tokens and buf:
                chunks.append(Chunk(text=buf.strip(), index=len(chunks)))
                buf = char
            else:
                buf += char
        if buf.strip():
            chunks.append(Chunk(text=buf.strip(), index=len(chunks)))
        return chunks
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_chunking.py -q`
Expected: PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add app/rag/chunking/strategies/token_aware.py tests/test_chunking.py
git commit -m "feat(chunking): add token-aware strategy"
```

---

## Task 5: 结构切分策略（迁移现有 Markdown 逻辑）

**Files:**
- Create: `app/rag/chunking/strategies/structured.py`
- Test: `tests/test_chunking.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_chunking.py` 追加：

```python
from app.rag.chunking.strategies.structured import StructuredChunkingStrategy


async def test_structured_preserves_headings() -> None:
    text = "# 章节A\n" + "A内容。" * 20 + "\n\n# 章节B\n" + "B内容。" * 20
    chunks = await StructuredChunkingStrategy().split(
        text, params=ChunkParams(chunk_size=50, chunk_overlap=10)
    )
    assert any(c.text.startswith("# 章节A") for c in chunks)
    assert any(c.text.startswith("# 章节B") for c in chunks)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_chunking.py::test_structured_preserves_headings -q`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 写最小实现**

创建 `app/rag/chunking/strategies/structured.py`：

```python
"""结构切分策略：按 Markdown 标题分节，复用现有句子切分逻辑。"""

from __future__ import annotations

from app.rag.chunking.base import Chunk, ChunkParams, ChunkingStrategy
from app.rag.ingestion import split_text_structured


class StructuredChunkingStrategy(ChunkingStrategy):
    """复用现有 ``split_text_structured``，将其包装为统一 Chunk 输出。"""

    name = "structured"

    async def split(self, text: str, *, params: ChunkParams) -> list[Chunk]:
        pieces = split_text_structured(text, params.chunk_size, params.chunk_overlap)
        return [Chunk(text=piece, index=i) for i, piece in enumerate(pieces)]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_chunking.py -q`
Expected: PASS（6 passed）

- [ ] **Step 5: 提交**

```bash
git add app/rag/chunking/strategies/structured.py tests/test_chunking.py
git commit -m "feat(chunking): add structured strategy wrapping split_text_structured"
```

---

## Task 6: 注册表与工厂（含 auto 路由）

**Files:**
- Create: `app/rag/chunking/registry.py`
- Create: `app/rag/chunking/factory.py`
- Test: `tests/test_chunking.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_chunking.py` 追加：

```python
from app.rag.chunking.factory import get_chunking_strategy, resolve_strategy_name
from app.rag.chunking.strategies.paragraph import ParagraphChunkingStrategy
from app.rag.chunking.strategies.structured import StructuredChunkingStrategy


def test_resolve_strategy_name_request_wins() -> None:
    assert resolve_strategy_name("内容", "semantic") == "semantic"


def test_resolve_strategy_name_auto_routes_structured() -> None:
    assert resolve_strategy_name("# 标题\n正文", "auto") == "structured"


def test_get_chunking_strategy_paragraph() -> None:
    strategy = get_chunking_strategy("paragraph")
    assert isinstance(strategy, ParagraphChunkingStrategy)


def test_get_chunking_strategy_unknown_raises() -> None:
    with pytest.raises(ValueError, match="不支持的切分策略"):
        get_chunking_strategy("nope")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_chunking.py -k "resolve_strategy_name or get_chunking_strategy" -q`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 写最小实现**

创建 `app/rag/chunking/registry.py`：

```python
"""切分策略注册表：按 name 映射策略构造器。"""

from __future__ import annotations

from collections.abc import Callable

from app.rag.chunking.base import ChunkingStrategy

StrategyBuilder = Callable[..., ChunkingStrategy]


class ChunkingRegistry:
    """策略 name -> 构造器 的注册表。"""

    def __init__(self) -> None:
        self._builders: dict[str, StrategyBuilder] = {}

    def register(self, name: str, builder: StrategyBuilder) -> None:
        self._builders[name] = builder

    def names(self) -> list[str]:
        return list(self._builders)

    def build(self, name: str, **kwargs) -> ChunkingStrategy:
        builder = self._builders.get(name)
        if builder is None:
            raise ValueError(f"不支持的切分策略: {name}")
        return builder(**kwargs)
```

创建 `app/rag/chunking/factory.py`：

```python
"""切分策略分发与 auto 路由。"""

from __future__ import annotations

import re

from app.core.config import settings
from app.rag.chunking.base import ChunkingStrategy
from app.rag.chunking.registry import ChunkingRegistry
from app.rag.chunking.strategies.paragraph import ParagraphChunkingStrategy
from app.rag.chunking.strategies.parent_child import ParentChildChunkingStrategy
from app.rag.chunking.strategies.semantic import SemanticChunkingStrategy
from app.rag.chunking.strategies.sliding_window import SlidingWindowChunkingStrategy
from app.rag.chunking.strategies.structured import StructuredChunkingStrategy
from app.rag.chunking.strategies.token_aware import TokenAwareChunkingStrategy

_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _auto_route(text: str) -> str:
    """按文档特征选择策略。"""
    if _HEADING_RE.search(text):
        return "structured"
    if len(_CJK_RE.findall(text)) / max(1, len(text)) < 0.3:
        return "token_aware"
    return "paragraph"


def resolve_strategy_name(text: str, strategy: str | None) -> str:
    """解析最终策略名：请求级 > 配置级 > auto 路由。"""
    if strategy and strategy != "auto":
        return strategy
    configured = settings.RAG_CHUNK_STRATEGY
    if strategy == "auto" or configured == "auto":
        return _auto_route(text)
    return configured


def build_registry() -> ChunkingRegistry:
    """构建默认策略注册表。"""
    registry = ChunkingRegistry()
    registry.register("paragraph", lambda **kw: ParagraphChunkingStrategy())
    registry.register("sliding_window", lambda **kw: SlidingWindowChunkingStrategy())
    registry.register("token_aware", lambda **kw: TokenAwareChunkingStrategy())
    registry.register("structured", lambda **kw: StructuredChunkingStrategy())
    registry.register(
        "semantic", lambda embedding=None, **kw: SemanticChunkingStrategy(embedding)
    )
    registry.register(
        "parent_child",
        lambda embedding=None, **kw: ParentChildChunkingStrategy(
            registry=None, embedding=embedding
        ),
    )
    return registry


def get_chunking_strategy(
    name: str, embedding=None
) -> ChunkingStrategy:
    """按 name 返回策略实例。"""
    return build_registry().build(name, embedding=embedding)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_chunking.py -k "resolve_strategy_name or get_chunking_strategy" -q`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add app/rag/chunking/registry.py app/rag/chunking/factory.py tests/test_chunking.py
git commit -m "feat(chunking): add registry and factory with auto routing"
```

---

## Task 7: 语义切分策略

**Files:**
- Create: `app/rag/chunking/strategies/semantic.py`
- Test: `tests/test_chunking.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_chunking.py` 追加：

```python
from app.rag.chunking.strategies.semantic import SemanticChunkingStrategy


async def test_semantic_splits_on_low_similarity() -> None:
    class FakeEmbedding:
        async def embed(self, texts):
            # 用简单哈希模拟相似度：相同主题句子向量相近。
            return [[hash(t) % 100, 1.0] for t in texts]

    text = "主题A第一句。主题A第二句。主题B第三句。"
    strategy = SemanticChunkingStrategy(FakeEmbedding())
    chunks = await strategy.split(
        text, params=ChunkParams(similarity_threshold=0.0)
    )
    assert chunks
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_chunking.py::test_semantic_splits_on_low_similarity -q`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 写最小实现**

创建 `app/rag/chunking/strategies/semantic.py`：

```python
"""语义切分策略：用 embedding 计算相邻候选相似度，低于阈值处断开。"""

from __future__ import annotations

import re

from app.rag.chunking.base import Chunk, ChunkParams, ChunkingStrategy

_SENTENCE_RE = re.compile(r"[^。！？!?\n]+[。！？!?]?")


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


class SemanticChunkingStrategy(ChunkingStrategy):
    """复用 embedding provider，按相邻句子相似度切分。"""

    name = "semantic"

    def __init__(self, embedding) -> None:
        if embedding is None:
            raise ValueError("语义切分需要 embedding provider")
        self._embedding = embedding

    async def split(self, text: str, *, params: ChunkParams) -> list[Chunk]:
        text = (text or "").strip()
        if not text:
            return []
        sentences = [s.strip() for s in _SENTENCE_RE.findall(text) if s.strip()]
        if len(sentences) <= 1:
            return [Chunk(text=text, index=0)]
        vectors = await self._embedding.embed(sentences)
        threshold = params.similarity_threshold or 0.5
        chunks: list[Chunk] = []
        buf = sentences[0]
        prev = vectors[0]
        for sentence, vec in zip(sentences[1:], vectors[1:]):
            if _cosine(prev, vec) < threshold:
                chunks.append(Chunk(text=buf, index=len(chunks)))
                buf = sentence
            else:
                buf += sentence
            prev = vec
        if buf.strip():
            chunks.append(Chunk(text=buf, index=len(chunks)))
        return chunks
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_chunking.py -q`
Expected: PASS（7 passed）

- [ ] **Step 5: 提交**

```bash
git add app/rag/chunking/strategies/semantic.py tests/test_chunking.py
git commit -m "feat(chunking): add semantic strategy using embedding"
```

---

## Task 8: 父子文档切分策略

**Files:**
- Create: `app/rag/chunking/strategies/parent_child.py`
- Test: `tests/test_chunking.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_chunking.py` 追加：

```python
from app.rag.chunking.strategies.parent_child import ParentChildChunkingStrategy


async def test_parent_child_builds_relations() -> None:
    text = "第一段。" * 20 + "\n\n" + "第二段。" * 20
    strategy = ParentChildChunkingStrategy(
        registry=None, embedding=None
    )
    chunks = await strategy.split(
        text,
        params=ChunkParams(
            child_strategy="paragraph",
            child_params={"chunk_size": 30},
            parent_strategy="paragraph",
            parent_ratio=3,
        ),
    )
    children = [c for c in chunks if c.metadata.get("kind") == "child"]
    parents = [c for c in chunks if c.metadata.get("kind") == "parent"]
    assert children
    assert parents
    assert all(c.parent_id is not None for c in children)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_chunking.py::test_parent_child_builds_relations -q`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 写最小实现**

创建 `app/rag/chunking/strategies/parent_child.py`：

```python
"""父子文档切分策略：父块为子块的倍数粗切分块，子块为细粒度块。"""

from __future__ import annotations

from app.rag.chunking.base import Chunk, ChunkParams, ChunkingStrategy
from app.rag.chunking.registry import ChunkingRegistry


class ParentChildChunkingStrategy(ChunkingStrategy):
    """父块 = 子块 N 倍粗块；子块记录 ``parent_id`` 指向父块。"""

    name = "parent_child"

    def __init__(self, *, registry: ChunkingRegistry | None, embedding=None) -> None:
        self._registry = registry
        self._embedding = embedding

    def _build_child(self, name: str, params: ChunkParams) -> ChunkingStrategy:
        if self._registry is not None:
            return self._registry.build(name, embedding=self._embedding)
        # 回退到 factory，避免循环导入
        from app.rag.chunking.factory import get_chunking_strategy

        return get_chunking_strategy(name, embedding=self._embedding)

    async def split(self, text: str, *, params: ChunkParams) -> list[Chunk]:
        text = (text or "").strip()
        if not text:
            return []
        child_name = params.child_strategy or "paragraph"
        parent_name = params.parent_strategy or "paragraph"
        ratio = params.parent_ratio or 3

        child_strategy = self._build_child(child_name, params)
        children = await child_strategy.split(text, params=params)

        parent_size = max(1, params.chunk_size * ratio)
        parent_strategy = self._build_child(parent_name, params)
        parent_params = ChunkParams(
            chunk_size=parent_size,
            chunk_overlap=params.chunk_overlap,
            window_size=parent_size,
        )
        parents = await parent_strategy.split(text, params=parent_params)

        # 把子块按序归属到父块：按子块在原文中的顺序简单映射。
        result: list[Chunk] = []
        for i, parent in enumerate(parents):
            parent.metadata["kind"] = "parent"
            parent.metadata["parent_key"] = f"p{i}"
            result.append(parent)
        per_parent = max(1, len(children) // max(1, len(parents)))
        for j, child in enumerate(children):
            parent_idx = min(j // per_parent, len(parents) - 1)
            child.parent_id = f"p{parent_idx}"
            child.metadata["kind"] = "child"
            result.append(child)
        return result
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_chunking.py::test_parent_child_builds_relations -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/rag/chunking/strategies/parent_child.py tests/test_chunking.py
git commit -m "feat(chunking): add parent-child strategy"
```

---

## Task 9: DocumentChunk 迁移（parent_id / strategy / metadata）

**Files:**
- Modify: `app/models/rag.py`
- Create: `alembic/versions/<rev>_add_chunk_metadata.py`
- Test: `tests/test_rag.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_rag.py` 追加：

```python
def test_document_chunk_has_parent_metadata_fields() -> None:
    from app.models.rag import DocumentChunk

    chunk = DocumentChunk(
        tenant_id="t",
        document_id="d",
        chunk_index=0,
        content="内容",
        parent_id=None,
        strategy="paragraph",
        metadata="{}",
    )
    assert chunk.strategy == "paragraph"
    assert chunk.metadata == "{}"
    assert chunk.parent_id is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_rag.py::test_document_chunk_has_parent_metadata_fields -q`
Expected: FAIL，构造 `DocumentChunk` 时 `parent_id / strategy / metadata` 为意外关键字

- [ ] **Step 3: 写最小实现**

在 `app/models/rag.py` 的 `DocumentChunk` 类里，`tokens` 字段之后新增：

```python
    # 父子文档关联：指向父块（parent_id 为 None 表示自身是父块或普通块）。
    parent_id: str | None = Field(default=None, index=True)
    # 本次切分使用的策略名，便于审计与后续重解析策略还原。
    strategy: str | None = Field(default=None)
    # 章节 / 位置 / 溯源等扩展信息（JSON 字符串）。
    metadata: str | None = Field(default=None)
```

创建迁移 `alembic/versions/<rev>_add_chunk_metadata.py`（`<rev>` 取当前最新版本号的下一短 hash）：

```python
"""add chunk parent/strategy/metadata

Revision ID: <rev>
Revises: b7d9f3e41d2a
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "<rev>"
down_revision = "b7d9f3e41d2a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("rag_document_chunks", sa.Column("parent_id", sa.String(), nullable=True))
    op.add_column("rag_document_chunks", sa.Column("strategy", sa.String(), nullable=True))
    op.add_column("rag_document_chunks", sa.Column("metadata", sa.String(), nullable=True))
    op.create_index(
        "ix_rag_document_chunks_parent_id", "rag_document_chunks", ["parent_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_rag_document_chunks_parent_id", table_name="rag_document_chunks")
    op.drop_column("rag_document_chunks", "metadata")
    op.drop_column("rag_document_chunks", "strategy")
    op.drop_column("rag_document_chunks", "parent_id")
```

> 注意：`<rev>` 需要替换为真实 revision id。用 `alembic revision --autogenerate -m "add chunk metadata"` 生成更稳妥。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_rag.py::test_document_chunk_has_parent_metadata_fields -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/models/rag.py alembic/versions/<rev>_add_chunk_metadata.py tests/test_rag.py
git commit -m "feat(chunking): add parent_id/strategy/metadata to DocumentChunk"
```

---

## Task 10: ingest_text 接入策略层与模式选择

**Files:**
- Modify: `app/rag/service.py`
- Modify: `app/rag/backend/native.py`
- Test: `tests/test_rag.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_rag.py` 追加：

```python
async def test_ingest_uses_paragraph_strategy_metadata(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.models.rag import DocumentChunk
    from app.rag.service import RAGService

    user = User(
        id=f"u-{uuid.uuid4().hex}",
        tenant_id=f"t-{uuid.uuid4().hex}",
        username="chunker",
        hashed_password="",
        role=Role.TENANT_ADMIN.value,
        token_version=0,
        is_active=True,
    )
    rag = RAGService(session, user.tenant_id)
    doc = await rag.ingest_text(
        "第一段。\n\n第二段。",
        "切分测试",
        "test",
        user.id,
        strategy="paragraph",
    )
    chunks = session.exec(
        select(DocumentChunk).where(DocumentChunk.document_id == doc.id)
    ).all()
    assert all(c.strategy == "paragraph" for c in chunks)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_rag.py::test_ingest_uses_paragraph_strategy_metadata -q`
Expected: FAIL，`ingest_text()` 不接受 `strategy` 关键字

- [ ] **Step 3: 写最小实现**

修改 `app/rag/service.py`：

1. 顶部 import 区新增：

```python
from app.rag.chunking.base import ChunkParams
from app.rag.chunking.factory import get_chunking_strategy, resolve_strategy_name
```

2. `ingest_text` 签名新增 `strategy` 与 `chunk_params`：

```python
    async def ingest_text(
        self,
        text: str,
        title: str,
        source: str | None,
        user_id: str,
        *,
        storage_path: str | None = None,
        backend: str | None = None,
        source_kind: str | None = None,
        source_uri: str | None = None,
        content_hash: str | None = None,
        version_group_id: str | None = None,
        version_number: int = 1,
        previous_document_id: str | None = None,
        import_job_id: str | None = None,
        strategy: str | None = None,
        chunk_params: dict | None = None,
    ) -> Document:
```

3. 把原分块逻辑替换为策略层调用：

原：

```python
        rag_backend = self._resolve_backend(backend)
        chunks = await rag_backend.split(
            text, chunk_size=settings.RAG_CHUNK_SIZE, overlap=settings.RAG_CHUNK_OVERLAP
        )
        if not chunks:
            raise ValueError("文本为空或无法切分为任何分块")
```

替换为：

```python
        strategy_name = resolve_strategy_name(text, strategy)
        chunking = get_chunking_strategy(strategy_name, embedding=self._embedding)
        params = ChunkParams(
            chunk_size=settings.RAG_CHUNK_SIZE,
            chunk_overlap=settings.RAG_CHUNK_OVERLAP,
            **(chunk_params or {}),
        )
        chunk_objs = await chunking.split(text, params=params)
        if not chunk_objs:
            raise ValueError("文本为空或无法切分为任何分块")
```

4. 落库循环改为消费 `Chunk` 对象，先落父块再落子块：

原：

```python
        chunk_rows: list[DocumentChunk] = []
        for index, content in enumerate(chunks):
            row = DocumentChunk(
                tenant_id=self.tenant_id,
                document_id=document.id,
                chunk_index=index,
                content=content,
                source=source,
                tokens=json.dumps(self._tokenizer(content), ensure_ascii=False),
            )
            chunk_rows.append(row)
            self.session.add(row)
```

替换为：

```python
        chunk_rows: list[DocumentChunk] = []
        parent_id_map: dict[str, str] = {}
        for chunk in chunk_objs:
            row = DocumentChunk(
                tenant_id=self.tenant_id,
                document_id=document.id,
                chunk_index=chunk.index,
                content=chunk.text,
                source=source,
                tokens=json.dumps(self._tokenizer(chunk.text), ensure_ascii=False),
                strategy=strategy_name,
                metadata=json.dumps(chunk.metadata, ensure_ascii=False) if chunk.metadata else None,
            )
            if chunk.metadata.get("kind") == "parent":
                self.session.add(row)
                self.session.commit()
                self.session.refresh(row)
                parent_id_map[str(chunk.metadata.get("parent_key"))] = row.id
                chunk_rows.append(row)
            else:
                if chunk.parent_id and chunk.parent_id in parent_id_map:
                    row.parent_id = parent_id_map[chunk.parent_id]
                chunk_rows.append(row)
                self.session.add(row)
```

> 注意：父块需先 commit 拿到真实 id，再落子块时回填 `parent_id`。父块也会进入 `chunk_rows` 参与后续 `_embed_and_store`。

修改 `app/rag/backend/native.py`：`split` 方法改为委托策略层，并返回 `list[Chunk]` 兼容（若仍被调用则走结构化策略）：

```python
    async def split(self, text: str, *, chunk_size: int, overlap: int) -> list[str]:
        from app.rag.chunking.factory import get_chunking_strategy
        from app.rag.chunking.base import ChunkParams

        chunks = await get_chunking_strategy("structured").split(
            text, params=ChunkParams(chunk_size=chunk_size, chunk_overlap=overlap)
        )
        return [c.text for c in chunks]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_rag.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/rag/service.py app/rag/backend/native.py tests/test_rag.py
git commit -m "feat(chunking): wire chunking strategy layer into ingest_text"
```

---

## Task 11: 父子检索配合（命中子块返回父块）

**Files:**
- Modify: `app/rag/service.py`（`search` 方法）
- Test: `tests/test_rag.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_rag.py` 追加：

```python
async def test_search_expands_child_hits_to_parent(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.models.rag import DocumentChunk
    from app.rag.service import RAGService
    from app.rag.vectorstore.base import ChunkResult

    user = User(
        id=f"u-{uuid.uuid4().hex}",
        tenant_id=f"t-{uuid.uuid4().hex}",
        username="retr",
        hashed_password="",
        role=Role.TENANT_ADMIN.value,
        token_version=0,
        is_active=True,
    )
    rag = RAGService(session, user.tenant_id)
    doc = await rag.ingest_text(
        "父块内容第一句。父块内容第二句。",
        "父子检索",
        "test",
        user.id,
        strategy="parent_child",
    )
    parent = session.exec(
        select(DocumentChunk).where(
            DocumentChunk.document_id == doc.id,
            DocumentChunk.parent_id.is_(None),
        )
    ).first()
    child = session.exec(
        select(DocumentChunk).where(
            DocumentChunk.document_id == doc.id,
            DocumentChunk.parent_id == parent.id,
        )
    ).first()

    async def fake_hybrid_search(*args, **kwargs):
        return [
            ChunkResult(
                id=child.id,
                content=child.content,
                source="test",
                document_id=doc.id,
                score=0.9,
            )
        ]

    monkeypatch.setattr(rag._backend._store, "hybrid_search", fake_hybrid_search)
    results = await rag.search("父块内容")
    assert any(r.id == parent.id for r in results)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_rag.py::test_search_expands_child_hits_to_parent -q`
Expected: FAIL，命中子块后未返回父块

- [ ] **Step 3: 写最小实现**

修改 `app/rag/service.py` 的 `search` 方法：

```python
    async def search(
        self, query: str, top_k: int | None = None, *, backend: str | None = None
    ) -> list[ChunkResult]:
        """对查询做混合检索，命中子块时展开父块上下文并去重。"""
        top_k = top_k or settings.RAG_TOP_K
        rag_backend = self._resolve_backend(backend)
        hits = await rag_backend.retrieve(
            query, tenant_id=self.tenant_id, top_k=top_k
        )
        return await self._expand_parent_chunks(hits)

    async def _expand_parent_chunks(
        self, hits: list[ChunkResult]
    ) -> list[ChunkResult]:
        """命中子块时，追加其父块并去重。"""
        expanded: list[ChunkResult] = []
        seen: set[str] = set()
        for hit in hits:
            if hit.id not in seen:
                seen.add(hit.id)
                expanded.append(hit)
            parent = self.session.get(DocumentChunk, hit.id)
            if parent is not None and parent.parent_id:
                parent_row = self.session.get(DocumentChunk, parent.parent_id)
                if parent_row is not None and parent_row.id not in seen:
                    seen.add(parent_row.id)
                    expanded.append(
                        ChunkResult(
                            id=parent_row.id,
                            content=parent_row.content,
                            source=parent_row.source,
                            document_id=parent_row.document_id,
                            score=hit.score,
                        )
                    )
        return expanded
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_rag.py::test_search_expands_child_hits_to_parent -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/rag/service.py tests/test_rag.py
git commit -m "feat(chunking): expand child hits to parent chunks in search"
```

---

## Task 12: 配置项与文档同步，全量回归

**Files:**
- Modify: `app/core/config.py`
- Modify: `.env.example`
- Modify: `README.md`

- [ ] **Step 1: 新增配置项**

在 `app/core/config.py` 的 `RAG_CHUNK_OVERLAP` 之后新增：

```python
    RAG_CHUNK_STRATEGY: str = "structured"  # 默认切分策略；"auto" 按文档特征路由
```

- [ ] **Step 2: 同步 .env.example**

在 OCR 相关段落附近新增：

```text
# ── 文档切分策略 ──
# 可选：structured（默认，Markdown 结构）/ paragraph / sliding_window / token_aware / semantic / parent_child / auto
RAG_CHUNK_STRATEGY=structured
```

- [ ] **Step 3: 同步 README**

在 RAG 配置说明里补充「文档切分策略」小节，列出六种模式及 `RAG_CHUNK_STRATEGY` 配置。

- [ ] **Step 4: 全量回归**

Run: `pytest tests/test_rag.py tests/test_rag_backend.py tests/test_chunking.py -q`
Expected: 全部 PASS

Run: `ruff check app/rag/chunking app/rag/service.py app/rag/backend/native.py app/models/rag.py tests/test_chunking.py`
Expected: `All checks passed`

- [ ] **Step 5: 提交**

```bash
git add app/core/config.py .env.example README.md
git commit -m "docs(chunking): add chunk strategy config and docs"
```

---

## Self-Review

- **Spec 覆盖**：六种模式（Task 2/3/4/5/7/8）、模式选择（Task 6）、`Chunk` 模型（Task 1）、`DocumentChunk` 迁移（Task 9）、`ingest_text` 接入（Task 10）、父子检索（Task 11）、配置与文档（Task 12）——全部有对应任务。
- **占位符扫描**：Task 9 的 `<rev>` 需在生成迁移时替换为真实 revision id，其余无 TBD/TODO。
- **类型一致性**：`Chunk`（`text/index/parent_id/metadata`）、`ChunkParams` 字段在 Task 1 定义，Task 2~8 引用一致；`ingest_text(strategy, chunk_params)` 签名在 Task 10 与 Task 6 的 `resolve_strategy_name` 一致；`ParentChildChunkingStrategy(registry, embedding)` 构造签名在 Task 6 与 Task 8 一致。
