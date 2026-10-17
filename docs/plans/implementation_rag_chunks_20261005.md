# 文档分块详情查看（知识库逐块展示）实现说明 2026-10-05

> 性质：RAG 域知识库管理能力补强（用户直接需求，非预建任务卡）
> 分支：nightly/rag-20261005

## 1. 完成了什么

知识库文档除了列表 + 分块数统计外，现在可**查看单篇文档的分块详情**——前端点击文档展开，按块序逐块展示内容（含页码、段落、分块策略）。

- 后端新增 `GET /api/rag/documents/{document_id}/chunks`：按 `chunk_index` 升序返回该文档全部分块；每条含 `id / chunk_index / content / source / strategy / page / section / created_at`。`page`、`section` 从 `chunk_metadata` 解析（键 `page`、`section_path`）。
- 权限：`knowledge_bases:read`（与文档详情一致）；不存在/无权限 → 404。服务层 `RAGService.list_chunks` 复用 `get_document` 控制面校验。
- 前端：文档行标题与「N 分块」徽标可点击展开，展开区 `DocumentChunkList` 按块渲染「#序号 · 第N页 · 段落 · 策略 + 内容」。

## 2. 实际修改文件

| 文件 | 改动 |
|---|---|
| `app/rag/service.py` | 新增 `list_chunks(document_id, user)` |
| `app/api/routes/rag.py` | `import json`、`DocumentChunk`；`DocumentChunkOut` schema；`_chunk_out` helper；`GET /documents/{document_id}/chunks` 路由 |
| `frontend/src/types/api.ts` | `DocumentChunkOut` 接口 |
| `frontend/src/api/rag.ts` | `useDocumentChunks` hook |
| `frontend/src/pages/Knowledge.tsx` | `DocumentChunkList` 组件；`DocumentRow` 展开交互；页面 `expandedId` 状态 |
| `tests/test_rag_chunks.py` | 新增 4 测试 |

## 3. 验证命令与结果

| 命令 | 退出码 | 结果 |
|---|---|---|
| `pytest tests/test_rag_chunks.py -v` | 0 | **4 passed**（块序升序+字段齐全+数量一致 / 未知文档404 / 成员可读 / 内容覆盖） |
| `pytest tests/test_rag.py tests/test_rag_chunks.py tests/test_rag_sources.py tests/test_rag_threshold.py -q` | 1 | **40 passed**；11 errors 为 `data/pytest-tmp` 清理 PermissionError（环境性，同此前轮次） |
| `ruff check app/api/routes/rag.py app/rag/service.py` | 0 | All checks passed |
| `python -c "import app.api.routes.rag"` | 0 | import OK |
| `npm run typecheck` | 0 | 通过 |
| `npm run build` | 0 | ✓ built in 13.29s |

## 4. 明确没做的（边界）

- **未改**数据库表结构、知识库数据、索引、检索链路。
- **未做**分块内容级深链（如从对话来源跳转精确定位到某块）；本次是文档视角的逐块浏览。
- **未做**分块元数据可视化增强（如命中高亮）；保留为纯文本展示。
- **未 commit、未合并、未部署**（夜间红线；git ACL 亦拦截 AI 侧写入）。

## 5. 残余与建议

- `data/pytest-tmp` 清理 PermissionError 为环境性（Windows 文件句柄），非业务失败。
- 该功能为知识库管理增强，若需纳入任务体系，建议落一张 RAG 域卡（如「文档分块详情查看」），由用户裁定是否建卡及优先级。
