# 知识库检索父子关系展示实现说明

日期：2026-10-06。用户批准补充页面验收能力；关联 RAG-016。计划见 plan_rag_016_search_explanation_20261006.md。状态 in_review，尚未宣称人工页面验收通过，未提交、推送、合并或部署。

## 实际行为

有知识库读取权限的用户检索后，可查看块 ID、父块 ID，以及“命中子块 / 命中父块 / 扩展父块 / 命中块（未知）”标记。扩展父块显示触发它的子块 ID；所有继承子块评分的父块都显示评分来源 ID，包括本身也在检索命中列表的父块。

命中上限说明改为“检索命中上限，父块展开后可能增加”。没有记录或无法判断的关系显示未知，不按内容长度或分数猜测父子关系。旧后端响应缺新字段时，前端明确显示未知，正文继续展示。

API 保留既有字段，新增 chunk_id、parent_id、chunk_kind、retrieval_origin、expanded_from_chunk_id、score_inherited_from_chunk_id。Service 根据记录关系和展开流程填充，不增加数据表字段或迁移。按 ID 去重、排序、score、similarity、租户权限和原 top_k 含义保持原有行为。

## 修改文件

- app/rag/vectorstore/base.py：结果对象补充展示元数据。
- app/rag/service.py：搜索父块展开阶段填写真实关系和评分继承来源。
- app/api/routes/rag.py：响应模型和映射。
- frontend/src/types/api.ts、frontend/src/pages/Knowledge.tsx：兼容类型、标签、完整 ID、评分来源和数量说明。
- tests/test_rag_search_explanation.py：合成契约用例，含不同父子分数、双重命中顺序、缺失记录和响应映射。
- README.md、docs/swagger.json、tasks.yaml：同步接口说明、导出和批准范围；任务不标 done。

## 实际验证

后端解释器 D:/DepTooL/anaconda3/envs/ai-assistant/python.exe，测试使用 data/test_ai_assistant.db。以下为实际执行结果：

| 命令 | 结果 / 退出码 |
|---|---|
| python -m pytest tests/test_rag_search_explanation.py -q（实现前） | 5 failed，缺新增字段，1 |
| python -m pytest tests/test_rag_search_explanation.py tests/test_rag_parent_expand_lock.py -q --basetemp=data/pytest-tmp/search-explanation-final | 最终 9 passed，0 |
| python -m pytest tests/test_rag.py tests/test_rag_import_jobs.py tests/test_rag_backend.py tests/test_chunking.py -q --basetemp=data/pytest-tmp/search-explanation-rag | 79 passed、3 failed、1 skipped，1 |
| python -m pytest -q --basetemp=data/pytest-tmp/search-explanation-full | 602 passed、2 skipped、1 warning，0；报告 data/search-explanation-full.txt |
| python -m ruff check . --no-cache | 最终 All checks passed，0 |
| python -m mypy app/ --cache-dir=data/mypy-search-explanation | 最终 172 文件无错误，0 |
| npm.cmd run typecheck / npm.cmd run build（frontend） | 最终均 0；直接 npm 被本机 npm.ps1 执行策略拦截，未修改策略 |
| python docs/export_swagger.py | 最终导出成功，0 |
| yaml.safe_load + 唯一 ID / 依赖存在性 | 183 条，0 |
| git diff --check | 0 |

完整 pytest 在独立审查补充评分来源字段前完成；补充后重跑专项测试、Ruff、mypy、前端检查和 Swagger，未重复完整套件。

四文件独立运行的 3 个失败是 test_upload_accepts_file_at_configured_size_limit、test_upload_stores_source_file_and_downloads_it、test_download_missing_source_file_returns_404，断言源文件落盘或删除失败；完整套件中这些用例通过。此次未修改上传、去重和源文件保存逻辑；独立运行与完整套件的差异尚未在本任务修复，不能把四文件结果写为全绿。

两个完整套件跳过来自缺少 llama_index.core / mcp 的模块级 importorskip。确定性 Mock 用例只证明关系元数据和 UI 契约，不证明真实 Embedding 检索质量。

## 人工验收与回滚

后端加载更新后刷新前端，对已有 parent_child 文档重新检索即可，不需要重新上传。检查子块的父块 ID 等于对应父块的块 ID；扩展父块的来源 ID 等于触发子块的块 ID。同一块 ID 只出现一次，内容相同但 ID 不同可以保留。父块 score 继承来源有明确提示。

尚未由 Agent 操作浏览器完成真实页面验收；等待用户验收，不伪关闭。回滚本次响应字段和页面展示即可，无索引、数据库或原文件迁移。未做源文件下载按钮、结构渲染、智能切分或对话父块预算。
