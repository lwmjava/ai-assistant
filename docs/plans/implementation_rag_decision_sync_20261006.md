# RAG 裁决同步实现说明

日期：2026-10-06。计划：[裁决同步计划](plan_rag_decision_sync_20261006.md)。本说明描述文档和任务契约实际完成内容，不表示相关业务卡已实现。

## 实际完成

用户批准此前裁决建议后，后续实施者可以据已批准的权限、索引、预算/来源、评测制定方法及摘要生命周期方向编写单卡计划和实现；不再重复询问同一方向。具体缺失数值、真实数据和生产操作仍按明确条件处理。

- 新增 Accepted ADR-0007（有权块级引用核验，不扩大整文件/管理权）和 ADR-0008（索引身份、新索引准备/验证/切换、旧索引回退）。
- ADR-0001/0002 链接上述决定；ADR-0005 第9节登记通用预算、来源、外发/费用制定方法、质量门槛制定方法和摘要生命周期。
- 推进清单第9节记录用户原话、日期、批准人和保留条件；旧对齐/设计/拆分/整改映射文档增加历史时点与最新决定入口。
- tasks.yaml 调整14张既有卡的执行条件，补充027的tenant/uploader核验验收与Evaluation场景；未新增卡，未改变任何状态、依赖、allowed_paths、risk或nightly准入。
- AGENTS、迭代指导、治理规范、PRD和As-Is矩阵同步决定入口。修复已核实漂移：ADR索引正式目标应为Milvus而当前默认Local；PRAG-002为backlog/Deferred；sources已有document_id/chunk_id；控制面详情权限与检索权限不同。

## 文件与任务映射

| 交付 | 文件 | 相关卡 |
|---|---|---|
| 核验权限 | docs/adr/0007-rag-source-verification-permission.md；ADR-0001补记 | 027、026 |
| 索引治理 | docs/adr/0008-embedding-index-identity-and-switching.md；ADR-0002补记 | 032、015、036、019 |
| 预算/来源/摘要/评测条件 | docs/adr/0005-rag-adaptive-chunking-context-budget.md 第9节 | 028、029、030、031、033、035、037、038、040及017 |
| 批准追溯与旧文档对齐 | docs/plans/plan_rag_remaining_order_20261006.md；design_rag_adaptive_context_20261005.md；plan_rag_multi_agent_alignment_20261005.md；plan_rag_remediation_cards_20261005.md；plan_rag_review_remediation_20261005.md | 现有RAG/PRAG范围 |
| 规则和产品入口 | AGENTS.md；docs/AI辅助开发迭代指导.md；docs/governance/agent-harness-engineering.md；docs/product/项目产品需求方案.md；docs/product/as-is-capability-matrix.md；docs/adr/README.md | 防漂移 |
| 执行条件与验收 | tasks.yaml | 017、026～033（不含034）、035、037～040 |

## 验证命令与结果

使用 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe -c` 执行只读文档验证程序：PyYAML解析当前任务并与 `git show HEAD:tasks.yaml` 比较，检查ID唯一/依赖存在/无环，检查全部状态/依赖/scope/risk/nightly保持，检查批准文档的相对链接。

实际结果，退出码0：183卡；修改契约为RAG-017/026/027/028/029/030/031/032/033/035/037/038/039/040；所有任务状态、依赖、范围、风险和夜间准入保持；依赖无环；RAG/PRAG仍46卡（23done/23backlog）；首轮17份Markdown、42个相对链接、缺失0。新增本实现说明和评审后再核对链接，最终数量见评审记录。

最终新增实现说明/复核后，项目解释器执行全部改动Markdown本地链接/范围/ADR索引检查，退出0：19份Markdown、43个相对链接、缺失0；8份Accepted ADR；183条任务；改动仅AGENTS/tasks/docs。之后仅补本文证据文字与清单历史说明链接，最终链接数44，退出0。

`git diff --check` 实际执行退出0。Git提示既有文件LF将按仓库设置转CRLF，不是校验失败。`git status/diff`只读检查；未修改Git暂存区，未提交/推送/合并/部署。

## 当前业务事实与剩余风险

只读核查最新 `_process_job` 仍在新版摄取前提交旧版非current；`_persist_document`已有成功持久化时同事务降级其他current的路径。该发现供下一张RAG-024的失败复现与计划使用，本轮未改业务，也未将静态观察当作已通过的故障测试。

各业务卡仍backlog，核验入口、全部生成预算、索引治理及摘要并未因文档Accepted而完成。未运行业务pytest/Ruff/mypy或真实模型调用，没有新质量/性能结果。费用/发布门槛等具体数值尚未形成，不写成已获数值批准；重要数据重建、迁移、生产启用和新供应商/客户文档外发未授权。017、039的跨模块阻塞和018/PRAG-002/041进入条件继续保留。

## 回滚

按本次diff恢复文档补记和14张卡内文本，不回退已有业务实现或改动状态；保留用户批准的原始证据。此回滚不操作数据库、原文或索引。
