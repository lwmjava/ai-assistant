# 父块展开安全与真实评分修复计划

2026-10-06 用户明确授权修复属于 RAG-016 的问题。根因审查见 docs/reviews/2026-10-06-RAG016分块数量与展开安全复核.md。本方案基于已确认方案 A：保留子块、补父块、按块 ID 去重。

## 目标与实现范围

- app/rag/service.py：命中行及文档一次批量 join；候选父块及文档一次批量 join。SQL 强制 chunk/document tenant 与当前租户一致、文档未删除；默认仅当前版本；开启日期过滤时复用既有可见性规则。父块还必须与子块同 document_id，缺失记录失败关闭，不返回未经授权的后端内容。复用检索注入检测检查最终实际返回内容。
- app/rag/vectorstore/local.py：visible_chunks_with_status 可接收预取文档，复用既有规则而不逐个 session.get；原调用保持兼容。
- app/api/routes/rag.py、README.md、docs/swagger.json：说明初始命中上限、追加父块与分组呈现；不宣称最终混合列表是统一重新评分排序。
- tests/test_rag_safe_parent_expansion.py：跨租户/跨文档错指、块与文档租户不一致、缺失、删除/历史/过期/预告、注入父块、直接命中评分/相似度优先、重复父块和 SQL 次数。
- tests/test_rag_search_explanation.py、tests/test_rag.py：纠正旧继承评分断言；说明字段与 API 映射继续覆盖。其余边界回归不降低。
- docs/adr/0001-knowledge-base-permission.md、docs/adr/0005-rag-adaptive-chunking-context-budget.md、tasks.yaml 和实现/审查记录：同步本轮权限保护、兼容、回滚和验证。

直接命中父块优先：先识别所有通过校验的真实命中，遍历时保留它们原顺序和 score/similarity；若父块已在真实命中集合，子块不另行创建覆盖它的扩展副本。仅未命中的父块追加一次，继承触发子块分数、标注 parent_expansion。状态取授权文档实际可见性，不盲目复制后端 status。

SQL 查询次数最多两批，不随 top_k 逐块增长；复用现有同步 Session，不引入异步 ORM 或跨线程共享 Session。不宣称事件循环全部数据库访问已异步化。查询使用 populate_existing 刷新已有 identity map 中的数据。

## 非目标

不改 paragraph/parent_child 切分、87/407 数量、标题/分隔线/结构保护、资源 ACL、uploader 整体检索隔离（RAG-026）、对话父块预算（RAG-030）或 Milvus 重建。当前 tenant 模式的权限、父子一致性和可见性必须完成。本卡不通过截断父块强行把返回数量压回 top_k。

## 验证与回滚

先写真实 SQL 与合成安全 Evaluation 用例，确认旧实现失败；最小实现后跑专项、RAG 四文件、完整 pytest、ruff check .、mypy app/，导出 Swagger，检查 YAML/ID/依赖。使用项目 conda D:/DepTooL/anaconda3/envs/ai-assistant/python.exe 和测试 DB data/test_ai_assistant.db；不修改真实数据，不调用真实 Embedding。

安全/契约变更无迁移、无需重建。回滚代码恢复旧检索行为，也会恢复已知安全缺口，不能把旧路径作为安全降级兜底。查询失败沿用既有异常，不绕过校验返回父块。独立只读审查及页面业务验收未完成前保持 in_review；不自动提交、推送、合并或部署。
