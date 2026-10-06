# RAG-016 搜索父块安全与评分修复实现说明

2026-10-06；实施依据 [计划](plan_rag_016_safe_parent_expansion_20261006.md)。用户授权修复本卡范围内的问题；修复交付时为 in_review，后经用户验收并明确批准关闭，状态更新为 done。

## 实际行为

当前租户的知识库搜索保留安全子块，并追加同文档、同租户且可见的父块。Chunk 和 Document 双重租户校验、文档软删除和版本日期校验在返回前执行。缺失或关联错误的命中不返回；父块不可用时可保留安全子块。数据库中的实际内容执行注入过滤，后端缓存内容不能覆盖已复核内容。

父块本身命中时保留自身 score、similarity、hit 来源和原始命中顺序，评分继承来源为空。仅展开的父块继承触发子块分数并显示来源。top_k 限制初始命中数量，父块展开后可增加；返回列表按命中顺序穿插父块，不声明独立重新排序。

查询采用最多两批 Chunk/Document 联查，并预加载可见性判断所需的 Document，避免逐块同步查询。`populate_existing` 刷新 Session 缓存中的旧指针和内容。仍使用同步 Session，没有迁移到 AsyncSession。

## 修改范围

- `app/rag/service.py`：读路径复核、两批查询、评分优先级及父块注入过滤。
- `app/rag/vectorstore/local.py`：可选预加载文档参数，原调用兼容。
- `app/api/routes/rag.py`、`README.md`、`docs/swagger.json`：数量和顺序语义同步。
- `tests/test_rag_safe_parent_expansion.py`：24 项安全/评分/查询回归；`tests/test_rag_search_explanation.py` 和 `tests/test_rag.py`：纠正旧继承评分与缺失命中断言。
- ADR-0001/0005、tasks.yaml、计划及独立审查：边界和证据同步。保留仓库原有修改，没有暂存或提交其他工作。

## 验证

解释器：`D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`。全部测试使用测试配置或独立内存库，没有改写开发知识库。确定性测试不需要真实 Embedding，也不作为检索质量指标。

| 实际命令（均由该解释器执行） | 结果 / 退出码 |
|---|---|
| `-m pytest tests/test_rag_search_explanation.py -q`（修复前） | 2 failed、3 passed / 1；真实评分丢失及缺失记录未关闭 |
| 安全专项修复前失败验证 | 19 failed、2 passed / 1；旧查询量 5 命中 10 次、20 命中 40 次 |
| `-m pytest tests/test_rag_safe_parent_expansion.py tests/test_rag_search_explanation.py tests/test_rag_parent_expand_lock.py -q` | 33 passed / 0；查询次数不超过两批 |
| `-m pytest tests/test_rag.py tests/test_rag_import_jobs.py tests/test_rag_backend.py tests/test_chunking.py -q` | 83 passed、1 skipped / 0 |
| `-m ruff check . --no-cache` | All checks passed / 0 |
| `-m mypy app/ --cache-dir=data/mypy-safe-expansion` | Success，172 source files / 0 |
| `docs/export_swagger.py` | 已导出 / 0 |
| 项目环境 `yaml.safe_load`、ID、依赖、状态和 OpenAPI 对账 | 183 条、无重复 ID/悬空依赖、状态 in_review、搜索描述一致 / 0 |

全量命令 `-m pytest -q --basetemp=data/pytest-tmp/safe-expansion-full -ra`：634 passed、2 skipped、1 warning，退出码 0，耗时 154.02 秒。跳过的是缺少可选 mcp 依赖和 llamaindex extra，并非本次业务断言失败。报告保存在 `data/rag016-safe-full.txt`（忽略文件）。独立只读审查未发现阻断项，见 `docs/reviews/2026-10-06-RAG016父块展开安全与评分独立审查.md`。

## 验收与回滚

无需重新上传或重建，服务加载更新后直接重新检索。页面上仅展开的父块显示继承来源；父块自身命中显示“命中父块”，保留自身得分且没有继承提示。安全和版本失败路径以专项测试取证，不能靠页面分数猜测权限正确性。

本次不修改切分策略或持久化数据。407 块、孤立标题/分隔线和相同文本块的切分质量由 RAG-021 处理；uploader 隔离由 RAG-026、对话父块预算由 RAG-030 承接。未开展新的 Embedding 质量实验或 Milvus 重建，也未把本次验证写成生产质量结论。

读取修复回滚不需要数据库迁移或索引重建，但直接撤销安全校验会恢复已知越权和评分缺陷，不能当作安全降级方案。此前父子映射/显式重建的索引回滚仍按其独立说明处理。

## 用户验收与关闭记录

2026-10-06，用户报告在本地运行专项命令得到 33 passed，并提供四张页面截图：#3 子块分数 0.0317，#4 自身命中父块保留 0.0313 且无继承提示；#5 子块和 #6 扩展父块均为 0.0299，扩展来源及评分继承来源与子块 ID 一致；#1 自身命中父块与 #2 子块 ID 关系正确。初始命中上限 5、最终展示 6 条，数量提示符合批准契约。

用户随后明确要求“关闭 RAG-016，并提交当前仓库的所有文件”。独立审查、自动化验证及本卡页面验收已齐，更新 tasks.yaml 为 done；本次本地提交包含仓库全部未忽略改动（包括此前已有修改），不推送、合并或部署。切分碎片问题保留在 RAG-021，不因本卡关闭标为完成。
