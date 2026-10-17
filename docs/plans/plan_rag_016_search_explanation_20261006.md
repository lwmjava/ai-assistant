# 检索父子关系展示补充计划

用户于 2026-10-06 明确授权补充 RAG-016 页面验收能力，范围扩展到检索 API、结果类型和知识库页面。保留仓库已有改动，不关闭任务、不提交未验收内容。

## 目标与现状

当前 search 保留命中并追加父块，按块 ID 去重，追加父块继承分数；页面缺块 ID、关系和结果来源，且将 top_k 称为返回条数。

## 方案与文件归属

- 主 Agent：app/rag/service.py、app/rag/vectorstore/base.py、app/api/routes/rag.py、tests/test_rag_search_explanation.py、README.md、docs/swagger.json、tasks.yaml 和本计划及实现说明。
- 前端子 Agent：frontend/src/types/api.ts、frontend/src/pages/Knowledge.tsx。
- 只读审查 Agent：最终相关 Diff，无写入权限。

响应增加 chunk_id、parent_id、chunk_kind（parent/child/unknown）、retrieval_origin（hit/parent_expansion）、expanded_from_chunk_id。关系来自数据库，来源来自展开流程，不按文本长度或分数推断。父块自身也命中时优先标为命中父块，避免错误称为仅扩展。旧字段保留，无迁移。

独立审查补充：score_inherited_from_chunk_id 单独记录继承评分的子块，即使父块本身也命中仍显示评分来源；未知块记录的父子关系显示未知。

## 非目标与失败回滚

不改变权限、排序、分数、去重、top_k 或切分策略；不做源文件下载和结构渲染。数据库缺记录时标未知块，不编造关系。回滚本次新增字段和页面展示即可，无数据写入、重建索引或迁移。

## 验收与验证

确定性契约覆盖子块与父块关系、父块追加来源、直接父块命中优先、ID 去重和未知记录。页面显示块 ID、父块 ID、扩展来源子块 ID；数量说明为“检索命中上限，父块展开后可能增加”。运行专项测试、RAG 四文件回归、ruff check .、mypy app/、前端 typecheck/build，并更新 Swagger。实际命令及结果写入实现说明，人工页面验收前不标 done。
