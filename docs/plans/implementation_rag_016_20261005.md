# RAG-016 父块展开（方案 A）锁定验收 — 实现说明 2026-10-05

> 卡：RAG-016（P1，L1）
> 分支：nightly/rag-20261005

## 1. 完成了什么

把"父块展开（方案 A）"锁定为验收基线：命中子块时**保留子块**并**追加父块**；父子同时命中时**去重**；父块**继承子块的 score 与 similarity**。

- 修正 `_expand_parent_chunks` 中 `similarity=hit.similarity` 的续行缩进错乱（4 空格 → 对齐 24 空格，ruff 合规）。
- 补强验收测试：子块保留 + 父块追加 + 评分/相似度继承；父子同命中去重。

## 2. 实际修改文件

| 文件 | 改动 |
|---|---|
| `app/rag/service.py` | `_expand_parent_chunks` 452 行 `similarity` 缩进对齐 |
| `tests/test_rag.py` | 新增 `test_parent_expand_keeps_child_and_inherits_score`、`test_parent_expand_dedupes_when_parent_child_both_hit` |
| `docs/plans/plan_rag_016_20261005.md` | 实现计划 |

## 3. 验证命令与结果

| 命令 | 退出码 | 结果 |
|---|---|---|
| `pytest -k 'parent' -v` | 0 | **5 passed**（含 2 个新增验收测试：子块保留+评分继承 / 去重） |
| `ruff check app/rag/service.py tests/test_rag.py` | 0 | All checks passed |
| `mypy app/rag/service.py` | 1 | **service.py 无新增错误**；3 errors 均为 langchain_backend/llamaindex_backend 存量 LSP override（REL-003 基线，非本卡引入） |

## 4. 验收对照（卡 acceptance）

- [x] 命中带子 parent_id 的子块时，返回集含对应父块，且**同时保留子块**（`child.id in ids` 断言）。
- [x] 不以「结果仅含父块」为合格线（子块保留断言覆盖）。
- [x] 父子同时命中时父块去重（`ids.count(parent.id) == 1`）；父块继承 score/similarity（`pytest.approx` 断言）。

## 5. 明确没做的（Non-goals 边界）

- 未做"只返回父块"模式（卡 non_goals）。
- 未做检索子仅喂母父的配置项（卡 non_goals）。
- 未改分块策略、embedding、RRF 融合、检索链路参数；未动知识库数据/索引。
- 未 commit、未合并、未部署（夜间红线；git ACL 亦拦截 AI 侧写入）。

## 6. 残余与说明

- mypy 存量 LSP override 错误（langchain/llamaindex backend）为既有基线，与本卡无关，留待独立治理。
- 本卡仅锁定既有行为 + 补测试，无运行时行为变更。
