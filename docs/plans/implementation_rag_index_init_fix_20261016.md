# 对话索引初始化故障修复说明

对应任务：RAG-044；计划：[实现计划](plan_rag_index_init_fix_20261016.md)。

## 实际行为

开启 RAG 的对话创建检索器时不再提前解析索引，因此身份未知的历史分块不再在意图分流之前使普通问答、工具路径整体失败。实际检索仍经过原有索引身份验证；索引不可用时沿用 unavailable 状态与现有披露规则，不把故障冒充 no_hit。父块展开前重新核对当前生效索引。

修改 app/rag/service.py 的 make_retriever 与 _dialog_parent_expander；新增 tests/test_rag_index_init.py，合成 native/langchain 两种后端的历史分块、空库与完整流式管线验证。用户已有 app/services/active_runs.py 与 tests/test_sse_active_runs_review.py 修改保持原样。

## 验证

Windows，Python 3.12.0；解释器 D:/DepTooL/anaconda3/envs/ai-assistant/python.exe，以下命令均以该解释器执行 `-m`。pytest 8.4.2、ruff 0.8.4、mypy 1.14.1、SQLModel 0.0.22、langchain-core 1.6.2、langgraph 1.0.10。

基线 HEAD 093ceb6466b8846c7a4153593628de9bd7acb579，分支 codex/platform-batch-20261015；基线 Ruff=0、mypy=0。首次全量基线临时目录 data/pytest-tmp/run 权限失败，已中断，不能记为通过；单文件换新 basetemp 后 5 passed。第二次全量基线未等待汇总即中断，不作为完整基线证据。修复前新增两例实际失败，错误为 make_retriever 提前抛 IndexUnavailableError，data/index-init-red.log（退出 1）。

修复后专项命令：`pytest tests/test_rag_index_init.py tests/test_rag_037_retrieval_status.py tests/test_rag_030_dialog_parent_assembly.py tests/test_rag_032_index_identity.py tests/test_rag_cache.py --basetemp=data/index-init-focused-tmp -q`，环境 DATABASE_URL=sqlite:///./data/index-init-focused.db；120 passed / 1 warning，退出 0，data/index-init-focused.log。Mock Embedding dim=64 与合成模型仅证明链路、状态与隔离，不证明真实模型回答或检索质量。

`ruff check .` 首次缓存目录权限错误退出 2；设置 RUFF_CACHE_DIR=data/index-init-ruff-cache 后相同命令退出 0，data/index-init-final-ruff2.log。`mypy app/` 退出 0，data/index-init-final-mypy.log。未放宽配置或排除测试。

最终全量命令：`pytest --basetemp=data/index-init-final-tmp`，DATABASE_URL=sqlite:///./data/index-init-final.db；退出 1，2529 passed / 1 failed / 3 skipped / 3 warnings，619.22 秒，日志 data/index-init-final-pytest.log。失败项 tests/test_rag_039_acceptance.py::test_partial_batch_is_running_until_all_children_finish：预期前两项一成功一失败，实际两成功。该文件候选版本第一次复跑 17 passed / 1 failed（data/index-init-import-compare.log），第二次复跑 18 passed（data/index-init-import-recheck.log）；HEAD service 通过只读 git show 加载器注入、保留正常导入顺序的对照 18 passed（data/index-init-original-loader.log）。表现不稳定，不能据此宣称全量通过或已经确定失败根因；不扩大本卡修改批量导入。

最终源码及既有改动指纹与依赖版本见 data/index-init-inputs.json，检查后复核指纹未变化。前端源码/API 契约未改，前端检查不适用；没有新增进程、信号、文件系统或平台分支，Linux 专项不适用，未运行远程 CI。

## 边界与验收

不登记、重建、激活或删除实际 873 条历史分块；原知识库仍不可用，恢复需先证明向量身份或明确批准真实数据重建。不关闭 RAG、不改默认向量库或模型、不降低索引保护。未使用用户登录态发起真实模型请求；需用户页面确认普通问答和工具调用。全量存在未解决的不稳定失败、独立审查与人工业务验收未完成，任务保持 in_review，不代表可发布；没有提交或推送。

回滚只需恢复本任务 app/rag/service.py 变更，不涉及数据回滚。
