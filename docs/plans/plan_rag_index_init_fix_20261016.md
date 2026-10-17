# 对话检索器初始化故障修复计划

任务：`RAG-044`，见 `tasks.yaml`。

## 目标与现状

当前 local 向量库缺少索引登记，当前租户有 873 条 index_id 为空的分块。make_retriever 提前调用 _read_index_id，在对话分流前抛出 IndexUnavailableError，使普通问答与工具请求失败。

## 方案

创建检索器时仅绑定父块展开回调，不访问索引。实际检索保留后端身份核验与 unavailable 收敛；父块展开时重新取得当前生效索引，保留权限与索引过滤。沿用既有检索状态契约与 ADR-0008，不增加架构或放宽身份校验。

## 非目标

不登记、重建、激活或删除实际知识库数据；不改模型、配置、前端、检索排名、阈值或提示词；不覆盖用户已有 active_runs 与测试改动。

## 验收与验证

隔离 SQLite 中创建身份未知分块：检索器可以构建，实际检索返回 unavailable 且无命中；空库 no_hit；有效索引父块展开及身份保护回归通过。使用合成 Mock Embedding 仅验证链路，不声称质量提升。运行相关回归、全量 pytest、ruff check .、mypy app/。前端无契约改变，不适用；未涉及平台行为，不触发新增 Linux 专项。

## 基线

HEAD 093ceb6466b8846c7a4153593628de9bd7acb579；分支 codex/platform-batch-20261015。已有改动 app/services/active_runs.py、tests/test_sse_active_runs_review.py，保持原样。Windows，解释器 D:/DepTooL/anaconda3/envs/ai-assistant/python.exe。Ruff=0、mypy=0，日志 data/index-init-baseline-{ruff,mypy}.log；全量 pytest 结果待记录。
