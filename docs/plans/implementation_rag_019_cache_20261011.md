# 检索与生成响应缓存实现说明

任务：RAG-019；分支 codex/rag-019-cache；实施前 HEAD 7709d7b。原始契约及实施方案见 `plan_rag_019_cache_20261011.md`。README 的额外修改由用户 2026-10-11 明确授权。

## 实际完成的功能

已持久化且仍有效的认证主体可以在显式开启开关后复用相同检索结果；相同查询/参数与有效授权域、数据、数据库、后端、索引身份完全匹配时，不重复调用向量库。搜索和对话 Retriever 共用缓存。每次命中重新检查 SQL 授权、分块归属/内容/可见状态和实际索引；授权回查失败不释放缓存。发布、删除、重解析、改权、索引切换和日期生效状态变化使旧键失效。返回副本避免调用者修改污染缓存。

认证 ChatService 请求可复用相同且可安全确认的确定性 OpenAI 兼容模型文本响应。键含实际 messages/context、选项、实际端点/模型/凭据摘要、配置与应用/Prompt源版本，以及最新授权/数据版本。temperature=0 之外，还必须明确开启缓存、有有效请求租约、实际响应模型匹配、正常 stop 完成且没有工具调用。错误、空/截断结果、取消流或缺少 DONE 不写缓存。逐跳缓存不改变兜底模型归属；原预算和输出安全检查继续执行。流式每次重放再授权；生成器关闭时清理租约，后台任务不能沿用已关闭租约。

两种缓存独立受 TTL/LRU、条数、序列化负载总字节及单条字节预算限制。配置非法时启动校验拒绝；运行时非法值不能重放旧缓存。默认两个开关均为 false，LLM 默认 temperature=0.7 未改变。

未提交写事务不使用提交态缓存，包括 flush、Session/Connection显式DML、嵌套回滚仍含外层写及 SQLite raw-driver写入。未登记自定义后端或实际 Embedding/Store/Tokenizer 与 Service 名义依赖不一致时走原后端；自定义 Embedding 状态变化可导致保守不命中。

## 代码与任务映射

| 文件 | 作用 |
|---|---|
| app/core/response_cache.py | 有界 TTL/LRU、副本、配置/代码版本摘要、请求租约 |
| app/services/cache_scope.py | 新 Session 授权/数据快照、时间可见状态、事务写入检测、ChatService作用域 |
| app/rag/cache.py、app/rag/service.py | 实际后端装饰器及搜索/对话检索接线 |
| app/llm/openai_compatible.py、app/services/chat_service.py | 实际 Provider单跳缓存、完整流及作用域接线 |
| app/core/config.py、.env.example、README.md | 默认关闭配置、限制、回滚与边界说明 |
| docs/adr/0008-embedding-index-identity-and-switching.md | 既有已批准身份约束的实现补记，不新增架构决策 |
| tests/test_rag_019_acceptance.py | 独立 E2：真实本地 SQL/向量库/HTTP/ChatService |
| tests/test_rag_cache.py、tests/test_rag_cache_review.py | 检索/时间/关闭及独立入口审查回归 |
| tests/test_response_cache.py | E3 数值配置、内存/TTL/LRU与租约边界 |
| docs/evaluations/rag019-cache-safety-v1.md | Smoke/Adversarial/Observed Regression版本与判定 |
| docs/reviews/2026-10-11-RAG-019-*.md、tasks.yaml | 独立计划/验证/审查、缺陷修复与任务状态 |

## 实际分工与审查

主 Agent /root 实现公共缓存、授权快照、LLM/ChatService、配置与文档；/root/cache_retrieval 实现检索装饰器与接线；/root/cache_verification 独立从原始契约构造36项 E2 验收；/root/cache_plan_review 独立计划与最终代码审查，执行领域入口反例，未参与业务实现。实际使用任务交付技能及其规定的 brainstorming、plan-eng-review、code-review-quality；具体阅读与适用维度见审查报告。

独立审查发现 F1（flush后脏事务识别遗漏）与 F2（不透明/依赖不一致后端身份键不足）及 F3（异步可变请求与缓存键错配），缺陷、先失败后修复顺序及复验见 `docs/reviews/2026-10-11-RAG-019-review-fixes.md` 和代码审查。最终证据与关闭对账在后文更新，不能用修复前报告代替最终代码证据。

## 验证命令与证据

均用项目解释器 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`。测试库为临时独立 SQLite 或项目测试库，不改生产数据。

- 前置当前HEAD验证：`-m pytest tests/test_rag_026_uploader_scope.py tests/test_rag_032_index_identity.py tests/test_rag_032_rebuild_cli.py tests/test_rag_032_remote_rebuild.py -q --basetemp=data/pytest-tmp/rag019-deps-20261011`，110 passed，exit0。
- 有效 RED：相同查询实际向量调用2而非1；默认五阶段重复对话HTTP从6增至12而非保持6；独立flush反例返回旧空结果。实际命令及修复后指纹见独立报告。
- 独立业务验收最终阶段：`-m pytest tests/test_rag_019_acceptance.py -q --basetemp=data/pytest-tmp/rag019-independent-final`，34 passed，29.12s，exit0（F2保守准入追加前；最终受影响范围随后统一重验）。
- 原语/检索/事务修复回归阶段：`-m pytest tests/test_rag_cache_review.py tests/test_response_cache.py tests/test_rag_cache.py -q --basetemp=data/pytest-tmp/rag019-review-fix-1`，25 passed，exit0（此为阶段报告，不冒充最终全部入口覆盖）。

Windows默认 Ruff 缓存写入和默认 pytest 临时目录清理曾权限失败，采用 --no-cache 与独立 basetemp 重跑；这类环境失败不当业务RED。显式污染向量的 NumPy 警告属于反例，未将其结果写成质量提升。

F3修复前缓存门禁：`-m pytest -k cache -q --basetemp=data/pytest-tmp/rag019-cache-gate-final2`，**63 passed / 1652 deselected，55.01s，exit0**，包含独立34业务验收、7入口审查反例、2检索集成和20原语边界。3条warning为既有AST弃用和显式污染向量的NumPy告警；没有skip缓存验收。该选择没有执行1652项无关测试，不将其声称全仓pytest通过。

F3修复前静态检查：`-m ruff check --no-cache .` exit0；`-m mypy app/ --cache-dir=data/mypy-rag019` exit0，194个源文件，报告位于 data/rag019-ruff-final.log / rag019-mypy-final.log。只调整import空行排序，未放宽配置、未忽略业务错误。独立代码审查追加核对了最终指纹连续性。

相关回归首轮执行17文件：353 passed / 1 failed，exit1。唯一失败 `test_factory_defaults_native` 读到本机 LangChain 配置；`app/rag/backend/factory.py` 没有本次diff，故可隔离为本机配置而非缓存实现。显式 `RAG_BACKEND=native`、`EMBEDDING_PROVIDER=mock`、`AGENT_ORCHESTRATION=self` 后该默认断言单独通过（1passed，exit0），随后保持同一项目解释器完整重跑相关回归，不改变断言或检查规则。结果于关闭对账记录补齐。

配置隔离后的第二轮351 passed / 3 failed（exit1）来自共享测试库遗留的重复上传记录：源文件路径仍指向上一轮临时目录，而本轮使用新的storage root。保留原测试库，不删除或迁移数据；以新的隔离测试目录中的 `test_ai_assistant.db`、固定 native/mock/self 重跑相同17文件，**354 passed，40.19s，exit0**。命令和完整日志为 `data/rag019-regression-fresh.log`；临时数据库 `sqlite:///./data/pytest-tmp/rag019-regression-fresh-db/test_ai_assistant.db`。这是测试数据隔离，不改变解释器、业务断言或门禁。

上述全量阶段后补充了 F3 异步请求快照缺口。独立真实HTTP反例chat/stream两项RED后，冻结实际options/payload/headers/url/timeout，键与HTTP发送及响应模型核验共用快照；独立完整36项E2重验38.22s、exit0（data/rag019-independent-frozen-final.log），独立审查再次复验两项4.39s、exit0。原34项和63项保留为历史阶段证据，最终门禁见下方追加。

## F3修复后的最终验证

- `-m pytest -k cache -q --basetemp=data/pytest-tmp/rag019-cache-gate-final3`：**65 passed / 1652 deselected / 3 warnings，46.53s，exit0**；日志data/rag019-cache-gate-final3.log。包含36独立E2、7入口审查、2检索集成与20原语；无skip。warning分类与历史门禁相同。
- `-m ruff check --no-cache .`：exit0；`-m mypy app/ --cache-dir=data/mypy-rag019`：194源文件通过、exit0（data/rag019-mypy-final3.log）；未放宽配置。
- 独立审查最终无未修复P1/P2，F1/F2/F3均有RED→修复→独立GREEN；F3未证明跨租户越权，等级P2，不能把进程内上下文错误写成已发生外部数据泄漏。

F3受影响回归：显式native/mock/self、隔离 `sqlite:///./data/pytest-tmp/rag019-llm-final-db/test_ai_assistant.db`，`-m pytest tests/test_llm.py tests/test_llm_route.py tests/test_chat.py tests/test_chat_controls.py -q --basetemp=data/pytest-tmp/rag019-llm-final-tmp`，**36 passed / 1既有ASTwarning，5.18s，exit0**，日志data/rag019-llm-final.log。

354项相关回归的精确测试选择：`-m pytest tests/test_rag.py tests/test_rag_import_jobs.py tests/test_rag_backend.py tests/test_chunking.py tests/test_rag_safe_parent_expansion.py tests/test_rag_search_explanation.py tests/test_rag_026_uploader_scope.py tests/test_rag_032_index_identity.py tests/test_rag_030_dialog_parent_assembly.py tests/test_rag_038_deadline_retry.py tests/test_rag_037_retrieval_status.py tests/test_rag_parent_mapping.py tests/test_rag_parent_expand_lock.py tests/test_llm.py tests/test_llm_route.py tests/test_chat.py tests/test_chat_controls.py -q --basetemp=data/pytest-tmp/rag019-regression-fresh-tmp`，环境隔离和阶段限制见前文。F3之后复验了受影响4文件，不冒充在最终Provider上重新执行全部354项。

YAML解析、184卡ID唯一性及状态合法性通过；11个最终源码/测试SHA256与关闭记录一致；`git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check` exit0（兼容仓库既有CRLF，未改文件换行规范）；范围只涉及RAG-019及用户授权README。分支codex/rag-019-cache，工作区改动未提交、未推送。

## 关闭对账记录（原始契约，不以实施方案替代）

| 原始要求 / 任务出处 | 输入、操作链与可观察结果 | 证据类别与位置 | 结果及缺口 |
|---|---|---|---|
| 检索缓存：相同查询+参数不重复向量调用 | 真实摄取→search两次、search/对话Retriever复用；实际向量调用1 | E2；独立acceptance、test_rag_cache，最终缓存门禁 | 通过；非生产延迟指标 |
| 确定性LLM缓存省调用/不重复计费 | 完整默认五阶段ChatService→真实隔离HTTP；第二新会话同消息，HTTP总数保持6 | E2；default_chatservice_actual_http_count、最终缓存门禁 | 省调用通过；无真实外部账单证据，不量化费用 |
| 租户/有效授权域/数据/索引版本、实际context/model/Prompt版本，命中再授权 | 新Session改权、跨租户/DB、模型/端点/凭据/context/config变化、索引不符、时间越界 | E2；独立验证报告、7入口反例、最终缓存门禁；代码含源版本摘要 | 通过；资源ACL仍Planned |
| 发布/删除/重解析/改权失效 | 真实生命周期修改→再次检索观察内容/空结果/拒绝 | E2；lifecycle_publish_reparse_delete、permissions_mutate_in_new_session、独立事务反例 | 通过 |
| 非目标：无Redis、无非确定性强缓存、不单凭temperature=0 | 配置默认关闭；实际模型/完整响应/请求作用域检查；温度与异常数值反例 | E2核心准入，E3有界原语；实际diff无依赖变化 | 通过 |
| 失败/回滚/幂等边界 | HTTP503/取消/无DONE不收录，实际重试；未提交flush/DML/rawDriver绕过，外层回滚恢复；TTL/关闭回原路径 | E2独立验收与review7；E3内存/数值/租约 | 通过 |
| 依赖当前HEAD复核与独立审查 | 前置110验证及指纹；独立原始契约构造，F1/F2/F3先反例后修复复验 | E2 + 独立review报告 | 无未修复P1/P2；最终门禁不代替人工判断 |
| 项目人工业务验收 | 用户仅调用技能，没有明确委托替代业务判断 | 未验证，原始AGENTS第9/13节与技能Constraints | 保留in_review，禁止done |

功能的自动验收已通过；关闭对账包含未验证人工项，未做生产启用、真实重要数据重建、付费模型或质量/性能裁决。不把这些未执行项写成完成。

## 未做、限制和回滚

不引入 Redis，不协调多实例或合并并发请求；不变更资源ACL Planned、默认local/Milvus Partial、Embedding/Chunking/Reranker、模型温度或生产配置。每次计算租户文档/分块指纹有SQL/哈希开销，没有整体性能达标声明。无付费模型、真实Milvus缓存或生产账单/质量测试；E2请求计数只证明省调用。前端未变更，无页面验收。关闭两个开关恢复原路径，重启清空缓存；可以回退本次文件改动，不需要数据迁移。

仅调用单卡交付技能没有替代人工业务验收授权，因此最终自动证据通过后任务保持 in_review，等待用户业务验收；不标done。未自行提交、推送、合并或部署。

## 最终验收代码/测试 SHA256

| 文件 | SHA256 |
|---|---|
| app/core/config.py | 340751501b54eaa145d79bf126a2d7019b545687ea0efff393240a4d5c9efb1b |
| app/core/response_cache.py | 6704cf0cb6574e737b8d92407481c9a165d4bda07770ebdc623c9ae03e56588c |
| app/services/cache_scope.py | 1f31bd5ceabb5719cb90da7c8c00023416af258b6c9ec848d4774d9f932ed9f8 |
| app/rag/cache.py | c650e4dd4e359da4ff02ed8ca0a2a485b2355725d31334660fd8bd507a3d5d48 |
| app/rag/service.py | 61a4654a5760734af26e3ae8e2df8c9ea866a13ca3ee1489620a254fdd667337 |
| app/llm/openai_compatible.py | 422bbd6c965264fb5ef275f83431e99dbbe8e35c79e4d1cbe041ceca1e20f3ac |
| app/services/chat_service.py | fb35c6ae1df7f29548afa1536936384d9f6caaa117f80ba390c0c566cfada1e3 |
| tests/test_rag_019_acceptance.py | 81584f16ea2f4198f934fbd101c261cb0e6a7afbdec6a5f37f3fe454a8b51642 |
| tests/test_rag_cache_review.py | 877ecc4b75b916f922017b91c7978c87b1f899c6863a95b6c520dbb9353d5b67 |
| tests/test_rag_cache.py | dde6a4eeadc3fe247085aeddecd868ae3ae6aefa63f4b6c2e830d282a32dedb9 |
| tests/test_response_cache.py | 04f22d6be32268b3e00217599ca79c496785d2cfddf99992f71d1b1b70bacdc2 |
