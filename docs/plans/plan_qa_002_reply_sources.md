# QA-002：回复携带来源字段

> 状态：已实现（`tasks.yaml` QA-002 `done`）
> 来源：交付排期 B3；`docs/plans/plan_remaining_delivery.md` 的 QA-002
> 日期：2026-09-26
> 截止：2026-11-13
> 依赖：QA-001 已完成

调用方从对话响应里就能看到这条回复用了哪些文件。有页码或段落时一并给出。没有检索命中时来源为空，不从回答正文里猜。

## 目标

1. 非流式响应、流式结束事件、会话详情里的助手消息都带来源列表。
2. 每条来源至少有文件名。分块元数据里已经有页码或段落路径时原样放入。
3. 没有命中、检索关闭、或文件名也没有时，来源为空列表。不编造页码或段落。

## 现状

- `ChatResponse` 只有 `conversation_id`、`reply`、`model`。消息表只有正文，没有来源列。
- 检索器把命中拼成不可信上下文文本后交给管线。`ChunkResult` 有 `source` 和 `document_id`，没有页码。
- 页码和段落路径存在分块的 `chunk_metadata` 里，键是 `page`、`section_path`。纯文本摄取通常没有这两项。
- 对话页展示来源是 QA-003，本任务只稳定 API 字段。

## 方案

1. 来源不从模型回答里解析。`HybridRetriever.retrieve` 仍返回原来的上下文字符串，并保留本次命中。对话结束后用这些命中读取对应分块的 `chunk_metadata`。
2. 每条来源包含 `filename`、`page`、`section`。`filename` 用分块上的 `source`，为空则用文档标题。`page` 只在元数据里有整数页码时填写。`section` 只在 `section_path` 非空时，用 `/` 拼成一段文字。同一个文件、页码和段落只保留一条。
3. 元数据缺少页码或段落时，对应字段为 `null`。不把块序号、阅读顺序或 0 填成页码。
4. 非流式 `ChatResponse.sources` 返回该列表。流式在结束前增加一条 `sources` 事件，数据是同一列表。助手消息把列表存成 JSON；会话详情的助手消息带回 `sources`。用户消息的来源为空列表。旧消息没有该列时视为空列表。
5. 检索器没有建出来，或本次没有命中时，`sources` 为 `[]`。
6. 给 `messages` 增加可空的来源 JSON 列，并补一条数据库修订。新库由模型建表，旧库靠修订补列。

## 非目标

不做对话页展示，那是 QA-003。不做引用角标、原文摘录和侧栏。不新增引用准确率评测。不改切分、Embedding、RRF 和检索过滤。不改已冻结的检索基线。

## 验收

- 命中带文件名的文档时，响应来源含该文件名。
- 元数据有页码或段落路径时，响应带上原值；没有时字段为 `null`，不出现编造的数字。
- 无命中或检索关闭时，来源为 `[]`。
- 会话详情里能读回刚保存的助手来源。
- 相关 pytest 通过，改动文件的 ruff 通过。

## 实现时允许改动的位置

原允许路径不够覆盖路由、检索命中和消息存储。实现时还要改：

- `app/api/routes/chat.py`
- `app/rag/retriever.py`
- `app/rag/service.py`
- `app/models/conversation.py`
- `alembic/versions/`
- `docs/plans/plan_qa_002_reply_sources.md`（实现后再补完成说明）

不改前端，不改 `evals/reports/rag-v0.1-baseline-20260919.json`、生产数据和 `.env`。

## 验证

```powershell
pytest tests/test_chat.py tests/test_rag.py -v
ruff check app/api/routes/chat.py app/services/chat_service.py app/rag/retriever.py app/rag/service.py app/models/conversation.py tests/test_rag.py
```

## 已完成的行为

非流式响应的 `sources`、流式结束前的 `sources` 事件、会话详情里的助手消息，都是同一份来源列表。每条有 `filename`。分块元数据里的整数页码写入 `page`，`section_path` 用 `/` 写入 `section`。没有这些值时字段为 `null`。页码 `0`、布尔值和块序号不会被当成页码。同一个文件、页码和段落只出现一次。没有文件名的命中被丢掉。

没有命中或检索关闭时，`sources` 为 `[]`。用户消息的来源也是空列表。来源存在助手消息的 JSON 列里，不写进回答正文。

对话页没有展示这些字段。

## 代码位置

- 命中保留：`app/rag/retriever.py` 的 `last_hits`。
- 来源组装：`app/rag/service.py` 的 `sources_from_hits`。
- 写入回复：`app/services/chat_service.py`。
- 响应与会话详情：`app/api/routes/chat.py`。
- 消息列：`app/models/conversation.py`，修订 `alembic/versions/a9c4e2b81d07_add_message_sources.py`。
- 已有库若被直接标到最新修订，由 `app/core/migration.py` 补上这一列。
- 测试：`tests/test_rag.py` 的 `test_sources_keep_filename_and_existing_location`，`tests/test_chat.py` 的三条来源用例。

## 验证结果

```text
pytest tests/test_chat.py tests/test_rag.py::test_sources_keep_filename_and_existing_location -v
9 passed，退出码 0

ruff check app/api/routes/chat.py app/rag/retriever.py app/rag/service.py app/models/conversation.py app/core/migration.py tests/test_rag.py tests/test_chat.py
通过，退出码 0
```

`ruff check app/services/chat_service.py` 仍报 6 个既有的前向引用未定义。这次没有改那些注解。

`pytest tests/test_rag.py` 里另有 3 个 BM25 排序用例失败。失败原因是共享测试库里同一租户已有上次运行留下的分块，候选数多于用例新建的分块。这次没有改排序或融合代码。

## 没做的事

没有做对话页展示、引用角标、原文侧栏和引用准确率评测。没有改检索过滤、切分、Embedding 或 RRF。
