# RAG-037 检索状态与最终拒答契约 — 实施计划（2026-10-07）

卡契约：`tasks.yaml` RAG-037（P0，depends_on: RAG-023(done)）
分支：`rag-batch-20261007`（基线 `42bf7e5`）

## 1. 卡的交付物与验收（原文）

- deliverables：`ok/no_hit/below_threshold/unavailable 状态；入口/后端矩阵；明确少返回策略。`
- non_goals：`不自动补位改变排名、不做无界兜底生成。`
- acceptance：`同步/流式故障明确提示不冒充知识回答；低分最终回复验收；补位仅评测不自动采纳。`
- allowed_paths：`app/rag/`、`app/services/chat_service.py`、`app/agents/`、`app/api/routes/`、`frontend/src/`、`tests/`、`docs/`、`tasks.yaml`

## 2. 现状复现（开工实测，非静态推测）

| # | 事实 | 证据 |
|---|---|---|
| F-1 | 检索异常在**自研管线**里没有任何 try/except，`backend.retrieve` 抛异常直接冒到管线级兜底 → 请求失败 | `app/agents/pipeline.py:417-424` `_fill_retrieval` 无异常处理；兜底在 `pipeline.py:518` 与 `745` |
| F-2 | 检索异常在**短路径**里被静默吞掉：`except Exception: snippet = ""`，随后 `extra = "## 知识库\n没有可用的检索结果。"` → 模型直接用自身知识作答，用户完全看不到「检索故障」 | `app/agents/fast_path.py:92-100` |
| F-3 | 全低分拒答语 `RAG_REFUSE_MESSAGE` 是**裸字符串**返回，不带 `[UNTRUSTED_SOURCE]` 围栏，也不带任何指令标记 → 模型可自由忽略它并照常作答，拒答只是「期望」不是「契约」 | `app/rag/retriever.py:87-92` 返回 `settings.RAG_REFUSE_MESSAGE`；对照 `format_context`（`retriever.py:27-39`）才会加围栏 |
| F-4 | 除 `retriever.py` 外无任何代码引用 `RAG_REFUSE_MESSAGE`；不存在 `ok/no_hit/below_threshold/unavailable` 任何一种状态标识 | `grep -rn "RAG_REFUSE_MESSAGE\|below_threshold\|no_hit" app/` 仅命中 `app/core/config.py:115` 与 `app/rag/retriever.py:90` |
| F-5 | 后端工厂把 langchain / llamaindex 缺依赖**静默降级为 native**，只写 warning，调用方（含 HTTP `?backend=`）拿不到实际生效后端 | `app/rag/backend/factory.py` `get_rag_backend` 的两个 `except ImportError` 分支 |
| F-6 | 少返回（部分命中被阈值剔除）只有一行 `logger.info`，没有结构化计数，调用方无从得知 top-k 被削了几条 | `app/rag/retriever.py:77-83` |

### 复现结论

`no_hit` / `below_threshold` / `unavailable` 三种路径**都没有形成最终回复契约**：
- `no_hit`：返回空串，短路径额外补一句「没有可用的检索结果」，管线路径则退化成「未接入外部检索」提示（`pipeline.py:369`），两条入口措辞不一致。
- `below_threshold`：拒答语进了上下文，是资料不是指令，模型可以忽略 → 「低分最终回复」无法验收。
- `unavailable`：一条入口 500（管线）、一条入口静默冒充（短路径）→ 直接命中 acceptance 第 1 条。

## 3. 契约设计

### 3.1 状态枚举

`app/rag/retrieval_status.py`（新增）：

```python
class RetrievalStatus(str, Enum):
    OK = "ok"                        # 有命中且至少一条通过阈值
    NO_HIT = "no_hit"                # 检索成功但零候选
    BELOW_THRESHOLD = "below_threshold"  # 有候选但全部低于 RAG_MIN_SIMILARITY
    UNAVAILABLE = "unavailable"      # 检索未完成（嵌入/向量库/后端异常）
```

判定顺序（唯一，写在代码里并由测试钉住）：
1. `backend.retrieve` 抛异常 → `UNAVAILABLE`（记 `error_type`）
2. 返回列表为空 → `NO_HIT`
3. 过滤后为空（有候选） → `BELOW_THRESHOLD`
4. 否则 → `OK`；`kept < results` 时为**少返回**（`dropped_by_threshold = len(results) - len(kept)`）

### 3.2 少返回策略（明确，不自动补位）

- 少返回**只减不补**：被阈值剔除的候选不再回填，也不动排名顺序（对齐 non_goals「不自动补位改变排名」）。
- 少返回时 `status` 仍为 `OK`，但 `dropped_by_threshold > 0`，由 `RetrievalOutcome` 结构化暴露并记录日志。
- 注入块剔除（`drop_injected_chunks`）与阈值剔除是两件事，计数分开，不混算。

### 3.3 最终回复契约（确定性提示，非「期望」）

非 `OK` 状态时，入口把一句**指令**送进模型（不是资料、不加 `[UNTRUSTED_SOURCE]` 围栏）：

| 状态 | 是否强制披露 | 提示语来源 | 动作 |
|---|---|---|---|
| `OK`（含少返回） | 否 | — | 正常注入上下文 |
| `NO_HIT` | 是 | `RAG_NO_HIT_NOTICE`（新增，默认开） | 要求模型声明「未使用知识库内容」 |
| `BELOW_THRESHOLD` | 是 | `RAG_REFUSE_MESSAGE`（复用既有） | 要求模型传达未检索到足够相关资料 |
| `UNAVAILABLE` | 是 | `RAG_UNAVAILABLE_NOTICE`（新增，默认开） | 要求模型明确说明检索服务不可用，不得把自身知识包装成知识库结论 |

- 总开关 `RAG_STATUS_NOTICE_ENABLED`（默认 True），可独立关闭回退到改造前行为（对齐 risk.rollback「规则/配置独立关闭或回退」）。
- **不做无界兜底生成**：契约只规定「必须披露」，不规定模型必须编出答案，也不改变生成长度/采样参数。

### 3.4 入口 × 后端矩阵（交付物之一，落 `docs/rag/检索状态与拒答契约.md`）

| 入口 | 后端 | 异常时现状 | 改造后 |
|---|---|---|---|
| 同步对话（自研管线） | native / langchain / llamaindex | 冒泡 → 请求失败 | `UNAVAILABLE` + 披露指令，请求成功 |
| 流式对话（自研管线） | 同上 | 冒泡 → 流中断 | `UNAVAILABLE` + 披露指令，流完整结束 |
| 同步/流式（短路径 RAG） | 同上 | 静默吞掉 → 冒充知识回答 | `UNAVAILABLE` + 披露指令 |
| `POST /api/rag/search` | 请求级 `?backend=` 回退 | 静默降级 native，无提示 | 响应头 `X-RAG-Backend` 暴露实际生效后端；`X-Retrieval-Status` 暴露状态 |
| 关闭 `RAG_STATUS_NOTICE_ENABLED` | 任意 | — | 状态仍记录与暴露，但不注入披露指令 |

## 4. 改动清单

| 文件 | 改动 |
|---|---|
| `app/rag/retrieval_status.py` | 新增：`RetrievalStatus`、`RetrievalOutcome`、`notice_for()`、`directive_for()` |
| `app/core/config.py` | 新增 `RAG_STATUS_NOTICE_ENABLED`、`RAG_NO_HIT_NOTICE`、`RAG_UNAVAILABLE_NOTICE` |
| `app/rag/retriever.py` | `retrieve` 内捕获异常 → `UNAVAILABLE`；记录 `last_status` / `last_outcome`；非 OK 返回指令块而非裸拒答句；少返回结构化计数 |
| `app/agents/pipeline.py` | `AgentState` 增 `retrieval_status` / `retrieval_notice`；`_fill_retrieval` 从 outcome 取值（不解析字符串）；`_build_act` / `_build_respond` 追加「检索状态要求」 |
| `app/agents/fast_path.py` | 短路径改为读 outcome，把披露指令送进 system，而非塞进 untrusted `extra` |
| `app/api/routes/rag.py` | `POST /api/rag/search` 输出 `X-Retrieval-Status` / `X-RAG-Backend` 响应头 |
| `tests/test_rag_037_retrieval_status.py` | 新增专项测试 |
| `docs/rag/检索状态与拒答契约.md` | 新增契约与矩阵文档 |
| `tasks.yaml` | RAG-037 状态与验收记录 |

## 5. 验证与证据要求

遵循 `docs/ai-prompts/skills/module-batch-delivery/references/acceptance-gates.md`：
- 先写失败测试 → 实现 → 转绿 → **变异测试**（故意改坏代码证明测试会红）→ 还原。
- 证据是「故障注入后入口的真实行为」，不是测试条数、不是报告格式。

命令（每次换新的 RUN 名）：

```bash
RUN=rag037a; rm -rf data/pytest-tmp/$RUN; mkdir -p data/pytest-tmp/$RUN/db
DATABASE_URL="sqlite:///./data/pytest-tmp/$RUN/db/test.db" \
  D:/DepTooL/anaconda3/envs/ai-assistant/python.exe -m pytest tests/ \
  -k "rag or chunk or context or embedding" -q --basetemp data/pytest-tmp/$RUN/tmp
D:/DepTooL/anaconda3/envs/ai-assistant/python.exe -m ruff check app/
D:/DepTooL/anaconda3/envs/ai-assistant/python.exe -m mypy app/rag/
```

## 6. 不做的（non_goals 与授权边界）

- 不改排名、不补位、不引入重排（non_goals）。
- 不重建真实索引、不推送、不合并、不部署。
- 不在本卡处理 deadline / 重试 / 取消（属 RAG-038，且 RAG-038 明确 blocked_by RAG-037）。
