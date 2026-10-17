# 显式重建同内容跳过问题实现说明

2026-10-06。用户重建后截图仍显示错误父子关系，沿用本卡已批准修复范围继续定位。计划见 plan_rag_016_explicit_rebuild_20261006.md。

## 原因与修复

只读检查截图中两条实际开发库分块，创建时间仍为 2026-10-05；重建任务于 2026-10-06 返回 success，两条子内容都不属于关联父内容。app/rag/import_jobs.py 的 _dedupe_or_version_existing 对相同原文 hash 的 reparse 返回 existing，worker 因此提前成功，没有执行新切分逻辑。

现在显式重建返回 deduplicated=false，进入既有 reindex_document_in_place，重新应用当前切分和本地索引逻辑。文档 ID、版本组、版本号和当前版状态保持。普通上传去重分支、权限检查、已删除文档拒绝和失败处理未改。前一轮只验证切分而漏测此短路，本轮补齐真实 worker 回归，修订“原来点击重建就能更新旧关系”的过早判断。

修改：app/rag/import_jobs.py、tests/test_rag_import_jobs.py、tasks.yaml 交付范围以及本计划、实现说明和独立审查记录。测试保存原文并摄取相同 hash，故意破坏测试库父子关系，连续两次调用真正的重建 worker；断言新块 ID、正确父子归属、内容 hash 和文档版本身份。

## 实际验证

解释器 D:/DepTooL/anaconda3/envs/ai-assistant/python.exe；测试库 data/test_ai_assistant.db。真实开发库仅只读核对，没有执行重建或修改。

- 修复前 `python -m pytest tests/test_rag_import_jobs.py -k explicit_reparse_unchanged -q --basetemp=data/pytest-tmp/rebuild-red`：1 failed、19 deselected，退出 1；旧块 ID 未更新。
- 修复后 `python -m pytest tests/test_rag_import_jobs.py -k 'reparse or dedup' -q --basetemp=data/pytest-tmp/rebuild-green`：4 passed、16 deselected，退出 0。
- `python -m pytest tests/test_rag.py tests/test_rag_import_jobs.py tests/test_rag_backend.py tests/test_chunking.py -q --basetemp=data/pytest-tmp/rebuild-rag`：83 passed、1 skipped，退出 0；data/rebuild-rag.txt。
- `python -m pytest -q --basetemp=data/pytest-tmp/rebuild-full`：610 passed、2 skipped、1 warning，234.18 秒，退出 0；data/rebuild-full.txt。
- `python -m ruff check . --no-cache`：All checks passed，退出 0。
- `python -m mypy app/ --cache-dir=data/mypy-parent-mapping`：172 source files 无错误，退出 0。
- YAML 全量解析、183 唯一 ID 和依赖存在性、git diff --check：退出 0。

完整测试两项跳过来自可选 llama_index.core / mcp 未安装。此次无前端改动，不重复其已通过的构建。Mock 仅用于验证本地流程，不宣称真实检索质量。

## 验收、限制与回滚

后端加载修复后，用户对测试文档再次明确重建；确认分块 ID 更新、子块关联父块包含其内容，再确认页面验收。当前保持 in_review，未自动重建、提交、推送、合并或部署。

独立审查确认 local 范围通过。既有 Milvus 就地重建只有删除外部向量而缺少重新写入，本轮不宣称 Milvus 重建闭环已验证；同内容显式重建也会进入该既有路径。应由 Milvus 完整集成任务处理，不在此顺手扩大范围。

代码回滚恢复原同 hash 短路行为，已生成新索引不会自动还原。已有索引的再次重建必须明确授权。
