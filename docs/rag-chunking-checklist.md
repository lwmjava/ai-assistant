# RAG 文档切分阶段 · 完成与待打磨清单

> 用途：作为「文档切分」阶段的打磨参照，标记已完成能力与剩余缺口，避免后续重复排查。
> 更新日期：2026-09-06

## 一、已完成（可视为生产可用）

### 1. 切分策略层（10 种模式）

| 模式 | 策略名 | 说明 |
|---|---|---|
| 段落切分 | `paragraph` | 按空行分段，超长段硬切 |
| 固定字符 | `fixed_chars` | 按固定窗口切分，支持 overlap |
| 滑动窗口 | `sliding_window` | 定窗口 + 步长，尾部对齐 |
| token 感知 | `token_aware` | tiktoken 优先，缺失时回退估算 |
| 递归分块 | `recursive` | 空行→换行→句子→短句→字符硬切递减 |
| 结构切分 | `structured` | 保留标题边界（复用结构化切分） |
| 语义切分 | `semantic` | 复用 embedding，相邻句相似度断句 |
| 父子文档 | `parent_child` | 子块先切，父块=子块 N 倍粗块，`parent_id` 关联 |
| 基于格式 | `format_aware` | 消费解析层 `ParsedBlock`，按结构边界切分 |
| 基于版式 | `layout_aware` | 按 `page` + `bbox` 列检测做阅读顺序重排 |

### 2. 策略调度与配置

| 能力 | 状态 | 说明 |
|---|---|---|
| 注册表 + factory | ✅ | `ChunkingRegistry` + `get_chunking_strategy` |
| `auto` 路由 | ✅ | 有标题→`structured`，长篇无标题→`recursive` |
| 请求级覆盖 | ✅ | `ingest_text(..., strategy=...)` 逐次指定 |
| 全局配置 | ✅ | `RAG_CHUNK_STRATEGY`，默认 `structured` |

### 3. 数据模型与检索配合

| 能力 | 状态 | 说明 |
|---|---|---|
| `DocumentChunk` 溯源字段 | ✅ | `parent_id` / `strategy` / `chunk_metadata` |
| idempotent 迁移 | ✅ | `_ensure_rag_schema_columns` 补齐列 |
| 落库写入溯源 | ✅ | `strategy` + `chunk_metadata`（规避 SQLAlchemy `metadata` 保留字） |
| 父子检索配合 | ✅ | 命中子块返回父块并去重 |

### 4. 解析层版式元信息

| 来源 | 块级坐标 | layout_role 细化 |
|---|---|---|
| PPT shape | ✅ 真实 `bbox` | title / note / table_header / table_cell / chart_title / chart_axis / chart_series / chart_category / chart_legend / chart_data_label |
| PDF 原生文本层 | ✅ PyMuPDF 块级 `bbox` | body |
| Tesseract OCR | ✅ TSV 行级 `bbox` | body |
| 云 OCR | ⚠️ 页级 | body |

## 二、待打磨（尚未完成 / 需增强）

### P0（功能缺口）

1. **命题分块 `proposition`（未做）**
   - 按论点 / 主题漂移切分，比 `semantic` 更强。
   - 暂以后置优化定位，需结合评测再决定是否上。

2. **云 OCR 页级缺失行坐标**
   - 云视觉模型只返回纯文本，无词 / 行级 `bbox`。
   - 扫描件走云 OCR 时 `layout_aware` 退化，无法做栏级重排。
   - 补法：引入可返回坐标的版面 OCR（供应商耦合增加）。

### P1（路由与参数调优）

3. **`auto` 路由规则偏粗**
   - 当前仅按「有无标题 + 长短文」分流。
   - 未按文档类型（PDF / PPT / 表格 / 扫描件）智能路由到 `format_aware` / `layout_aware`。

4. **语义 / 命题阈值无真实语料校验**
   - `similarity_threshold` 等当前是默认值，缺真实文档集调优。

### P2（精细度打磨）

5. **版式重排的列检测阈值**
   - `layout_aware` 用 x 轴投影空白带（页面宽 8%）判列，属启发式。
   - 对复杂版面（三栏、图文混排、表内嵌套）覆盖有限。

6. **缺真实文档评测**
   - 现为单测覆盖（策略行为正确），无企业真实文档集的召回 / 命中质量对比。

## 三、结论

- **切分主干已成熟**：10 种模式 + 调度 + 父子检索配合 + 版式重排均已就绪，可投入使用。
- **最大缺口是「命题分块」与「云 OCR 行坐标」**，但均不阻塞当前使用。
- **下一阶段更值得的投入是评测与路由细化**，用真实语料反哺 `auto` 路由与语义阈值，比继续堆模式收益更直接。