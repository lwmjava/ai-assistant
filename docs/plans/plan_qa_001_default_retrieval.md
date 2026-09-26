# QA-001：对话默认检索本租户当前版

> 状态：已实现（`tasks.yaml` QA-001 `done`）
> 来源：交付排期 B3；`docs/plans/plan_remaining_delivery.md` 的 QA-001
> 日期：2026-09-26
> 截止：2026-11-13
> 依赖：TEN-003 已完成

成员提问时，默认把本租户已发布的当前版文档检索进对话。另一租户的文档、已软删除的文档、不是当前版的文档不进入上下文。

## 目标

1. 未在环境里显式关闭时，对话会构建检索器并注入检索结果。
2. 注入内容只来自提问者所属租户、未删除、且为当前版的文档。
3. 显式关闭 `RAG_ENABLED` 时，对话仍不检索。

## 现状

- `RAG_ENABLED` 的代码默认值是 `false`。`ChatService._build_retriever` 在关闭时返回 `None`，管线不调用检索。
- 运行中的配置会读取项目根目录 `.env`。本地 `.env` 里如果写了 `false`，改代码默认值不会改变这台机器的现有进程。`.env` 不在本任务修改范围内。
- Local 检索已经按 `tenant_id` 过滤，并排除 `deleted_at` 有值的文档。`RAG_EFFECTIVE_DATE_FILTER` 默认关闭时，还会加上 `is_current`。
- 已有测试覆盖开关本身，以及打开开关后主链能跑完。它们没有把“默认开启”和“跨租户 / 已删除 / 非当前版不注入”绑在本任务上。
- 没有 Embedding 密钥时，开发环境会降为 Mock，只能证明链路。生产环境会在取嵌入时抛错，检索异常会中断这次对话。这是现在打开检索之后的行为。

## 方案

1. 把 `app/core/config.py` 里 `RAG_ENABLED` 的默认值改为 `true`。不改检索算法、切分、Embedding、RRF，也不改 `RAG_EFFECTIVE_DATE_FILTER`。
2. 同步默认值说明：`.env.example`、`README.md` 的默认配置句和配置表、`AGENTS.md` 第 3.2 节、`docs/AI辅助开发迭代指导.md` 的关键配置、`docs/product/as-is-capability-matrix.md` 里“对话默认不注入 RAG”这一行。能力状态改为默认会注入；Mock 不能证明检索质量仍要写明。
3. 不改本地 `.env`。已经写成 `false` 的环境保持关闭。
4. 过滤继续用现有 Local 查询条件。新增测试直接走检索器或向量检索，检查上下文文本：
   - 本租户当前版的正文出现。
   - 另一租户的正文不出现。
   - 已软删除文档的正文不出现。
   - 同一租户里 `is_current` 为假的正文不出现。
5. 保留“关闭开关时 `_build_retriever` 返回 `None`”的现有测试。默认值断言读字段默认值，不读被 `.env` 覆盖后的全局 `settings`。
6. 生产环境缺少 `EMBEDDING_API_KEY` 时仍失败，不在本任务改成静默跳过检索。

## 非目标

不返回或展示来源字段，那是 QA-002、QA-003。不改知识库页面的上传、重建和删除，那是 QA-004。不改切分、Embedding、RRF 和独立重排。不把默认向量库改成 Milvus。不改写已冻结的检索基线。不修改开发者本机的 `.env`。

## 验收

- 字段默认值为开启；显式关闭时不构建检索器。
- 本租户当前版进入检索上下文。
- 另一租户、已软删除、非当前版不进入检索上下文。
- `pytest` 中与对话、检索相关的用例通过，`ruff check app/` 通过。
- 上述文档里的默认值与代码一致。

## 实现时允许改动的位置

`tasks.yaml` 里 QA-001 原允许路径不含配置说明。实现时还要改：

- `.env.example`
- `README.md`
- `docs/AI辅助开发迭代指导.md`
- `docs/product/as-is-capability-matrix.md`
- `docs/plans/plan_qa_001_default_retrieval.md`（实现后再补完成说明）

不改 `evals/reports/rag-v0.1-baseline-20260919.json`、生产数据和 `.env`。

## 验证

```powershell
pytest tests/test_chat.py tests/test_rag.py tests/eval/test_rag006_pipeline.py -v
ruff check app/
```

## 已完成的行为

未在环境里关掉检索时，代码默认会构建检索器。检索上下文只包含本租户、未删除、当前版的正文。显式关闭 `RAG_ENABLED` 时不构建检索器。本地 `.env` 没有改；里面若仍是 `false`，这台环境继续不检索。

生产环境没有 Embedding 密钥时，检索仍会失败并中断这次对话。开发环境没有密钥时仍降为 Mock，只能证明链路。

来源字段、知识库页面、切分、Embedding、RRF 和默认向量库都没有改。

## 代码位置

- 默认值：`app/core/config.py` 的 `RAG_ENABLED`。
- 检索入口未改：`app/services/chat_service.py` 的 `_build_retriever`。过滤仍在 `app/rag/vectorstore/local.py`。
- 测试：`tests/test_rag.py` 的 `test_rag_enabled_field_defaults_to_on`、`test_retriever_context_is_tenant_current_only`。关闭开关的原用例仍在。
- 说明：`.env.example`、`README.md`、`AGENTS.md`、`docs/AI辅助开发迭代指导.md`、`docs/product/as-is-capability-matrix.md`。

## 验证结果

```text
pytest tests/test_chat.py tests/test_rag.py tests/eval/test_rag006_pipeline.py -v
47 passed，退出码 0

ruff check app/core/config.py tests/test_rag.py
通过，退出码 0
```

`ruff check app/` 退出码 1，报告 94 个既有问题，不在本次改动的文件里。没有为了变绿去改那些文件。

## 没做的事

没有改来源展示、知识库页面、切分、Embedding、RRF、Milvus 默认值，也没有改本机 `.env` 和已冻结的检索基线。没有用真实 Embedding 或真实模型验证检索质量。
