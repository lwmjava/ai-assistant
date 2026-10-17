# 显式重建被去重跳过的修复计划

用户重建后的页面仍有父子错配。只读核对默认开发库中截图两条块，创建时间仍为 2026-10-05，2026-10-06 重建任务却为 success；两条子内容均不在关联父内容中。代码确认 _dedupe_or_version_existing 在重建源内容 hash 未变时返回 existing，worker 提前成功，不进入 reindex_document_in_place。

目标：显式重建必须执行重切分和向量替换，即使内容未变。普通重复上传继续去重。保留文档 ID、版本号、版本组和当前版状态。继续沿用现有权限、确认和重建失败路径，不自动执行用户库重建。

验证范围为现有 local 向量存储；测试使用 Mock Embedding 验证重建行为和关系，不证明检索质量或 Milvus 重建闭环。独立审查确认既有 reindex_document_in_place 未向外部 VectorStore 重新 add，本任务不扩大为 Milvus 治理，不宣称外部向量库重建已完成。

范围：app/rag/import_jobs.py、tests/test_rag_import_jobs.py、tasks.yaml、docs/plans/ 实现说明及 docs/reviews/ 审查说明。先添加真实 worker 回归：保存原文、摄取相同内容、损坏测试库中的父子绑定、调用重建 job；断言新块 ID、正确绑定以及文档版本不变；再次显式重建仍实际更新块。然后修复重建分支不参与内容去重。普通上传去重和历史/已删除文档行为运行现有回归。

验证：专项失败到通过、RAG 四文件、完整 pytest、Ruff、mypy，独立只读审查。旧实现说明中“点击重建即可生效”的结论由本次修复补齐实际 worker 证据。回滚代码会恢复旧行为；已重建索引不会自动还原。RAG-016 保持 in_review，不自动提交、推送或更改真实文档。
