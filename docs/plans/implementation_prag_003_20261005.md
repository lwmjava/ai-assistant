# PRAG-003 实现说明（Citation 产品化：结构化溯源输出，2026-10-05）

> 任务卡：PRAG-003（P1，L2，risk: api-response-shape-changed）
> 状态：实现完成，验收条件满足；待人工审查后关闭
> 分支：nightly/rag-20261005

## 1. 完成了什么

答案引用从"文件名+页码+段落"升级为**结构化、可定位、含摘录**的引用，前端可展示摘录并跳转到知识库定位来源文档；OpenAPI 响应结构随 `SourceOut` 同步更新。

- 每条来源新增 `excerpt`（摘录原文，折叠空白后截断 240 字符）、`chunk_id`（分块 id，可定位到具体分块）、`document_id`（所属文档 id，跳转定位基础）。
- 去重粒度从 `(filename,page,section)` 细化到含 `chunk_id`（同页多分块各成一条引用，摘录即该分块原文）。
- 前端来源项从纯文本改为卡片：标题可点击跳 `/knowledge?doc=<document_id>`，并展示摘录原文。
- 知识库页支持 `?doc=<id>` query：进入后滚动并高亮对应文档，完成"定位来源"闭环。

## 2. 实际修改文件

| 文件 | 性质 |
|---|---|
| `app/rag/service.py` | `sources_from_hits` 追加 excerpt/chunk_id/document_id；去重到分块粒度；新增 `_source_excerpt` |
| `app/api/routes/chat.py` | `SourceOut` 追加 excerpt/chunk_id/document_id（可空，向后兼容） |
| `frontend/src/types/api.ts` | `SourceRef` 同步三字段 |
| `frontend/src/components/chat/MessageList.tsx` | `SourceNotes` 改卡片：跳转链接 + 摘录 |
| `frontend/src/pages/Knowledge.tsx` | 支持 `?doc=` query：高亮 + 滚动定位来源文档 |
| `tests/test_rag_sources.py` | 新增 4 个测试 |

## 3. 验证命令与结果

| 命令 | 退出码 | 结果 |
|---|---|---|
| `pytest tests/test_rag_sources.py -v` | 0 | **4 passed**（字段齐全 / 摘录截断 / SourceOut schema / 分块去重） |
| `pytest -k 'citation or rag' -q` | 1 | **82 passed**，2 skipped；46 errors 为 `data/pytest-tmp` 清理 PermissionError（环境性，同 NFR-002/PRAG-004） |
| `ruff check`（service.py + chat.py + test） | 0 | All checks passed |
| `mypy app/rag/service.py` | — | **service.py 0 新增错误**（修复 seen 去重集合注解到 4 元组）；其余 31 errors 为其他文件存量基线（REL-003） |
| `npm run typecheck` | 0 | tsc --noEmit 通过 |
| `npm run build` | 0 | ✓ built in 9.21s |

## 4. 验收对照（卡 acceptance）

- [x] 接口返回可解析的引用数组，每条含 filename/page/section/excerpt/chunk_id/document_id（SourceOut schema 测试断言）。
- [x] 前端展示引用摘录，并可点击定位到知识库对应文档（`/knowledge?doc=` 高亮+滚动）。
- [x] 本卡同步更新 OpenAPI（`SourceOut` 字段变化由 FastAPI 自动生成；契约测试断言新字段）。

## 5. 明确没做的事（Non-goals 边界）

- **未做**引用可信度评分（卡 non_goals）。
- **未改**检索链路参数（卡 non_goals）。
- **未依赖**重排实验结论（只改响应形状）。
- **未建**独立"文档分块详情页"（用 `doc=<id>` 在知识库页最小定位；深链分块详情留后续）。
- **未改**数据库表结构、知识库数据/索引。
- **未动** `evals/reports/rag-v0.1-baseline-20260919.json`、`.env`。
- **未 commit、未合并、未部署、未推送**（夜间红线）。

## 6. 残余风险与说明

- **api-response-shape-changed（L2）**：新增字段可空，旧客户端兼容；回滚=保留旧字段、回退提交。
- **前端定位为最小实现**：跳转到知识库页高亮文档，不做分块级深链；若后续要"精确定位到分块"，需独立文档详情视图（留后续）。
- **pytest-tmp 环境 errors**：46 errors 均为测试临时目录清理 PermissionError，非业务失败（可删除 data/pytest-tmp 后复跑确认）。

## 7. 次日人工审查清单

1. 复核 `pytest tests/test_rag_sources.py`（4 passed）与 `pytest -k 'citation or rag'`（82 passed）。
2. 实机验证：检索型问答返回引用数组含 excerpt/chunk_id/document_id；前端来源卡片显示摘录、点击跳知识库页高亮文档。
3. 确认 OpenAPI 的 SourceOut schema 已含新字段（/docs 或契约测试）。
4. 确认 service.py seen 注解 4 元组、`_source_excerpt` 截断逻辑符合预期。
5. 决定 PRAG-003 关闭；并决定是否把"前端分块深链定位"作为后续卡。
