# 检索与 LLM 缓存独立代码审查

日期：2026-10-11；独立审查 Agent：`/root/cache_plan_review`。原始契约为 `tasks.yaml` RAG-019，实施计划 `docs/plans/plan_rag_019_cache_20261011.md`。本 Agent 未修改业务代码，独立编写 `tests/test_rag_cache_review.py`，使用实际 RAGService/LocalVectorStore 和独立 SQLite 库执行反例。

实际使用技能：[task-card-delivery](../ai-prompts/skills/task-card-delivery/SKILL.md)、[plan-eng-review](C:/Users/123/.agents/skills/gstack/plan-eng-review/SKILL.md)、[code-review-quality](C:/Users/123/.agents/skills/code-review-quality/SKILL.md)。代码审查按逻辑、权限、失败、可测试性和性能分段读取；按单卡技能要求不凑发现数量，不采用通用 code-review-quality 的最低发现数量要求。

## 审查发现与修复计划

### P2：已 flush、未提交写事务继续复用提交态负缓存

位置：`app/services/cache_scope.py` 的 `AuthorizedCacheScope.snapshot`。仅检查 `session.new/dirty/deleted` 不足以识别已经 flush 的未提交变更；flush 后三个集合均为空，新独立会话仍只能读旧提交态，缓存旧空结果继续命中。调用方已发布的事务内文档因此不可检索，违反计划的未提交事务绕过及数据变化失效约束。

实际领域入口反例：真实 `rag.ingest_text` 写入 draft 文档，重复 `RAGService.search` 获得并缓存空结果；将文档设为 current/published，`session.flush()`，再次 search 应调用实际 LocalVectorStore 并返回该文档，实际仍为 `[]`。

命令：`D:/DepTooL/anaconda3/envs/ai-assistant/python.exe -m pytest tests/test_rag_cache_review.py -q --basetemp data/pytest-tmp/rag019-review-red-1`。退出码 1，`1 failed in 4.21s`，失败于 `assert result and result[0].document_id == doc.id`。隔离合成 Embedding，仅证明事务/缓存业务行为，不证明检索质量。

修复计划：跟踪实际 Session 事务内已 flush/显式 DML 的写入，提交或最外层事务结束后才清除写标记；未提交写事务绕过缓存。至少补 ORM flush、显式 update、回滚恢复缓存的反例。原生 connection DML 是否受覆盖必须检查并披露，不能仅以 ORM 集合为空认定只读。

### 环境与测试准备记录

首次测试导入未带 `tests.` 包名造成 collection error，已修正，仅为测试准备错误；第二次默认 basetemp 清理遇到 Windows 权限错误，使用全新上述 basetemp 后成功执行断言。二者均不作为业务 RED 证据。

### P2：注入后端的实际参数/依赖未完整绑定

未知后端按对象 namespace 区分实例，但同一实例的可变检索参数未进入键；注册 Native 后端也可能使用与 RAGService 名义参数不同的实际 Embedding/Store/Tokenizer，对名义依赖计算缓存键不足以绑定真实调用。

两条独立领域反例：自定义 Native 子类 `enabled=True` 首次命中后改为 False，实际检索应为空，仍复用旧命中；显式注入 Native 后端绑定另一合成 Embedding 实例，首次命中后实际 provider 改为零向量，名义 provider 未改变，仍返回旧正相似度命中，且没有第二次实际向量调用。两条均通过 RAGService.search 进入。

实际命令：`D:/DepTooL/anaconda3/envs/ai-assistant/python.exe -m pytest tests/test_rag_cache_review.py -q -k 'opaque or mismatch' --basetemp data/pytest-tmp/rag019-review-opaque-red-2`，退出码 1，`2 failed, 5 deselected in 3.93s`，均失败在期望 `[]` 的业务断言。

修复计划：未知/不透明后端不参与缓存；注册后端的真实 `_embedding/_store/_tokenizer` 必须与用于键和鉴权的 Service 注入依赖一致，否则回到原检索路径。保持正常注册生产后端热点缓存的验收路径。此为实际身份绑定缺陷，不扩大后端架构，也不以实现 namespace 代替配置/依赖一致性。

补记：最初第二条反例错误地假设零查询向量一定返回 `[]`。修复后的实跑证明现有 Local 查询仍返回相似度为 0 的候选。因此正式回归改为比较实际调用次数和相似度：首轮 >0，变更后重新调用且为 0。最初 `[]` 断言失败单独不能证明缓存缺陷；旧缓存的调用次数 1 和旧正相似度、修复后调用次数 2 与零相似度才是有效业务判据。首轮修复复跑曾出现 `6 passed, 1 failed`，该失败为上述测试期望错误，无业务代码新缺陷。

## 修复复核与实际运行证据

F1 修复：Engine 的 before_execute 对非结构化 SELECT 保守标记事务写，commit/rollback 清除最外层连接标记；原生 SQLite 写额外检查 DBAPI in_transaction。F2 修复：只允许注册 Backend 且其实际三个依赖与 Service 用于键和校验的依赖对象完全相同，不透明自定义后端保留原调用路径。两项均由主 Agent 实现，本 Agent只新增回归与审查文档。

| 命令（均使用规定解释器 python -m） | 退出码 | 实际结果与范围 |
|---|---:|---|
| pytest tests/test_rag_cache_review.py tests/test_rag_019_acceptance.py tests/test_rag_cache.py -q --basetemp data/pytest-tmp/rag019-review-green-1 | 0 | 39 passed，2 条污染向量 numpy warning，35.52s；该次发生于 F2 修复前 |
| pytest tests/test_rag_cache_review.py -q --basetemp data/pytest-tmp/rag019-review-green-3 | 0 | 5 passed，5.04s；ORM flush / Session DML / Connection DML / nested rollback / SQLite driver DML |
| pytest tests/test_rag_cache_review.py -q --basetemp data/pytest-tmp/rag019-review-final-2 | 0 | 7 passed，7.93s；上述五条加 opaque backend 与实际依赖错配两条，F2 修复后 |
| ruff check --no-cache --fix tests/test_rag_cache_review.py | 0 | 1 项 import 排序修复，0 remaining；fixture alias 调整只消除 lint shadowing |

首次 raw driver 测试草稿误用 `documents`，真实表名为 `rag_documents`，纠正后反例通过；不将表名错误当业务失败。Ruff 默认缓存因 Windows 临时文件权限失败，改用 --no-cache 后完成检查，不改 lint 规则。最终 7 条运行后只对测试进行 fixture import alias 和 import 排序，无业务断言变化；最终全回归由主 Agent复跑该版本。

## 原始要求与审查范围

读完整 tracked diff（配置、Provider、RAGService、ChatService、README、ADR、tasks）及所有新增应用模块与缓存测试，检查 search / make_retriever / ChatService.chat / chat_stream / actual Provider / FallbackChain 的调用路径。

| 原始要求 | 审查与动态证据 | 结论 |
|---|---|---|
| 相同查询参数不重复向量调用 | 真实 LocalVectorStore 计数及 search/retriever 共享，独立 39 条运行 | E2 已通过 |
| 确定性 LLM 请求避免重复 HTTP 调用 | 默认 ChatService 实际隔离 HTTP 计数、非确定性不缓存、Fallback 单跳身份；独立 39 条运行 | E2 已通过，非外部账单证明 |
| 租户/有效授权域/数据库/数据/索引绑定，命中再授权 | 同租户 uploader、跨租户、另一数据库、角色/禁用改变、实际模型/endpoint/context/凭据/配置、索引身份变化 | E2 已通过 |
| 发布/删除/重解析/改权失效 | 实际生命周期及新 Session 改权；本审查另补事务内 flush/DML 和回滚 | E2 已通过 |
| 完整结果与失败重入 | HTTP503 重试、模型/finish不匹配、无 DONE、流式取消并再入、失败重解析旧读路径 | E2 已通过 |

默认关闭、进程内、有界 TTL/LRU/字节预算，不引入依赖/Redis、不改 ACL 或 Milvus 状态。未知自定义后端和依赖错配绕过缓存，已明确写入 README；没有将这些不透明扩展声称为完整缓存支持。

## 最终代码指纹

基线 HEAD `7709d7bdb67290726f3e66200fab642577f23704`，审查对应未提交工作区。SHA256：

| 文件 | SHA256 |
|---|---|
| app/core/response_cache.py | 6704CF0CB6574E737B8D92407481C9A165D4BDA07770EBDC623C9AE03E56588C |
| app/core/config.py | 340751501B54EAA145D79BF126A2D7019B545687EA0EFFF393240A4D5C9EFB1B |
| app/services/cache_scope.py | 7FECB7BF9E7919D8B28ECF4BCA2F43A624A8C6353BA26BC7033ABF330BA2FDB0 |
| app/rag/cache.py | C650E4DD4E359DA4FF02ED8CA0A2A485B2355725D31334660FD8BD507A3D5D48 |
| app/rag/service.py | 61A4654A5760734AF26E3AE8E2DF8C9EA866A13CA3EE1489620A254FDD667337 |
| app/llm/openai_compatible.py | 66629050C99297F373399C71C57DA32951B023E5AC4A3B09F272BF7EC8DE58C9 |
| app/services/chat_service.py | FB35C6AE1DF7F29548AFA1536936384D9F6CAAA117F80BA390C0C566CFADA1E3 |
| tests/test_rag_cache_review.py | 877ECC4B75B916F922017B91C7978C87B1F899C6863A95B6C520DBB9353D5B67 |

## 最终结论与限制

两项 P2 已修复并通过独立真实入口反例复核；在本次原卡范围及最终指纹内，未发现未修复 P1/P2。独立代码审查通过，不代表可以跳过整仓门禁或人工验收。仅本机隔离 SQLite/Local + 合成 Embedding/HTTP；未运行收费真实模型、Milvus/PostgreSQL生产缓存或多实例验证，不声明账单节约比例、质量提升或生产延迟指标。全租户快照和源码配置指纹仍有 SQL/IO 开销，README 已披露。

## 最终格式整理后的证据连续性

主 Agent 最终仅删除 `app/services/cache_scope.py` import 段与 `_WRITE_MARKER` 之间多余空行；本 Agent重新读取该文件全部逻辑，权限、事务写跟踪、快照、命中校验与作用域行为未变。其当前 SHA256 为 `1F31BD5CEABB5719CB90DA7C8C00023416AF258B6C9EC848D4774D9F932ED9F8`，替代上述历史指纹；测试文件仍为 `877ECC4B75B916F922017B91C7978C87B1F899C6863A95B6C520DBB9353D5B67`。

本 Agent实际读取最终日志 `data/rag019-cache-gate-final2.log`：`pytest -k cache -q --basetemp=data/pytest-tmp/rag019-cache-gate-final2` 为 **63 passed, 1652 deselected, 3 warnings in 55.01s**，包含已整理 import 的七条独立反例；退出码 0 由主执行者记录。三条 warning 分别为既有 sandbox AST 弃用及数值污染反例的 numpy invalid/overflow，无 skipped。`data/rag019-ruff-final.log` 为 All checks passed，`data/rag019-mypy-final.log` 为 Success: no issues found in 194 source files，均由主 Agent执行并记录退出码 0。未并行重跑数据库测试，避免干扰后续回归。

证据连续性成立，独立审查结论仍为通过、无未修复 P1/P2；人工验收及其他回归门禁由交付对账继续记录。

## F3：异步期间实际 HTTP 请求与缓存键的上下文错配（P2，已修复）

后续独立验证 Agent 从实际 Provider 入口构造反例：`AsyncClient.__aenter__` 等待期间修改传入 messages、options，以及 Provider 的 model/base_url/api_key。旧实现先根据旧值构造键，再在 await 之后使用新值发送请求；结束时又核对可变的当前 model，导致对 `mutated-context`、temperature=0.7、mutated-model 的响应重放到恢复为 `original-context`、temperature=0、acceptance-model 的请求上。`data/rag019-independent-request-red2.log` 两条 chat/stream 均失败，实际重放文本为 `mutated-context|0.7|mutated-model`，2 failed / 34 deselected。

本 Agent独立复核旧/新调用顺序及反例，最终等级采用 **P2**：证据确认了上下文、确定性条件及模型身份绑定错误；未证明跨租户越权或外部敏感数据泄漏，不将其升级表述为已发生外部越权。该缺陷仍必须在关闭前修复。

修复后的 Provider 在首次 await 前深拷贝 options 与 payload，冻结 headers、url、timeout；HTTP 与键使用同一快照，完成判定和流式每块模型身份核对使用 payload 中的请求模型。预算检查仍执行；授权/数据快照仍复核。此修复没有改变权限范围、模型供应商或默认缓存开关。本 Agent完整读取最终 Provider 和两个新测试，未修改业务或测试。

独立实际复验命令：`D:/DepTooL/anaconda3/envs/ai-assistant/python.exe -m pytest tests/test_rag_019_acceptance.py -q -k mutable_request_during_transport --basetemp data/pytest-tmp/rag019-review-frozen-1`，退出码 **0**，**2 passed, 34 deselected in 4.39s**，覆盖 chat 和 stream；真实 localhost HTTP 回显实际 payload，隔离 SQLite 授权作用域，证据类别 E2。另读取验证 Agent 的 `data/rag019-independent-frozen-final.log`：36 passed，2 数值污染 warnings，38.22s；mypy 最终日志 194 source files 通过。最终全缓存门禁仍由主 Agent执行，本审查不将此前 63 条结果冒充新增 F3 后的全门禁结果。

替代上述 Provider 历史指纹：`app/llm/openai_compatible.py` SHA256 **422BBD6C965264FB5EF275F83431E99DBBE8E35C79E4D1CBE041CECA1E20F3AC**；新增反例后的 `tests/test_rag_019_acceptance.py` SHA256 **81584F16EA2F4198F934FBD101C261CB0E6A7AFBDEC6A5F37F3FE454A8B51642**；`app/services/cache_scope.py` 仍为 **1F31BD5CEABB5719CB90DA7C8C00023416AF258B6C9EC848D4774D9F932ED9F8**。其余本审查记录的应用代码指纹未因 F3 改变。

**最终结论：F1、F2、F3 三项 P2 均修复并经独立真实入口复验；最终审查通过，无未修复 P1/P2。** 人工业务验收及最终全门禁记录保留原要求，不自动关闭任务。
