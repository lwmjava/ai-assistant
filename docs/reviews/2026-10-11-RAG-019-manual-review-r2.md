# RAG-019 第二轮独立人工复审（R2）

- 任务：RAG-019「LLM 响应与检索结果缓存（P2）」
- 复审身份：第二轮独立人工复审 Agent；全程只读——未修改任何业务实现、测试、docs/plans/、tasks.yaml、docs/reviews/ 既有文件；未做任何 git 写入（无 commit/checkout/reset/stash）；本次仅新增本评审文件。
- 复审日期：2026-10-11（Asia/Shanghai）
- 复审对象：分支 `codex/rag-019-cache`，HEAD `65931e3`「feat(cache): 增加默认关闭的检索与 LLM 响应进程内缓存」，基线 `7709d7b`。
- 独立性声明：本轮不以上一轮 manual-review 结论为证据盖章；下列每一条结论均有本轮独立核验证据。上一轮文档仅作交叉核对。

---

## 一、本轮独立审查范围与时间线

1. 只读入口核对：`tasks.yaml` RAG-019 条目（9046–9107 行）、`docs/plans/plan_rag_019_cache_20261011.md`、`docs/plans/implementation_rag_019_cache_20261011.md`、`docs/evaluations/rag019-cache-safety-v1.md`。
2. 代码实读：`app/core/response_cache.py`（全文 149 行）、`app/services/cache_scope.py`（全文 159 行）、`app/rag/cache.py`（全文 151 行）、`app/llm/openai_compatible.py` 缓存段（90–228 行）、`app/services/chat_service.py` 装饰器接线（283/344 行）、`app/rag/service.py` 装饰器接线（273–302 行）、`app/core/config.py` 配置项（117–136 行）。
3. 测试实读：`tests/test_rag_cache_review.py`（全文 115 行）、`tests/test_rag_019_acceptance.py` 中 F3 用例（532–578 行）及关键用例清单。
4. 证据实算：11 个文件 SHA256 全表比对；pytest（验收 + 全缓存门禁）、ruff、mypy 实际执行并记录退出码。
5. 环境核对：git HEAD/分支/工作区状态；提交 65931e3 文件清单与行数；生产库 mtime。

未做：未重跑 354 项相关回归全量、未重跑前置 110 依赖测试、未逐行阅读历史日志——如实列入三分类。

## 二、环境与提交核对（已查证）

| 核对项 | 本轮实际结果 |
|---|---|
| `git rev-parse HEAD` | `65931e3eb2849535b51430ed60a082d2cc54ecae` |
| `git branch --show-current` | `codex/rag-019-cache` |
| `git status --short` | 空（工作区干净，已提交版本即审查对象） |
| `git show --stat 65931e3` | 23 文件，+2088/-26，与任务书一致；无 requirements/pyproject 依赖文件变更（非目标「不引入 Redis」成立） |
| 生产库 `data/ai_assistant.db` mtime | 2026-10-07 17:55:00，本轮测试运行期间未被触碰（测试经 `DATABASE_URL` 指向隔离临时库） |
| 解释器 | `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`，Python 3.12.0，确认可用 |

测试隔离：本轮全部 pytest 显式覆盖 `DATABASE_URL=sqlite:///./data/pytest-tmp/r2review-db/test_ai_assistant.db`、`RAG_BACKEND=native`、`EMBEDDING_PROVIDER=mock`、`AGENT_ORCHESTRATION=self`，并使用独立 `--basetemp`。临时目录 `data/pytest-tmp/r2review-*`、`data/mypy-r2review` 为本轮新建。

## 三、SHA256 全表比对（已查证）

用 PowerShell `Get-FileHash -Algorithm SHA256` 实算当前工作区 11 个指纹文件，与实现说明第 95–105 行指纹表逐条对比：

| 文件 | 实算 SHA256（小写前缀） | 指纹表 | 比对 |
|---|---|---|---|
| app/core/config.py | `34075150…c9efb1b` | `34075150…c9efb1b` | 一致 |
| app/core/response_cache.py | `6704cf0c…65888c` | `6704cf0c…65888c` | 一致 |
| app/services/cache_scope.py | `1f31bd5c…2ed9f8` | `1f31bd5c…2ed9f8` | 一致 |
| app/rag/cache.py | `c650e4dd…a3d5d48` | `c650e4dd…a3d5d48` | 一致 |
| app/rag/service.py | `61a4654a…673337` | `61a4654a…673337` | 一致 |
| app/llm/openai_compatible.py | `422bbd6c…20f3ac` | `422bbd6c…20f3ac` | 一致 |
| app/services/chat_service.py | `fb35c6ae…da1e3` | `fb35c6ae…da1e3` | 一致 |
| tests/test_rag_019_acceptance.py | `81584f16…51642` | `81584f16…51642` | 一致 |
| tests/test_rag_cache_review.py | `877ecc4b…53d5b67` | `877ecc4b…53d5b67` | 一致 |
| tests/test_rag_cache.py | `dde6a4ee…32dedb9` | `dde6a4ee…32dedb9` | 一致 |
| tests/test_response_cache.py | `04f22d6b…bacdc2` | `04f22d6b…bacdc2` | 一致 |

**结果：11/11 一致，0 不一致。** 审查对象确实是实现说明所描述的已提交版本。

## 四、实际执行命令与退出码（已查证）

| 命令（项目解释器，隔离环境变量） | 实际结果 | 退出码 |
|---|---|---|
| `python -m pytest tests/test_rag_019_acceptance.py -q --basetemp=data/pytest-tmp/r2review-basetemp` | **36 passed**, 2 warnings，45.52s | 0 |
| `python -m pytest -k cache -q --basetemp=data/pytest-tmp/r2gate-basetemp` | **65 passed / 1652 deselected**, 3 warnings，72.11s | 0 |
| `python -m ruff check --no-cache .` | `All checks passed!` | 0 |
| `python -m mypy app/ --cache-dir=data/mypy-r2review` | `Success: no issues found in 194 source files` | 0 |

Warning 分类（本轮独立确认）：
1. `app/agents/tools/sandbox/sandbox.py:93` `ast.NameConstant` DeprecationWarning——既有代码，与本卡无关。
2. `numeric_vector_change_never_hidden[vector1/vector2]` 两条 NumPy RuntimeWarning（除零/溢出）——该用例本就是「故意注入污染向量」的对抗反例，警告是被测行为本身，实现说明已如实披露，未将其写成质量提升。

环境性失败：无。ruff `--no-cache`、独立 `--basetemp`、独立 DATABASE_URL 均按任务书预案使用，未触发权限/缓存写阻断。

## 五、契约逐项核对（关闭对账表 8 行逐行结论）

| # | 原始要求（实现说明第 74–81 行） | 本轮独立核验 | 结论 |
|---|---|---|---|
| 1 | 检索缓存：相同查询+参数不重复向量调用 | 代码：`CachedRagBackend.retrieve` 键含 query/top_k/identity/index_id/backend/store/tokenizer/rrf_k/embedding options；命中走 `validate_hits` 而非原后端。验收 36 项含 `real_local_query_avoids_second_vector_call`，本轮实跑 36 passed。 | **成立（已查证）**；非生产延迟指标，实现说明已诚实标注 |
| 2 | 确定性 LLM 缓存省调用/不重复计费 | 代码：`_cache_request` 仅在 temperature 恰为 0、有限正数 timeout、有请求租约快照时建键；chat 仅在响应模型匹配、`finish_reason=stop`、无 tool_calls、非空 content 时写入。验收含 `default_chatservice_actual_http_count`。本轮 36 passed。 | **成立（口径受限，已查证）**：只证明 HTTP 调用次数不增；真实外部账单证据**未验证**，实现说明第 75 行已如实披露 |
| 3 | 键含租户/授权域/数据/索引版本、实际 context/model/Prompt 版本；命中再授权 | 代码实读确认：键 = digest(stamp, url, digest(headers), payload, opts, timeout, capability)，其中 stamp 来自 `AuthorizedCacheScope.snapshot`（绑定 user.id/tenant/role/token_version/read_scope/全量 documents+chunks+indexes 序列化 + 时间版本状态 + `configuration_stamp`（settings 全量 dump + 全仓 .py 源码摘要））；命中时 `validate_hits` 用全新 Session 逐块核对 chunk/doc/tenant/上传者范围/分数有限性/版本状态，且二次重查 `snapshot(query)==before` 与 `_identity_key` 一致后才释放。 | **成立（已查证）**；资源级 ACL 仍 Planned，未写成已实现 |
| 4 | 发布/删除/重解析/改权使缓存失效；跨租户/权限不复用 | 快照含全量 documents/chunks/indexes 的完整 model_dump，任何发布/删除/重解析/改权改变该行即改键；`_reader` 拒绝禁用/改角色/改 token_version/改 read_scope 的主体。验收含 `lifecycle_publish_reparse_delete`、`permissions_mutate_in_new_session` 等，本轮 36 passed。 | **成立（已查证）** |
| 5 | 非目标：无 Redis、不对非确定性强缓存、不单凭 temperature=0 | 提交 23 文件清单无依赖文件变更；`_cache_request` 拒绝 temperature≠0（含 0.7）、max_tokens≤0、timeout 非法；配置 `LLM_RESPONSE_CACHE_ENABLED=false`、`RETRIEVAL_CACHE_ENABLED=false` 默认（config.py 117–118 行 + .env.example 66–67 行一致）；LLM 默认 temperature=0.7 未变。 | **成立（已查证）** |
| 6 | 失败/回滚/幂等边界 | 代码：HTTP 错误 `raise_for_status` 先于写入缓存；流须收到 `[DONE]` 且 `finish_reason=stop`、无模型漂移/工具调用、单条字节未超预算才写；`BoundedCache` 存取均 `copy.deepcopy` 防污染；F1 五项事务反例（orm_flush/session_dml/connection_dml/nested_rollback/sqlite_driver_dml）本轮随 65 门禁通过。 | **成立（已查证）** |
| 7 | 依赖当前 HEAD 复核与独立审查 | 本轮独立实跑：HEAD=65931e3 与指纹表 11/11 一致；pytest/ruff/mypy 全绿；F1/F2/F3 修复代码与测试本轮独立抽查（见第六节）。 | **成立（已查证）** |
| 8 | 项目人工业务验收 | 本轮复审即 R8 的独立执行。 | 见第七节结论 |

**8 行关闭对账表逐行复核：全部成立，未发现把 Planned/Mock/合成结果写成已实现的表述。**

## 六、F1/F2/F3 修复有效性独立复核（不引用旧文档盖章）

### F1：flush 后未提交写事务绕过提交态负缓存
- 代码证据（本轮实读 `app/services/cache_scope.py` 27–40、79–86 行）：SQLAlchemy `Engine before_execute` 事件——非结构化 SELECT（即 flush/DML 等写语句）在 `connection.info` 打 `_WRITE_MARKER`；commit/rollback 清除标记；快照时若连接带写标记，或 SQLite 驱动层 `in_transaction` 为真，直接返回 None（不缓存/不命中）。
- 测试证据（本轮实读 `tests/test_rag_cache_review.py` 24–68 行）：5 种写入路径参数化反例——空结果先入缓存后 flush/显式 DML/连接 DML/嵌套回滚/SQLite raw driver 写入，断言 store.calls==2（绕过缓存真查），外层 rollback 后恢复负缓存。本轮随 65 门禁通过。
- **判定：修复有效（已查证）。**

### F2：不透明后端/名义依赖与实际依赖不一致
- 代码证据（本轮实读 `app/rag/cache.py` 100–111 行）：仅 Native/LangChain/LlamaIndex 三类注册后端才走缓存；且强校验 `backend._embedding is self.embedding`、`backend._store is self.store`、`backend._tokenizer is self.tokenizer`（同一对象身份），任一不一致走原后端。
- 测试证据（本轮实读 review 测试 71–114 行）：自定义可变后端开关后不复放旧命中；名义 Embedding 与后端实际 Embedding 不一致时，实际置零向量后仍真查（calls==2、similarity==0）。本轮随 65 门禁通过。
- **判定：修复有效（已查证）。**

### F3：异步等待期间可变请求与缓存键错配
- 代码证据（本轮实读 `openai_compatible.py` 134–140、165–171 行）：chat/stream 均在创建 `httpx.AsyncClient` **之前**完成 `opts.model_copy(deep=True)`、`payload=copy.deepcopy(...)`、`headers=dict(...)` 冻结快照；缓存键与实际 HTTP 请求共用该冻结 payload/headers/url/timeout/模型。`_revalidate` 在命中读取与流式逐块时校验租约未变，开关/上下文变更即 `PermissionError` 拒绝而非混放。
- 测试证据（本轮实读 acceptance 532–578 行）：monkeypatch `AsyncClient.__aenter__`，在客户端进入后、请求发出前篡改 messages 内容、temperature、max_tokens、provider.model/base_url/api_key；chat/stream 参数化双路径。断言第二次（恢复原输入）返回原上下文响应，不得把首次篡改响应复放到原键下。本轮随 36/65 门禁通过。
- **判定：修复有效（已查证）。** 三方（含本轮）一致将其定级 P2：进程内可变对象错配，未证明跨租户外部数据泄漏。

## 七、三分类清单

### 7.1 已查证（本轮实际核验）
1. HEAD=65931e3、分支正确、工作区干净；提交 23 文件 +2088/-26 与任务书一致。
2. 11/11 文件 SHA256 与实现说明指纹表一致。
3. 独立 E2 验收 36 passed（exit 0）；缓存门禁 65 passed/1652 deselected（exit 0）。
4. `ruff check --no-cache .` exit 0；`mypy app/` 194 源文件 exit 0。
5. 两个缓存开关默认 false（config.py + .env.example 双处一致）；无新依赖引入。
6. 键绑定（授权域/数据库/索引身份/内容指纹/配置/Prompt 版本）、命中再授权、返回副本、未提交写事务绕过、chat/stream 分键（payload 含 `stream` 标志天然分键）、TTL/LRU/条数/字节上限——均在代码中实读确认且有测试承载。
7. F1/F2/F3 修复代码与对应测试本轮独立抽查有效。
8. 生产库未被本轮测试触碰。

### 7.2 一方称（实施说明/旧审查自述，本轮未独立执行）
1. 前置 HEAD 依赖 110 passed（其中 `test_rag_032_rebuild_cli.py`、`test_rag_032_remote_rebuild.py` 本轮未运行）。
2. 354 项相关回归全量结果（本轮仅重跑了缓存相关 65 项与 36 验收）。
3. F1/F2/F3 「先 RED 后修复」的历史过程数值——本轮只复现修复后 GREEN。
4. `git diff --check` 空白检查、YAML 解析与 184 卡 ID 合法性——本轮未执行。
5. 历史门禁日志 `data/rag019-*.log` 逐行阅读——本轮以重新运行代替。

### 7.3 未验证（实施说明自列未执行项，如实保留，不得写成完成）
1. 生产启用（两开关默认 false，未上生产）。
2. 真实 Milvus 缓存验证（Milvus 仍 Partial，默认向量库 local）。
3. PostgreSQL 生产缓存。
4. 真实付费/外部模型调用与真实账单。
5. 多实例/外部缓存/并发请求合并协调（明确 non-goal）。
6. 端到端延迟/性能达标指标。
7. 资源级 ACL（Planned，非本卡范围）。
8. 前端页面验收（本卡无前端变更，不适用）。

## 八、缺陷与风险清单

本轮独立复审**未发现新的 P1/P2 缺陷**。以下为低风险观察项（不阻塞）：

| # | 级别 | 观察 | 证据 |
|---|---|---|---|
| O1 | 低 | `configuration_stamp()` 首次或源码 mtime 变化后遍历全仓 .py 计算摘要，为进程内一次性开销；高频多进程重启场景有可测量成本，但无正确性问题 | response_cache.py 44–53 行 |
| O2 | 低 | 缓存为进程内、默认 TTL 60s；多实例部署下各实例缓存独立，一致性靠 TTL+键内数据指纹兜底，不承诺外部协调——实施说明第 87 行已声明 | plan 第 24 行 non-goal |
| O3 | 低 | 过程性证据缺口（见 7.2）：RED 历史数值、354 回归、历史日志逐行读本轮未独立复现；当前 GREEN 状态已由本轮实跑覆盖 | 第四节 |

## 九、明确结论

**RAG-019 在本卡契约字面范围内满足要求。**

依据（均为本轮独立证据）：
- 关闭对账表 8 行逐行成立；键绑定、命中再授权、生命周期失效、失败/回滚边界、默认关闭、有界 TTL/LRU/条数/字节、无 Redis、非确定性不缓存——代码实读 + 65 项缓存门禁实跑通过。
- F1/F2/F3 修复本轮独立抽查代码与测试均有效。
- 11/11 指纹一致；pytest 36/65、ruff、mypy 全部 exit 0；未发现把 Planned/Mock/合成结果写成已实现。

**状态变更建议：与上一轮人工审查一致，建议 `in_review` → `done`（自动契约验收层面），但最终决定权在用户。**

残余风险与阻塞项：
- 无阻塞性缺陷；残余项均为 7.3 节明确 non-goal 的生产侧未验证项（生产启用、真实 Milvus/PostgreSQL、多实例、付费账单、性能、资源 ACL），须在后续 NFR/OPS/PRAG 族任务卡中另行验证后方可上生产。
- 「转 done」仅代表本卡契约范围内自动验收与本轮独立复审通过，不代表生产就绪。

**本评审不做任何状态变更**：未修改 tasks.yaml、未做任何 git 写入。RAG-019 是否从 in_review 转入 done，由用户最终决定。
